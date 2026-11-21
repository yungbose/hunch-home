"""The listening worker: a process of its own that holds the microphone, the speech detector and Parakeet, and
tells the app what it hears, one JSON line at a time on its standard output.

    python -m hunch_app.listen.worker --model <data>/speech/parakeet-tdt-0.6b-v2-int8 [--threads 2]

It is a separate process so that switching listening off gives every byte of its memory back and closes the
microphone for certain (the process ends), and so that with listening off none of the speech libraries is ever
loaded into the app. It ends when the app closes its standard input, on SIGTERM, or when the microphone ends. The microphone also
counts as ended when the sound system moves the recording to another source (`SourceWatch`).

The microphone is the default sound input (`parec`, Home Assistant's sound system): under Home Assistant the
input the owner chose in the app's Audio setting is the app's default, so no device is named here, and nothing
is loaded into the sound system or changed in it. In Docker, `HUNCH_MIC=alsa:<device>` reads an ALSA device
instead (`arecord`), as the Raspberry Pi's own listener does.

Lines it writes (no words are ever written anywhere else, and it logs nothing):

- `{"event": "ready", "load_s", "rss_mb"}` once the models are loaded and the microphone is open;
- `{"event": "hearing"}`, `{"event": "quiet", "speech"}`, `{"event": "heard", "words", "speech_end",
  "heard_at", "hear_ms"}` (`cutter.py`);
- `{"event": "problem", "reason": "memory" | "model" | "microphone", "detail"}`: it could not start or the
  microphone stopped. `detail` is a library's error name or the sound tool's own first line, never words heard.
"""

from __future__ import annotations

import os

# The speech detector's library works through OpenMP, whose idle threads spin by default: on a Pi that kept more
# than a whole core busy in a silent room. Told to sleep, the detector costs about 2% of a core. These must be set
# before the library is loaded. (ONNX Runtime's telemetry is switched off by the package's __init__.)
os.environ.setdefault("OMP_WAIT_POLICY", "passive")
os.environ.setdefault("GOMP_SPINCOUNT", "0")

import argparse
import contextlib
import json
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .cutter import FRAME, FRAME_S, RATE, Cutter

_OUT = threading.Lock()


def say(event: dict) -> None:
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with _OUT:
        try:
            sys.stdout.write(line)
            sys.stdout.flush()
        except (BrokenPipeError, ValueError, OSError):
            os._exit(0)   # the app has gone: nobody to tell


def rss_mb() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


def recorder_command(env: dict) -> list[str]:
    """The program that gives the microphone's sound as 16 kHz, 16-bit, one-channel raw samples."""
    chosen = (env.get("HUNCH_MIC") or "").strip()
    if chosen.startswith("alsa:"):
        return ["arecord", "-q", "-D", chosen[5:], "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw",
                "--period-size", "320", "--buffer-size", "6400"]
    return ["parec", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=20"]


# The recording moved: Home Assistant's sound system moves a recording to another source when the one it was on
# goes away, and the recorder never ends (seen in a real Home Assistant, 20 Nov 2026). Told as the microphone
# stopping, so the app retries as it does when the recorder ends.
MOVED = "the recording was moved to another sound source"
WATCH_EVERY = 3.0   # seconds between looks at which source the recording is on
_LIST = ["pactl", "list", "source-outputs"]


def own_source(text: str, pid: int) -> str | None:
    """The source the recording stream of process `pid` is on, from `pactl list source-outputs`; None when that
    stream is not listed."""
    for block in re.split(r"(?m)^Source Output #", text)[1:]:
        if re.search(rf'^\s*application\.process\.id = "{pid}"\s*$', block, re.MULTILINE):
            found = re.search(r"^\s*Source:\s*(\S+)", block, re.MULTILINE)
            return found.group(1) if found else None
    return None


def _list_streams(command: list[str]) -> str:
    return subprocess.run(command, capture_output=True, text=True, timeout=5, check=False,
                          stdin=subprocess.DEVNULL).stdout


class SourceWatch:
    """Which source the recorder's own stream is on, at the start and then every `every` seconds. It only lists
    the sound system's recording streams: it never loads a module or changes a default. With no sound system to
    ask (an ALSA microphone), or the stream not listed at the start, nothing is watched."""

    def __init__(self, pid: int, on_moved: Callable[[str], None], list_streams=_list_streams,
                 every: float = WATCH_EVERY) -> None:
        self.pid, self.on_moved, self.list_streams, self.every = pid, on_moved, list_streams, every
        self.source: str | None = None
        self.moved = False

    def _look(self) -> tuple[bool, str | None]:
        """(whether the sound system answered, the stream's source or None)."""
        try:
            text = self.list_streams(list(_LIST))
        except (OSError, subprocess.SubprocessError, ValueError):
            return False, None
        if not isinstance(text, str) or not text.strip():
            return False, None
        return True, own_source(text, self.pid)

    def start(self) -> bool:
        answered, source = self._look()
        self.source = source if answered else None
        return self.source is not None

    def check(self) -> bool:
        """Look once; True once the stream has moved or gone (told to `on_moved` the first time only)."""
        if self.moved or self.source is None:
            return self.moved
        answered, source = self._look()
        if answered and source != self.source:
            self.moved = True
            self.on_moved(MOVED)
        return self.moved

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(self.every):
            if self.check():
                return


class Mic:
    """The microphone as 32 ms frames, each with the clock time its last sample was heard.

    The time of a frame is counted from the microphone's own samples, not taken from when this program got round
    to reading them (a late read would make speech seem to end later than it did), tied to the clock by the
    least-delayed read of the last ten seconds, as on the Raspberry Pi."""

    def __init__(self, command: list[str], env: dict) -> None:
        self.samples = 0
        self.ahead: deque = deque(maxlen=round(10 / FRAME_S))
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL, env=env)
        self.ended_because: str | None = None   # why it was stopped from here (`moved_away`)

    def moved_away(self, reason: str) -> None:
        """The recording is no longer on the microphone it started on: stop the recorder, so its frames end."""
        self.ended_because = reason
        if self.process.poll() is None:
            self.process.terminate()

    def frames(self):
        import numpy as np

        step = FRAME * 2
        while True:
            raw = self.process.stdout.read(step)
            now = time.time()
            if len(raw) < step:
                return
            self.samples += FRAME
            counted = self.samples / RATE
            self.ahead.append(now - counted)
            yield np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0, counted + min(self.ahead)

    def why_ended(self) -> str:
        """The recorder's own first line about why it stopped (such as "Connection failure: ..."), or why it was
        stopped from here."""
        if self.ended_because:
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                self.process.wait(timeout=1)
            return self.ended_because
        try:
            self.process.wait(timeout=1)
            text = self.process.stderr.read(2000).decode(errors="replace")
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[0][:200] if lines else f"it ended (exit code {self.process.returncode})"

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()


