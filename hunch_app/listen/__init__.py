"""Listening with no wake word: experimental, off by default.

With the setting off, nothing of it is loaded: no speech library is imported into the app, the microphone is not
opened and the speech model is not fetched. Switched on from the page, it starts without a restart:

1. The first time, the speech model (Parakeet, `speech.py`) is fetched into the app's data folder, checked and
   unpacked, after the owner has confirmed it on the page. This is the only thing the app ever fetches.
2. A worker process (`worker.py`) loads the speech detector and Parakeet and opens the default sound input: under
   Home Assistant, the input the owner chose in the app's Audio setting. Nothing is loaded into Home Assistant's
   sound system and its default input is never changed.
3. The worker cuts the sound into utterances (`cutter.py`); each utterance becomes one turn
   (`turn.Turns.run(words, "listening")`), with the microphone's room from the settings. The page gets `hearing`
   when speech starts, `quiet` when it ends, then the turn.

Switched off, the worker is stopped: the microphone is closed and all of the models' memory is given back.

What `turn.py` already holds for listening, whatever the settings: it never unlocks, never opens or closes
anything that is not a window covering, passes nothing on to any agent, and an ignored sentence leaves no record
and no event with its words. This module adds: the words go nowhere but `turns.run` (never logged, at any level),
a turn's event is the only event that may carry them, and the daily counts leave listening out (`counts.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..api import IGNORED, LABEL_IGNORED, LISTENING
from . import speech
from .speech import FetchError, SpeechModel

LOG = logging.getLogger("hunch_app.listen")

# What `state()["listening"]["status"]` can be.
OFF = "off"                     # switched off; the speech model is here
NEEDS_MODEL = "needs model"     # switched off; the speech model has not been fetched
FETCHING = "fetching"           # fetching or unpacking the speech model, with progress
STARTING = "starting"           # loading the models and opening the microphone
ON = "on"
FAILED = "could not start"      # with a plain reason in `problem`

MEMORY_NEEDED_MB = 900          # on top of the app, from the microphone proof (20 Nov 2026)
MICROPHONE_EVERY = 5.0          # seconds between readings of the sound system's inputs from the Supervisor
STOP_WAIT = 1.5                 # seconds the worker is given to end before it is killed
MAX_WORDS = 300                 # characters: as `turn.MAX_CHARACTERS`
RETRY_WAITS = (2.0, 5.0, 15.0, 30.0, 60.0)   # seconds before each restart in turn ...
KEEP_TRYING = 300.0             # ... then every five minutes, for as long as listening is switched on
HEALTHY = 120.0                 # seconds on before the waits go back to the short ones

PROBLEMS = {
    "memory": "There was not enough memory to load the speech model. Listening needs about 0.9 GB more than "
              "the rest of Hunch. Close another app or add memory, then switch it on again.",
    "model": "The speech model could not be loaded. Switch listening off and on again to try once more.",
    "microphone": "The microphone could not be opened. Check it is plugged in and chosen in the app's Audio "
                  "setting, restart the app, then switch listening on again.",
    "no microphone": "No microphone is chosen. Plug a USB microphone into this machine, choose it in the app's "
                     "Audio setting on its Configuration tab, restart the app, then switch listening on.",
    "stopped": "Listening stopped unexpectedly. Switch it off and on again to start it once more.",
    "ran out": "Listening stopped because this machine ran out of memory.",
    "worker": "The listening part of Hunch could not be started.",
    "trying again": "The microphone is not answering. Hunch keeps trying.",
    "missing": "The speech model is no longer on this machine, so listening is off. Switch it on to fetch it again.",
}


def _keeps(char: str, before: str, after: str) -> bool:
    """Whether a character of the speech model's words is kept (`tidy`)."""
    return bool(("a" <= char <= "z") or ("0" <= char <= "9") or char == " "
                or (char in ".,:/" and before.isdigit() and after.isdigit())
                or (char == "-" and after.isdigit() and (before.isdigit() or before in ("", " ")))
                or char in "%\u00b0"
                or (char == "'" and before.isalpha() and after.isalpha()))


def source_descriptions(text: str) -> dict[str, str]:
    """Each input's description, by its id, from `pactl list sources` (its "Name:" and "Description:" lines)."""
    found: dict[str, str] = {}
    name = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Source #"):
            name = None
        elif line.startswith("Name:"):
            name = line[len("Name:"):].strip() or None
        elif line.startswith("Description:") and name:
            found[name] = line[len("Description:"):].strip()
    return found


