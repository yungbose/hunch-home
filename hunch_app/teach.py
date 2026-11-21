"""The owner's taught examples: kept in the app's own file, loaded into the model's own teaching, listed, removed.

How it works. The released runtime keeps a person's examples in memory, apart from the shipped ones, which never
change (`Hunch.teach`, `Hunch.forget`, its API.md). A taught example carries no cut-off: it answers whenever it is
the closest of all the examples to what was said. This module adds only what the runtime leaves to its caller:

- **Keeping them.** `taught.json` in the app's data folder (`/data` in the Home Assistant app, kept by the
  Supervisor across updates and backups; never in the model's folder, so an update of the app or the model never
  overwrites them and they are never shipped). `{"format_version": 1, "examples": [{"id", "sentence", "action",
  "kind", "at"}]}`, oldest first, `at` in seconds since 1970. Written whole to a temporary file and renamed over
  the old one. A file that cannot be read (or has a format this app does not know) is kept aside under another
  name and the app starts with none; that is said once in the log and in `notice` (the page's state). Kept aside
  as the store keeps its own files (`store.set_aside`): never over another, at most ten. When one cannot be kept,
  the file is left as it is and nothing is written over it (`held`, said at the top of the page) until the owner
  removes some of the copies.
- **Loading them.** When a model is put to use (`attach`), every example is taught to it again, before it answers
  anything. A model is never used with half of them.
- **Limits.** At most `MAX_TAUGHT` examples, each a sentence of at most `MAX_CHARACTERS` characters. The same
  sentence (ignoring case and spaces) taught again replaces the earlier answer.

Nothing here decides what happens to a device: the model's answer still goes through the rules (`logic/`), which
run after it, so a taught example never overrides the lock and door rules or listening mode's refusals. The only
caller of `Teaching.teach` is `App.teach`, which only the page's `POST api/teach` calls, on the owner's tap: the
app never teaches by itself, from the record, from what it infers, or from listening mode. Teaching is not counted
in the daily counts (their fixed words have no place for it), and no sentence is sent anywhere.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .logic.client import NO_KIND, NOT_FOR_ME
from .logic.table import ACTIONS, ACTIONS_1_1, KINDS
from .store import held_notice, set_aside, write_atomic

LOG = logging.getLogger("hunch_app.teach")

TAUGHT_FILE = "taught.json"
FORMAT_VERSION = 1
# Each example is read again by the model at every start (about 3 seconds for 50 and 30 for 500 on a fast
# machine), so the owner's examples stop at 200.
MAX_TAUGHT = 200
MAX_CHARACTERS = 300   # as the model's runtime has it

# Every message the owner can read.
STILL_STARTING = "Hunch is still starting up, so it cannot learn yet. Try again in a moment."
NO_SENTENCE = "Type the sentence you want Hunch to learn."
TOO_LONG = f"That is too long to teach. Keep it to {MAX_CHARACTERS} characters or fewer."
FULL = (f"Hunch already keeps {MAX_TAUGHT} of your examples, which is as many as it can. Remove one before "
        "teaching another.")
NO_SUCH_ACTION = "Hunch cannot be taught to do that. Choose one of the answers it offers."
NO_SUCH_KIND = "Hunch does not know that kind of thing. Choose one of the kinds it offers."
CANNOT_DECLINE = "This version of Hunch cannot be taught that a sentence is not a home command."
NOT_LEARNT = "Hunch could not learn that sentence. Nothing was changed."
NOT_SAVED = "Hunch could not save that example, so it has not learnt it. Nothing was changed."
NOT_REMOVED = "Hunch could not remove that example. Try again in a moment."
GONE = "That example is already gone."
UNREADABLE = ("Your taught examples could not be read, so Hunch started without them. The file was kept aside, "
              "so nothing is lost.")
SOME_LEFT_OUT = ("Some of your taught examples could not be read and were left out. The file was kept aside, so "
                 "nothing is lost.")
OVER_LIMIT = (f"Hunch keeps at most {MAX_TAUGHT} of your examples, so it uses your newest {MAX_TAUGHT}. The file "
              "was kept aside, so nothing is lost.")
NOT_IN_THIS_VERSION = ("{n} of your examples could not be used by this version of Hunch. They are kept, and are "
                       "used again when it can.")
NOT_LOADED = ("Hunch could not use your taught examples this time, so it answers as it came. They are kept, and "
              "are used again after a restart.")


@dataclass(frozen=True)
class Example:
    id: str
    sentence: str
    action: str
    kind: str
    at: float

    def to_json(self) -> dict:
        return {"id": self.id, "sentence": self.sentence, "action": self.action, "kind": self.kind, "at": self.at}

    def public(self, active: bool = True) -> dict:
        """As the page has it (`web.Backend.taught`): `time` is when it was taught; `active` is false for an
        example the model in use could not take."""
        return {"id": self.id, "sentence": self.sentence, "action": self.action, "kind": self.kind,
                "time": datetime.fromtimestamp(self.at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "active": active}


def clean_sentence(sentence: object) -> str:
    """The sentence as it is kept and taught: spaces tidied, as the runtime tidies them."""
    if not isinstance(sentence, str) or not sentence.strip():
        raise ValueError(NO_SENTENCE)
    text = " ".join(sentence.split())
    if len(text) > MAX_CHARACTERS:
        raise ValueError(TOO_LONG)
    return text


def same(sentence: str) -> str:
    """What makes two sentences the same one: case and spaces ignored."""
    return " ".join(sentence.casefold().split())


def can_teach(model: object) -> bool:
    return bool(getattr(model, "ready", False)) and callable(getattr(model, "teach", None))


def offer(model: object) -> dict:
    """What the owner can teach with the model in use (`state.teach`): its actions and kinds as it lists them
    (those the rules know), and whether a sentence can be taught as not a home command."""
    actions, kinds = getattr(model, "actions", None), getattr(model, "kinds", None)
    if not actions or not kinds:
        version = str(getattr(model, "version", None) or "")
        actions = ACTIONS if version.startswith("1.0") else ACTIONS + ACTIONS_1_1
        kinds = KINDS
    known = set(ACTIONS + ACTIONS_1_1)
    return {"actions": [a for a in actions if a in known], "kinds": [k for k in kinds if k in KINDS],
            "can_decline": bool(getattr(model, "can_decline", False))}


def _example(row: object) -> Example | None:
    if not isinstance(row, Mapping):
        return None
    ident, sentence, action, kind, at = (row.get(k) for k in ("id", "sentence", "action", "kind", "at"))
    if not (isinstance(ident, str) and 0 < len(ident) <= 64 and ident.isalnum()):
        return None
    if not (isinstance(action, str) and isinstance(kind, str) and action and kind):
        return None
    if isinstance(at, bool) or not isinstance(at, int | float):
        return None
    try:
        sentence = clean_sentence(sentence)
    except ValueError:
        return None
    return Example(ident, sentence, action, kind, float(at))


class Teaching:
    """The owner's examples, the file they live in, and the model they are taught to."""

    def __init__(self, folder: Path | str, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(folder) / TAUGHT_FILE
        self.clock = clock
        self.examples: list[Example] = []     # oldest first, as in the file
        self.handles: dict[str, int] = {}     # an example's id -> the runtime's id for it in `model`
        self.model: object | None = None      # the model the examples are taught to
        self.notice = ""                      # said once in the log; shown in the page's state
        self.held = ""                        # the file is damaged and left as it is: why, for the page's top
        self._lock = asyncio.Lock()

    # The file.

    def load(self) -> None:
        """Read the file (once, at start). A missing file means none. Never raises."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:   # a folder in its place, no permission, not UTF-8
            self._set_aside(UNREADABLE, type(error).__name__)
            return
        try:
            data = json.loads(raw)
        except ValueError:
            self._set_aside(UNREADABLE, "not JSON")
            return
        if (not isinstance(data, Mapping) or data.get("format_version") != FORMAT_VERSION
                or not isinstance(data.get("examples"), list)):
            self._set_aside(UNREADABLE, "not a format this app knows")
            return
        found, bad, seen = [], 0, {}
        for row in data["examples"]:
            example = _example(row)
            if example is None or example.id in {e.id for e in found}:
                bad += 1
                continue
            key = same(example.sentence)
            if key in seen:   # the same sentence twice: the later one is the answer
                found.remove(seen[key])
            seen[key] = example
            found.append(example)
        over = max(0, len(found) - MAX_TAUGHT)
        if over:   # kept by an earlier version with a higher limit: the newest are used
            found = found[-MAX_TAUGHT:]
        self.examples = found
        if bad:
            self._set_aside(SOME_LEFT_OUT, f"{bad} could not be read", keep_file=True)
        elif over:
            self._set_aside(OVER_LIMIT, f"{over} more than the {MAX_TAUGHT} it keeps", keep_file=True)

    def _set_aside(self, notice: str, why: str, keep_file: bool = False) -> None:
        """Keep the file under another name (never deleted, never over another), and say so once. When it cannot
        be kept, the file is left as it is and never written over (`held`)."""
        # with `keep_file` the good examples are still read from the file; a copy is kept as it was
        aside = set_aside(self.path, copy=keep_file)
        if aside is None:
            LOG.warning("%s: %s. It is left as it is, and nothing is saved over it.", self.path.name, why)
            self.held = held_notice(self.path, "taught")
            return
        LOG.warning("%s: %s. It was kept as %s.", self.path.name, why, aside.name)
        self.notice = notice

    async def _write(self, examples: list[Example]) -> None:
        if self.held:
            raise OSError("the taught examples' file is left as it is")
        data = {"format_version": FORMAT_VERSION, "examples": [e.to_json() for e in examples]}
        await asyncio.to_thread(write_atomic, self.path, data)

    # The model.

    def use(self, model: object) -> bool:
        """Put a model to use at once when there is nothing to teach it; False when `attach` is needed."""
        if self.examples and can_teach(model):
            return False
        self.model = model if can_teach(model) else None
        self.handles = {}
        return True

    async def attach(self, model: object) -> None:
        """Teach a model every kept example, before it answers anything. Never raises: an example the model
        refuses is kept and listed as not in use, and a model that fails is used without them."""
        async with self._lock:
            self.handles = {}
            self.model = model if can_teach(model) else None
            if self.model is None or not self.examples:
                return
            try:
                ids = await model.teach([(e.sentence, e.action, e.kind) for e in self.examples])
            except Exception as error:  # noqa: BLE001 - the model answers without them rather than not at all
                LOG.warning("the taught examples could not be loaded into the model (%s)", type(error).__name__)
                self._say(NOT_LOADED)
                return
            for example, taught_id in zip(self.examples, ids, strict=True):
                if taught_id is not None:
                    self.handles[example.id] = taught_id
            left = len(self.examples) - len(self.handles)
            if left:
                self._say(NOT_IN_THIS_VERSION.format(n=left))
            LOG.info("%d taught example%s in use", len(self.handles), "" if len(self.handles) == 1 else "s")

    def _say(self, notice: str) -> None:
        if notice != self.notice:
            LOG.warning("%s", notice)
        self.notice = notice

    # The page's three calls.

    async def teach(self, sentence: str, action: str, kind: str) -> dict:
        """Teach one sentence its answer, keep it, and return it as the page has it. Raises `ValueError` with a
        plain message when it is refused; then nothing has changed."""
        text = clean_sentence(sentence)
        if self.held:
            raise ValueError(self.held)
        async with self._lock:
            model = self.model
            if model is None or not can_teach(model):
                raise ValueError(STILL_STARTING)
            allowed = offer(model)
            if action == NOT_FOR_ME:
                if not allowed["can_decline"]:
                    raise ValueError(CANNOT_DECLINE)
                if kind != NO_KIND:
                    raise ValueError(NO_SUCH_KIND)
            elif action not in allowed["actions"]:
                raise ValueError(NO_SUCH_ACTION)
            elif kind not in allowed["kinds"]:
                raise ValueError(NO_SUCH_KIND)
            old = next((e for e in self.examples if same(e.sentence) == same(text)), None)
            if old is None and len(self.examples) >= MAX_TAUGHT:
                raise ValueError(FULL)
            try:
                ids = await model.teach([(text, action, kind)])
            except Exception as error:  # noqa: BLE001 - a refusal in the owner's words, never a fault
                LOG.warning("the model could not learn a sentence (%s)", type(error).__name__)
                raise ValueError(NOT_LEARNT) from None
            taught_id = ids[0] if ids else None
            if taught_id is None:
                raise ValueError(NOT_LEARNT)
            example = Example(uuid.uuid4().hex[:16], text, action, kind, float(self.clock()))
            examples = [e for e in self.examples if e is not old] + [example]
            try:
                await self._write(examples)
            except OSError as error:
                LOG.warning("could not save the taught examples (%s)", type(error).__name__)
                await self._forget_in(model, [taught_id])
                raise ValueError(NOT_SAVED) from None
            self.examples = examples
            self.handles[example.id] = taught_id
            if old is not None:
                earlier = self.handles.pop(old.id, None)
                if earlier is not None:
                    await self._forget_in(model, [earlier])
            return example.public()

    def listed(self) -> list[dict]:
        """The owner's examples, newest first."""
        return [e.public(active=self.model is None or e.id in self.handles) for e in reversed(self.examples)]

    async def forget(self, example_id: str) -> None:
        """Remove one example; the answer goes back to what the shipped examples give. `LookupError` when it is
        not there, `ValueError` when the file cannot be written (then nothing has changed)."""
        async with self._lock:
            example = next((e for e in self.examples if e.id == example_id), None)
            if example is None:
                raise LookupError(GONE)
            if self.held:
                raise ValueError(self.held)
            examples = [e for e in self.examples if e is not example]
            try:
                await self._write(examples)
            except OSError as error:
                LOG.warning("could not save the taught examples (%s)", type(error).__name__)
                raise ValueError(NOT_REMOVED) from None
            self.examples = examples
            taught_id = self.handles.pop(example.id, None)
            if taught_id is not None and self.model is not None:
                await self._forget_in(self.model, [taught_id])

    @staticmethod
    async def _forget_in(model: object, ids: list[int]) -> None:
        try:
            await model.forget(ids)
        except Exception as error:  # noqa: BLE001
            LOG.warning("the model could not remove a taught example (%s)", type(error).__name__)