class Parakeet:
    """Speech to words: Parakeet TDT 0.6B v2 (8-bit) through sherpa-onnx, greedy, as Home Assistant runs it."""

    def __init__(self, folder: Path, threads: int) -> None:
        import sherpa_onnx

        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            num_threads=threads, encoder=str(folder / "encoder.int8.onnx"), decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"), tokens=str(folder / "tokens.txt"), provider="cpu",
            model_type="nemo_transducer")

    def hear(self, samples) -> str:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(RATE, samples)
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hunch Home's listening worker")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    mic: Mic | None = None

    def end(*_args) -> None:
        if mic is not None:
            mic.close()
        os._exit(0)

    signal.signal(signal.SIGTERM, end)

    def watch_stdin() -> None:
        # The app closes this process's standard input to stop it, and a stopped app closes it too.
        try:
            while sys.stdin.buffer.read(1024):
                pass
        except (OSError, ValueError):
            pass
        end()

    threading.Thread(target=watch_stdin, name="hunch stdin", daemon=True).start()

    began = time.monotonic()
    try:
        import numpy as np
        from pysilero_vad import SileroVoiceActivityDetector

        detector = SileroVoiceActivityDetector()
        ear = Parakeet(args.model, args.threads)
        # One hearing of a faint hiss, so the first real one is not slow.
        ear.hear((np.random.default_rng(0).standard_normal(RATE) * 0.01).astype(np.float32))
    except MemoryError:
        say({"event": "problem", "reason": "memory", "detail": "MemoryError"})
        return 3
    except Exception as error:  # noqa: BLE001 - told to the app by name, without detail
        reason = "memory" if "alloc" in str(error).lower() else "model"
        say({"event": "problem", "reason": reason, "detail": type(error).__name__})
        return 3
    load_s = time.monotonic() - began

    env = dict(os.environ)
    try:
        mic = Mic(recorder_command(env), env)
    except OSError as error:
        say({"event": "problem", "reason": "microphone", "detail": f"{type(error).__name__}: the sound tool "
             "could not be started"})
        return 4
    cutter = Cutter(detector, ear, say, np.concatenate)
    frames = mic.frames()
    first = next(frames, None)
    if first is None:
        say({"event": "problem", "reason": "microphone", "detail": mic.why_ended()})
        mic.close()
        return 4
    say({"event": "ready", "load_s": round(load_s, 2), "rss_mb": rss_mb()})
    if not (env.get("HUNCH_MIC") or "").strip().startswith("alsa:"):
        watch = SourceWatch(mic.process.pid, mic.moved_away)
        if watch.start():   # the recording is listed: from now on, a move to another source ends it
            threading.Thread(target=watch.run, args=(threading.Event(),), name="hunch source watch",
                             daemon=True).start()
    cutter.feed(*first)
    for frame, now in frames:
        cutter.feed(frame, now)
    say({"event": "problem", "reason": "microphone", "detail": mic.why_ended()})
    mic.close()
    return 4


if __name__ == "__main__":
    sys.exit(main())