def sound_env() -> dict:
    """The environment the sound tools are run in: the app's own, with a home they can write to (the app's own user
    has none, and the tools want one)."""
    env = dict(os.environ)
    if not os.access(env.get("HOME") or "/nonexistent", os.W_OK):
        env["HOME"] = "/tmp"
    return env


async def pactl_sources() -> str:
    """`pactl list sources`, as the app's own sound tools see it ("" when it cannot be had in a few seconds)."""
    try:
        process = await asyncio.create_subprocess_exec("pactl", "list", "sources", stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE,
                                                       stdin=asyncio.subprocess.DEVNULL, env=sound_env())
    except OSError as error:
        LOG.info("could not ask the sound system for the microphone's description (%s)", type(error).__name__)
        return ""
    try:
        out, err = await asyncio.wait_for(process.communicate(), 3.0)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        return ""
    if process.returncode != 0:
        first = (err or b"").decode("utf-8", errors="replace").strip().splitlines()[:1]
        LOG.info("could not ask the sound system for the microphone's description (exit %s: %s)",
                 process.returncode, first[0][:120] if first else "")
        return ""
    return out.decode("utf-8", errors="replace")


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def tidy(words: str) -> str:
    """Words as a person would type a command: lower case, with sentence punctuation taken out. Parakeet writes
    the filler "Um" with a capital, and on a Raspberry Pi this word was most of the mistaken actions in a meeting
    until the words were tidied (18 Nov 2026).

    What carries a value is kept, since the rules read it: a decimal point or comma, a colon or a slash between
    digits ("21.5", "21,5", "7:30", "20/22"), a hyphen between digits ("20-22", which the rules refuse as a
    range), a minus before a number ("-2"), a percent or degree sign, and an apostrophe inside a word. A
    number or a time the rules would misread is caught with every other sentence's, by the turn (`unsure.py`):
    from listening it is then ignored."""
    text = words.lower().replace("\u2019", "'")
    out = []
    for i, char in enumerate(text):
        before = text[i - 1] if i else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        out.append(char if _keeps(char, before, after) else " ")
    return " ".join("".join(out).split())


