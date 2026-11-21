"""Cut the microphone's sound into utterances and hear each one as early as possible: the listener
of an earlier Raspberry Pi version of Hunch, brought into the app.

A small speech detector (Silero) looks at every 32 ms of sound. Speech starts an utterance (`hearing`). The moment
the speaker seems to pause (`early`, 0.096 s of quiet) the ear starts on what has been said so far, on a second
thread, while the detector waits to be sure the speaker has stopped (`end`, 0.48 s of quiet; a Pi's lesson: the
longer wait lost no time and stopped some commands being heard in two pieces). If the speaker carries on, those
words are dropped and the next pause starts again. So the wait to be sure and the speech model's own time overlap
instead of adding up.

Every utterance ends with `quiet` (with `speech` false for a sound too short to be speech) and then, for speech,
exactly one `heard` event with the words and the clock: when the speech ended (by the detector) and when the
words were ready. Nothing here acts on the words, logs them or keeps them; the caller is handed the events.

The detector and the ear are given (`process_samples(list) -> chance`, `hear(samples) -> words`), so a test can
give stand-ins; `join(frames)` puts frames together (numpy's `concatenate` in the worker).
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections import deque
from collections.abc import Callable

RATE = 16000
FRAME = 512          # samples at 16 kHz: 32 ms, the speech detector's own step
FRAME_S = FRAME / RATE


class Cutter:
    """Frames in, events out (`on_event(dict)`, called from the feeding thread or the ear's thread)."""

    def __init__(self, detector, ear, on_event: Callable[[dict], None], join: Callable[[list], object],
                 start: float = 0.5, stop: float = 0.35, early: float = 0.096, end: float = 0.48,
                 before: float = 0.32, shortest: float = 0.25, longest: float = 12.0,
                 clock: Callable[[], float] = time.time) -> None:
        self.detector, self.ear, self.on_event, self.join, self.clock = detector, ear, on_event, join, clock
        self.start, self.stop = start, stop
        self.early_frames = max(1, round(early / FRAME_S))
        self.end_frames = max(self.early_frames, round(end / FRAME_S))
        self.shortest, self.longest = shortest, longest
        self.recent: deque = deque(maxlen=max(1, round(before / FRAME_S)))
        self.sound: list = []
        self.speaking = False
        self.quiet = 0
        self.utterance = 0
        self.speech_end = 0.0
        self.speech_start = 0.0
        self.kept_before = 0
        self.lock = threading.Lock()
        self.jobs: queue.Queue = queue.Queue()
        # The utterance being spoken: what the ear was last asked about, as (utterance, frames), and its words
        # once heard. A new pause replaces both.
        self.asked: tuple[int, int] | None = None
        self.ready: dict | None = None
        # Utterances that are over and still waiting for their words: utterance -> (utterance, frames).
        self.finals: dict[int, tuple[int, int]] = {}
        self.passed_on: set[int] = set()
        self.closed = False
        self.thread = threading.Thread(target=self._work, name="hunch ear", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.closed = True
        self.jobs.put(None)

    # The second thread: one hearing at a time, in the order asked, skipping what a later pause replaced.
    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            key, samples, asked_at, ended = job
            with self.lock:
                wanted = self.asked == key or self.finals.get(key[0]) == key
            try:
                if wanted:
                    self._hear(key, samples, asked_at, ended)
            except Exception as error:  # noqa: BLE001 (one failed hearing must not stop the listening for good)
                with self.lock:
                    self.finals.pop(key[0], None)
                    self.passed_on.add(key[0])
                with contextlib.suppress(Exception):
                    self.on_event({"event": "heard", "utterance": key[0], "words": "", "failed":
                                   type(error).__name__, "speech_end": ended, "at": self.clock()})
            finally:
                self.jobs.task_done()

    def _hear(self, key: tuple[int, int], samples, asked_at: float, ended: float) -> None:
        began = time.perf_counter()
        words = self.ear.hear(samples)
        record = {"key": key, "words": words, "speech_end": ended, "asked_at": asked_at,
                  "hear_ms": round((time.perf_counter() - began) * 1000, 1), "heard_at": self.clock()}
        with self.lock:
            if self.finals.get(key[0]) == key:      # its utterance is over: these are its words
                del self.finals[key[0]]
                self._pass_on(record)
            elif self.asked == key:                 # still being spoken: keep them for when it ends
                self.ready = record

    def _pass_on(self, record: dict) -> None:
        """One `heard` event for an utterance, never two. Call with the lock held."""
        key = record["key"]
        if key[0] in self.passed_on:
            return
        self.passed_on.add(key[0])
        self.passed_on -= {old for old in self.passed_on if old < key[0] - 50}
        self.on_event({"event": "heard", "utterance": key[0], "words": record["words"],
                       "speech_end": record["speech_end"], "heard_at": record["heard_at"],
                       "hear_ms": record["hear_ms"], "seconds": round(key[1] * FRAME_S, 2), "at": self.clock()})

    def _ask(self, now: float) -> None:
        frames = len(self.sound) - self.quiet + 2  # the speech and two frames of what follows
        key = (self.utterance, frames)
        if self.asked == key:
            return
        self.asked, self.ready = key, None
        self.jobs.put((key, self.join(self.sound[:frames]), now, self.speech_end))

    def feed(self, frame, now: float) -> None:
        chance = self.detector.process_samples(frame.tolist() if hasattr(frame, "tolist") else list(frame))
        with self.lock:
            if not self.speaking:
                self.recent.append(frame)
                if chance >= self.start:
                    self.speaking, self.quiet = True, 0
                    self.utterance += 1
                    self.sound = list(self.recent)
                    self.kept_before = len(self.sound) - 1  # frames from before the speech; they are not speech
                    self.speech_start = now - FRAME_S
                    self.speech_end = now
                    self.asked = self.ready = None
                    self.on_event({"event": "hearing", "utterance": self.utterance, "at": now})
                return
            self.sound.append(frame)
            if chance >= self.stop:
                if self.quiet >= self.early_frames and self.utterance not in self.passed_on:
                    self.asked = self.ready = None  # the speaker carried on: drop the early words
                self.quiet = 0
                self.speech_end = now
            else:
                self.quiet += 1
            spoken = (len(self.sound) - self.kept_before - self.quiet) * FRAME_S
            too_long = len(self.sound) * FRAME_S >= self.longest
            if self.quiet == self.early_frames and spoken >= self.shortest:
                self._ask(now)
            if self.quiet >= self.end_frames or too_long:
                self.speaking = False
                self.recent.clear()
                if spoken < self.shortest and not too_long:
                    self.asked = self.ready = None
                    self.passed_on.add(self.utterance)
                    self.on_event({"event": "quiet", "utterance": self.utterance, "speech": False, "at": now})
                    self.sound = []
                    return
                self.on_event({"event": "quiet", "utterance": self.utterance, "speech": True, "at": now,
                               "speech_end": self.speech_end})
                if self.utterance not in self.passed_on:
                    if self.asked is None or too_long:
                        self._ask(now)
                    if self.ready is not None and self.ready["key"] == self.asked:
                        self._pass_on(self.ready)              # the words were already waiting
                    else:
                        self.finals[self.utterance] = self.asked  # the second thread passes them on
                self.asked = self.ready = None
                self.sound = []
