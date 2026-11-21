"""A stand-in ear: listening with no microphone, speech detector or speech model, for the tests and the demo.

It behaves as `listen.ProcessEar` does from the app's side: the speech model is "here" or not, can be "fetched"
(with progress, and nothing downloaded), and `open()` gives a worker whose lines are the real worker's
(`ready`, `hearing`, `quiet`, `heard`, `problem`). A test makes it hear a sentence with `worker.say(words)`;
the demo plays `SCRIPT` in a loop with `play(every)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from .speech import FetchError

# What the demo's microphone "hears", in turn: commands it carries out, talk it ignores, and the ones listening
# never does (unlocking, opening a door).
SCRIPT = ("turn on the reading light", "i think we should have pasta for dinner tonight", "shut the blinds",
          "unlock the front door", "turn off the reading light", "the train was late again this morning")


class StandInWorker:
    def __init__(self, ear: StandInEar) -> None:
        self.ear = ear
        self.lines: asyncio.Queue = asyncio.Queue()
        self.returncode: int | None = None
        self.last_error = ""
        self.ended_by_kill = False
        self.closed = False
        self._player: asyncio.Task | None = None

    async def next(self) -> dict | None:
        line = await self.lines.get()
        if line is None:
            self.returncode = self.returncode if self.returncode is not None else 4
        return line

    def put(self, line: dict | None) -> None:
        self.lines.put_nowait(line)

    def say(self, words: str, seconds: float = 1.2) -> float:
        """Hear `words` as if spoken just now: `hearing`, `quiet`, then `heard`. Returns when the speech ended."""
        ended = time.time()
        self.put({"event": "hearing", "at": ended - seconds})
        self.put({"event": "quiet", "speech": True, "at": ended + 0.48, "speech_end": ended})
        self.put({"event": "heard", "words": words, "speech_end": ended, "heard_at": ended + 0.3,
                  "hear_ms": 300.0})
        return ended

    def noise(self) -> None:
        self.put({"event": "hearing", "at": time.time()})
        self.put({"event": "quiet", "speech": False, "at": time.time()})

    def play(self, every: float, script=SCRIPT) -> None:
        """The demo: a hearing every `every` seconds, each line of `script` in turn, for ever."""
        async def loop() -> None:
            n = 0
            while True:
                await asyncio.sleep(every)
                self.put({"event": "hearing", "at": time.time()})
                await asyncio.sleep(1.4)
                self.put({"event": "quiet", "speech": True, "at": time.time(), "speech_end": time.time()})
                await asyncio.sleep(0.35)
                self.put({"event": "heard", "words": script[n % len(script)], "speech_end": time.time() - 0.35,
                          "heard_at": time.time(), "hear_ms": 300.0})
                n += 1

        self._player = asyncio.get_running_loop().create_task(loop(), name="hunch stand-in microphone")

    def end(self, reason: str | None = None, killed: bool = False) -> None:
        """The worker ends by itself: with a problem line first when `reason` is given."""
        if reason:
            self.put({"event": "problem", "reason": reason, "detail": "stand-in"})
        self.returncode = -9 if killed else 4
        self.ended_by_kill = killed
        self.put(None)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._player is not None:
            self._player.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._player
        if self.returncode is None:
            self.returncode = 0
        self.ear.open_workers -= 1


class StandInEar:
    """`ready`: whether the speech model is "here". `fetch_steps` and `step_seconds`: how a fetch goes.
    `fetch_fails`: the plain sentence a fetch fails with. `open_fails`: the worker cannot be started.
    `starts`: the worker says it is ready at once (False: the test sends `ready` itself). `play_every`: the demo's
    script, one line every so many seconds once started."""

    def __init__(self, ready: bool = True, fetch_steps: int = 4, step_seconds: float = 0.0,
                 fetch_fails: str | None = None, open_fails: bool = False, starts: bool = True,
                 play_every: float = 0.0) -> None:
        self._ready = ready
        self.fetch_steps, self.step_seconds = fetch_steps, step_seconds
        self.fetch_fails, self.open_fails, self.starts = fetch_fails, open_fails, starts
        self.play_every = play_every
        self.fetches = 0
        self.opened = 0
        self.open_workers = 0
        self.worker: StandInWorker | None = None
        self.workers: list[StandInWorker] = []   # every worker opened, closed or not
        self.open_gate = asyncio.Event()          # a test clears it to hold `open` half way
        self.open_gate.set()
        self.stopping = False
        self.hold = asyncio.Event()   # a test clears it to hold a fetch half way
        self.hold.set()

    def ready(self) -> bool:
        return self._ready

    def describe(self) -> dict:
        return {"download_mb": 482, "disk_mb": 661, "needs_disk_mb": 1194}

    def stop_fetching(self) -> None:
        self.stopping = True

    async def fetch(self, session, progress) -> float:
        self.fetches += 1
        self.stopping = False
        total = 482_440_461
        for step in range(1, self.fetch_steps + 1):
            if step == self.fetch_steps // 2 + 1:
                await self.hold.wait()
            await asyncio.sleep(self.step_seconds)
            progress("fetching", total * step // self.fetch_steps, total)
        if self.fetch_fails:
            raise FetchError(self.fetch_fails)
        progress("unpacking", 661_288_526, 661_288_526)
        self._ready = True
        return self.fetch_steps * self.step_seconds

    def alive(self) -> list[StandInWorker]:
        """The workers opened and not yet closed: a microphone each."""
        return [worker for worker in self.workers if not worker.closed]

    async def open(self) -> StandInWorker:
        if self.open_fails:
            raise OSError("stand-in")
        self.opened += 1
        self.open_workers += 1
        self.worker = StandInWorker(self)
        self.workers.append(self.worker)
        await self.open_gate.wait()
        if self.starts:
            self.worker.put({"event": "ready", "load_s": 0.0, "rss_mb": None})
        if self.play_every > 0:
            self.worker.play(self.play_every)
        return self.worker