def free_memory_mb(meminfo: str = "/proc/meminfo", cgroup: str = "/sys/fs/cgroup") -> int | None:
    """Memory this app could still use: the machine's available memory, or the container's limit less what it
    uses, whichever is smaller. None where neither can be read (not Linux)."""
    found = []
    try:
        with open(meminfo, encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    found.append(int(line.split()[1]) // 1024)
    except (OSError, ValueError):
        pass
    try:
        limit = Path(cgroup, "memory.max").read_text().strip()
        used = int(Path(cgroup, "memory.current").read_text().strip())
        if limit != "max":
            found.append(max(0, (int(limit) - used) // (1024 * 1024)))
    except (OSError, ValueError):
        pass
    return min(found) if found else None


class Worker:
    """The worker process, as the app sees it: its lines, and a way to stop it."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.last_error = ""
        self._errors = asyncio.get_running_loop().create_task(self._drain(), name="hunch listen errors")

    async def _drain(self) -> None:
        """The worker's error output is read so it never blocks, and kept only as the name of the last Python
        error (never a whole line: it is never logged as it is)."""
        assert self.process.stderr is not None
        while line := await self.process.stderr.readline():
            found = re.match(rb"^([A-Za-z_][\w.]*(?:Error|Exception|Interrupt))\b", line.strip())
            if found:
                self.last_error = found.group(1).decode(errors="replace")[:80]

    async def next(self) -> dict | None:
        """The next line as a dict; None once the worker has ended."""
        import json

        assert self.process.stdout is not None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                return None
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                return event

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def ended_by_kill(self) -> bool:
        return self.process.returncode == -9

    async def close(self) -> None:
        process = self.process
        if process.returncode is None:
            with contextlib.suppress(Exception):
                process.stdin.close()
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), STOP_WAIT)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        self._errors.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._errors


class ProcessEar:
    """The real ear: the speech model on disk, and the worker started as a process of its own."""

    def __init__(self, folder: Path | str, threads: int = 2, env: dict | None = None) -> None:
        self.speech = SpeechModel(folder)
        self.threads = threads
        self.env = dict(os.environ if env is None else env)

    def ready(self) -> bool:
        return self.speech.ready()

    async def fetch(self, session, progress: Callable[[str, int, int], None]) -> float:
        return await self.speech.fetch(session, progress)

    def stop_fetching(self) -> None:
        self.speech.stop()

    def describe(self) -> dict:
        return {"download_mb": speech.megabytes(self.speech.archive_bytes),
                "disk_mb": speech.megabytes(self.speech.unpacked_bytes),
                "needs_disk_mb": speech.megabytes(self.speech.disk_needed())}

    async def open(self) -> Worker:
        package = Path(__file__).resolve().parents[2]   # the folder that holds `hunch_app`
        env = dict(self.env)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(package), env.get("PYTHONPATH"))))
        env.update(OMP_WAIT_POLICY="passive", GOMP_SPINCOUNT="0", ORT_DISABLE_TELEMETRY="1",
                   PYTHONUNBUFFERED="1")
        # The sound tools want a home they can write to; the app's own user has none.
        if not os.access(env.get("HOME") or "/nonexistent", os.W_OK):
            env["HOME"] = "/tmp"
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "hunch_app.listen.worker", "--model", str(self.speech.model),
            "--threads", str(self.threads), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env)
        return Worker(process)


class Listening:
    """Listening's switch and status, joined to the app (its store, turns, live stream and Home Assistant)."""

    def __init__(self, app, ear, clock: Callable[[], float] = time.monotonic,
                 memory: Callable[[], int | None] = free_memory_mb) -> None:
        self.app = app
        self.ear = ear
        self.clock = clock
        self.memory = memory
        self.state = OFF
        self.problem = ""
        self.progress: dict | None = None     # {"stage", "done", "total"} while fetching
        self._told: tuple | None = None       # what the pages were last told about (`set`)
        self.sleep = asyncio.sleep            # the wait before starting again (a test's own)
        self.microphone: str | None = None     # the input's id: the one chosen, or the machine's default
        self.microphone_name: str | None = None
        self.chosen = False                    # the owner chose it in the app's Audio setting
        self.inputs: int | None = None         # how many inputs the sound system has; None when not known
        self._mic_at = -MICROPHONE_EVERY
        self.tried_at: float | None = None     # when the microphone was last tried (seconds since 1970)
        self.wall = time.time                  # the clock that time is read from (a test's own)
        self._on_since: float | None = None    # when the present worker became ready (`clock`)
        self.sources = pactl_sources           # the sound system's own list of inputs (a test's own)
        self._task: asyncio.Task | None = None
        self._turns: asyncio.Task | None = None
        self._heard: asyncio.Queue = asyncio.Queue()
        self._worker: Worker | None = None
        self.ready_at: float | None = None
        # One transition of the switch at a time (the check, saving the setting, fetching, starting, stopping):
        # a second request waits, then sees the settled state.
        self._lock = asyncio.Lock()
        self._workers: set = set()             # every worker started and not yet seen to end
        self._opening: set[asyncio.Future] = set()   # workers being started
        self._deciding: asyncio.Task | None = None   # the turn being decided for a heard sentence
        self._accepting = False                # a heard sentence may act (listening is on)
        self._session = 0                      # one more each time listening stops: older words never act

    # The setting.

    @property
    def wanted(self) -> bool:
        return self.app.store.settings.get("listening") is True

    def set(self, state: str, problem: str | None = None) -> None:
        """Listening's status (and its plain reason). Every page is told when either changes, so it follows
        without asking again and again (`App.state_changed`)."""
        self.state = state
        if problem is not None:
            self.problem = problem
        self._tell()

    def _tell(self) -> None:
        progress = self.progress or {}
        total = progress.get("total") or 0
        step = int(20 * progress.get("done", 0) / total) if total else 0   # every 5%
        tried = self.tried_at if self.problem == PROBLEMS["trying again"] else None
        told = (self.state, self.problem, progress.get("stage"), step, tried)
        if told != self._told:
            self._told = told
            changed = getattr(self.app, "state_changed", None)
            if callable(changed):
                changed()

    def _resting(self) -> str:
        return OFF if self.ear.ready() else NEEDS_MODEL

    async def start(self) -> None:
        """At the app's start: back on by itself if it was left on (without fetching anything)."""
        async with self._lock:
            await self._end_workers()
            self.set(self._resting())
            self._turns = asyncio.get_running_loop().create_task(self._run_turns(), name="hunch listen turns")
            if not self.wanted:
                return
            if not self.ear.ready():
                await self.app.store.save_settings({"listening": False})
                self.set(self.state, PROBLEMS["missing"])
                LOG.warning("listening was on, but the speech model is not here; it is off until switched on again")
                return
            self.set(STARTING, "")
            self._begin(self._listen())

    async def switch(self, on: bool) -> None:
        """The owner's switch on the page. On: fetch the model first if it is not here (the page has asked the
        owner), then start. Off: stop whatever is happening, close the microphone and free the models."""
        if not on:
            self._accepting = False   # at once, before waiting for any transition: nothing heard acts from now
        async with self._lock:
            if on:
                if self._task is not None and not self._task.done():
                    return            # already fetching, starting or on
                if self.ear.ready():
                    await self.app.store.save_settings({"listening": True})
                    self.set(STARTING, "")
                    self._begin(self._listen())
                else:
                    self.progress = {"stage": "fetching", "done": 0, "total": 0}
                    self.set(FETCHING, "")
                    self._begin(self._fetch_then_listen())
                return
            await self._halt()
            await self.app.store.save_settings({"listening": False})
            self.progress = None
            self.set(self._resting(), "")
            LOG.info("listening: off")

    async def stop(self) -> None:
        """The app is stopping: end the worker and any fetch at once (the setting is kept)."""
        self._accepting = False
        async with self._lock:
            await self._halt()
        if self._turns is not None:
            self._turns.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turns
            self._turns = None

    def _begin(self, work) -> None:
        self._task = asyncio.get_running_loop().create_task(work, name="hunch listening")

    async def _halt(self) -> None:
        """Stop everything listening is doing: the fetch, every worker (and any still being started), the turn
        being decided and the words waiting for one. Call with the lock held."""
        self._accepting = False
        self._session += 1
        task, self._task = self._task, None
        stop_fetching = getattr(self.ear, "stop_fetching", None)
        if self.state == FETCHING and callable(stop_fetching):
            stop_fetching()
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        deciding = self._deciding
        if deciding is not None and not deciding.done():
            deciding.cancel()
            await asyncio.wait({deciding})
        while not self._heard.empty():   # words not yet acted on are dropped, unread
            self._heard.get_nowait()
        await self._end_workers()
        self.ready_at = None

    async def _end_workers(self) -> None:
        """End every worker started and not yet ended, including one whose start was cut short."""
        for opening in list(self._opening):
            self._opening.discard(opening)
            with contextlib.suppress(Exception, asyncio.CancelledError):   # it did not start: nothing to end
                self._workers.add(await opening)
        self._worker = None
        for worker in list(self._workers):
            self._workers.discard(worker)
            with contextlib.suppress(Exception):
                await worker.close()

    # Fetching.

    def _progress(self, stage: str, done: int, total: int) -> None:
        if self.state != FETCHING:   # a late report from a fetch already stopped
            return
        self.progress = {"stage": stage, "done": done, "total": total}
        self._tell()

    async def _fetch_then_listen(self) -> None:
        self.progress = {"stage": "fetching", "done": 0, "total": 0}
        self.set(FETCHING, "")
        LOG.info("listening: fetching the speech model, as the owner asked")
        try:
            await self.ear.fetch(self.app.ha.session, self._progress)
        except FetchError as error:
            self.progress = None
            self.set(FAILED, str(error))
            LOG.warning("listening: the speech model could not be fetched")
            return
        except Exception as error:  # noqa: BLE001
            self.progress = None
            self.set(FAILED, PROBLEMS["worker"])
            LOG.warning("listening: fetching the speech model failed (%s)", type(error).__name__)
            return
        self.progress = None
        await self.app.store.save_settings({"listening": True})
        await self._listen()

    # Listening.

    async def _listen(self) -> None:
        """Start the worker and follow it. If it ends by itself once it was listening, or the microphone stops
        answering (a USB microphone unplugged, the sound system restarted), start it again after a growing wait
        (`RETRY_WAITS`), then every `KEEP_TRYING` seconds, for as long as listening is switched on: it never gives
        up on a microphone by itself. Switching off ends the waiting at once (`_halt` cancels this task)."""
        failures = 0       # tries since listening was last healthy
        again = False
        while True:
            self._on_since = None
            again = await self._run_worker(again)
            if not again:
                return
            # The waits go back to the short ones only after listening has been on for a while: never because
            # earlier failures are old (a long outage would otherwise fall back to them, loading the speech models
            # in bursts while the microphone is still gone; found by the other model family, 20 Nov 2026).
            if self._on_since is not None and self.clock() - self._on_since >= HEALTHY:
                failures = 0
            wait = RETRY_WAITS[failures] if failures < len(RETRY_WAITS) else KEEP_TRYING
            failures += 1
            self.set(STARTING, PROBLEMS["trying again"])
            # Quietly once the waits are long: one line every five minutes is enough in the log.
            log = LOG.info if wait < KEEP_TRYING else LOG.debug
            log("listening: the microphone is not answering; trying again in %g s", wait)
            await self.sleep(wait)

    async def _run_worker(self, again: bool = False) -> bool:
        """One worker, from start to end. True when it should be started again. `again`: a restart after the
        microphone stopped answering (then a microphone missing altogether is tried again too)."""
        # The time first, so the page is told of every try ("Last tried at" moves on: `_tell`).
        self.tried_at = self.wall()
        self.set(STARTING, self.problem if self.problem == PROBLEMS["trying again"] else "")
        # No microphone at all: said at once when switched on; a restart tries anyway (unplugged, it may be back).
        if self.app.ha.access.supervisor and self.inputs == 0 and not self.chosen and not again:
            self.set(FAILED, PROBLEMS["no microphone"])
            return False
        began = self.clock()
        # Started apart from this task, so a stop that cuts this task short still gets the worker to end it.
        opening = asyncio.ensure_future(self.ear.open())
        self._opening.add(opening)
        try:
            worker = await asyncio.shield(opening)
        except OSError as error:
            self._opening.discard(opening)
            self.set(FAILED, PROBLEMS["worker"])
            LOG.warning("listening: the worker could not be started (%s)", type(error).__name__)
            return False
        self._opening.discard(opening)
        self._workers.add(worker)
        self._worker = worker
        was_on = False
        try:
            while (event := await worker.next()) is not None:
                kind = event.get("event")
                if kind == "ready":
                    was_on, self.ready_at = True, self.clock()
                    self._on_since = self.ready_at
                    self._accepting = True
                    self.set(ON, "")
                    LOG.info("listening: on, %.1f s to start (models loaded in %s s, %s MB)",
                             self.clock() - began, event.get("load_s"), event.get("rss_mb"))
                elif kind == "hearing":
                    self.app.emit({"event": "hearing"})
                elif kind == "quiet":
                    self.app.emit({"event": "quiet", "speech": event.get("speech") is not False})
                elif kind == "heard":
                    self._heard.put_nowait((self._session, event))
                elif kind == "problem":
                    reason = event.get("reason") if event.get("reason") in PROBLEMS else "worker"
                    (LOG.debug if again else LOG.warning)("listening: could not go on (%s: %s)", reason,
                                                          str(event.get("detail"))[:200])
                    # The microphone ending (or never answering) may come back; a model that cannot be loaded
                    # will not.
                    if reason == "microphone" or (was_on and reason != "memory"):
                        return True
                    self.set(FAILED, PROBLEMS[reason])
                    return False
        finally:
            if self._worker is worker:
                self._worker = None
            await worker.close()
            self._workers.discard(worker)
        # The worker ended by itself.
        LOG.warning("listening: the worker ended (exit code %s%s)", worker.returncode,
                    f", {worker.last_error}" if worker.last_error else "")
        if was_on:
            return True
        self.set(FAILED, PROBLEMS["ran out"] if worker.ended_by_kill else PROBLEMS["stopped"])
        return False

    async def _run_turns(self) -> None:
        """One utterance, one turn, in the order heard. The words are given to `turns.run` and nowhere else."""
        while True:
            session, event = await self._heard.get()
            if session != self._session:
                continue              # heard before listening was last stopped
            # Each turn is a task of its own, so switching off can cut it short (`_halt`).
            self._deciding = asyncio.get_running_loop().create_task(self._turn(event, session),
                                                                    name="hunch listening turn")
            await asyncio.wait({self._deciding})
            if not self._deciding.cancelled() and self._deciding.exception() is not None:
                LOG.warning("listening: a turn failed (%s)", type(self._deciding.exception()).__name__)

    def may_act(self, session: int) -> bool:
        """Whether a sentence heard in `session` may still act: listening is on, and has not been stopped since."""
        return self._accepting and session == self._session

    async def _turn(self, event: dict, session: int) -> None:
        from ..logic.record import new_id
        from ..turn import Turn

        words = tidy(str(event.get("words") or ""))
        if not words or len(words) > MAX_WORDS:
            # Nothing to decide on: ignored, with nothing of what was heard.
            self.app.emit(Turn(new_id(), time.time(), LISTENING, "", IGNORED, LABEL_IGNORED).event())
            return
        turn = await self.app.turns.run(words, LISTENING, may_act=lambda: self.may_act(session))
        if turn.outcome != IGNORED:
            ended = event.get("speech_end")
            heard_at = event.get("heard_at")
            if isinstance(ended, (int, float)) and isinstance(heard_at, (int, float)):
                LOG.info("listening: carried out a command; the words came %d ms and the turn ended %d ms after "
                         "the end of speech (%.3f)", round((heard_at - ended) * 1000),
                         round((time.time() - ended) * 1000), ended)

    # The microphone, as Home Assistant's Supervisor describes it.

    async def read_microphone(self) -> None:
        """The chosen input (the app's Audio setting, read by `assistant.py`) and its description, from the
        Supervisor's `/audio/info` (which needs `hassio_api`); the machine's default input when none is chosen.
        At most every few seconds; outside the Supervisor there is nothing to read."""
        ha = self.app.ha
        if not ha.access.supervisor:
            self.microphone = self.microphone_name = None
            self.inputs = None
            return
        chosen = self.app.assistants.microphone
        self.chosen = bool(chosen)
        if self.clock() - self._mic_at < MICROPHONE_EVERY:
            if chosen:
                self.microphone = chosen
            return
        self._mic_at = self.clock()
        inputs = None
        try:
            status, reply = await ha.supervisor("GET", "/audio/info")
            data = reply.get("data") if isinstance(reply, dict) else None
            audio = data.get("audio") if isinstance(data, dict) and status == 200 else None
            listed = audio.get("input") if isinstance(audio, dict) else None
            if isinstance(listed, list):
                inputs = [i for i in listed if isinstance(i, dict) and isinstance(i.get("name"), str)]
        except Exception as error:  # noqa: BLE001 - the description is a nicety; the id is enough
            LOG.debug("could not read the sound inputs (%s)", type(error).__name__)
        self.inputs = None if inputs is None else len(inputs)
        found = None
        if inputs:
            if chosen:
                found = next((i for i in inputs if i["name"] == chosen), None)
            else:
                found = next((i for i in inputs if i.get("default") is True), None)
        self.microphone = chosen or (found["name"] if found else None)
        name = found.get("description") if found else None
        if not found and self.microphone:
            # The Supervisor lists the inputs it saw when it last read the sound system: one added since (seen in
            # the test machine, 20 Nov 2026) is missing until it reloads. The sound system itself knows it.
            name = source_descriptions(await self.sources()).get(self.microphone)
        self.microphone_name = name if isinstance(name, str) and name.strip() else None

    # What the page shows.

    def status(self) -> dict:
        describe = getattr(self.ear, "describe", dict)()
        free = self.memory() if self.state not in (ON, STARTING) else None
        warning = ""
        if free is not None and free < MEMORY_NEEDED_MB + 100 and self.state not in (ON, STARTING):
            warning = (f"This machine has about {free} MB of memory free, and listening needs about "
                       f"{MEMORY_NEEDED_MB} MB more. It may still start, but the machine could slow down or "
                       "stop other apps.")
        progress = None
        if self.state == FETCHING and self.progress:
            total = self.progress["total"] or 0
            progress = {"stage": self.progress["stage"], "done_mb": speech.megabytes(self.progress["done"]),
                        "total_mb": speech.megabytes(total),
                        "fraction": round(self.progress["done"] / total, 3) if total else 0.0}
        supervisor = self.app.ha.access.supervisor
        return {
            "available": (self.inputs != 0 or self.chosen) if supervisor else True,
            "status": self.state,
            "problem": self.problem,
            # when the microphone was last tried, while it is not answering and Hunch keeps trying
            "tried": (_iso(self.tried_at) if self.problem == PROBLEMS["trying again"] and self.tried_at is not None
                      else None),
            "microphone": self.microphone,
            "microphone_name": self.microphone_name,
            "chosen": self.chosen if supervisor else None,
            "model": {"ready": self.ear.ready(), "name": speech.NAME, "maker": speech.MAKER,
                      "licence": speech.LICENCE, "source": speech.SOURCE, "url": speech.URL,
                      **describe, "progress": progress},
            "memory": {"free_mb": free, "needed_mb": MEMORY_NEEDED_MB, "warning": warning},
        }
