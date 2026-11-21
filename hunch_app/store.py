"""What the app keeps on disk: its settings, the record and the install number, under one data folder.

The folder is given at start (`/data` in the Home Assistant app, where the Supervisor keeps it across updates
and backs it up). Three files:

- `settings.json`: the owner's settings (`api.SETTINGS_DEFAULTS` names them; a missing one is its default).
- `record.json`: the rules' record (`logic/record.py`: the latest 1,000 turns, none older than 30 days, with
  the install number, the day the counts were last sent and whether a deletion of them is owed), and beside
  it what the app adds to each turn (where the sentence came from, the outcome and label the page shows, the
  names of the devices acted on, the reply, how long it took, and a note of why a hand-off failed), and what
  the page shows of the counts' deletion (`sharing`: when one was last done, and when one last failed).
- `assistants.json`: what the one-button set-up changed (`assistant.py`): for each assistant it set up, the
  agent it had and whether "prefer handling commands locally" was on, so undo puts back exactly that; and
  whether the owner has chosen the agent to pass on to themselves.

Every write goes to a temporary file in the same folder and is renamed over the old one in one step, so a file
is never left half written. A file that cannot be read (no permission, a folder in its place, not UTF-8, not
JSON, not the shape the app keeps, or a record the rules cannot take) is set aside (`set_aside`) and the app
starts afresh, rather than refusing to start. That is said once in the log, and in `notices`, which the page's
state carries, so the owner knows what was started afresh.

A set-aside file is never overwritten: the first is `<name>.unreadable`, later ones `<name>.unreadable-<day>-<n>`,
and a name already taken is never replaced. At most `MAX_SET_ASIDE` are kept of each kind, and none is ever
deleted by the app. Past that, or when the file cannot be moved at all, the damaged file is left exactly where it
is and the app saves no changes to that file (`held`) until the owner removes some, so nothing is lost; the
notice says so. The taught examples (`teach.py`) follow the same rule.

The record is kept on this machine only. With "keep a record" off, no turn is added (the install number and
the counts' state stay, since they are not words). A turn whose outcome is `ignored` is never written, whatever
the setting.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .api import IGNORED, SETTINGS_DEFAULTS
from .logic.record import MAX_ENTRIES, Entry, Record

LOG = logging.getLogger("hunch_app.store")

DEFAULTS = dict(SETTINGS_DEFAULTS)
SETTINGS_FILE = "settings.json"
RECORD_FILE = "record.json"
ASSISTANTS_FILE = "assistants.json"
_AGENT = re.compile(r"conversation\.[a-z0-9_]{1,200}")
_AREA = re.compile(r"[^/\\]{1,200}")   # a room's name or its area id
# What the owner is told when a file could not be read and was set aside (`Store.notices`, the page's state).
NOTICES = {
    "settings": "Your settings could not be read, so Hunch started with the usual ones. The file was kept aside, "
                "so nothing is lost.",
    "record": "The record could not be read, so Hunch started a new one. The file was kept aside, so nothing is "
              "lost.",
    "assistants": "Hunch could not read which assistants it set up, so Stop using Hunch cannot put them back. To "
                  "put one back, choose its agent in Home Assistant, under Settings, Voice assistants. The file "
                  "was kept aside.",
}
# Damaged files kept aside, of each kind, at most. The app never deletes one.
MAX_SET_ASIDE = 10
# What the owner is told when a damaged file could not be kept aside, and is left as it is (`held`).
WHAT = {
    "settings": {"what": "Your settings", "it": "them"},
    "record": {"what": "The record", "it": "it"},
    "assistants": {"what": "Which assistants Hunch set up", "it": "that"},
    "taught": {"what": "What you have taught Hunch", "it": "it"},
}
HELD_FULL = ("{what} could not be read, and the app's folder already holds ten damaged copies of {it}, so Hunch "
             "has not kept another. The damaged file is left as it is, and Hunch saves no changes to {it} until "
             "you remove some of those copies from the app's folder. Nothing is lost.")
HELD = ("{what} could not be read, and Hunch could not move the file aside. It is left as it is, and Hunch saves "
        "no changes to {it} until it can. Nothing is lost.")
_LATER = re.compile(r"(?:(\d{4}-\d{2}-\d{2})-)?(\d+)")
# What the app keeps beside each of the rules' entries.
EXTRA_KEYS = ("source", "outcome", "label", "entities", "ms", "reply", "note")
# What the app keeps beside the rules' record about the counts' deletion: when one last went through, and when
# one last failed (seconds since 1970, or None). Only for the page; the rules' `delete_owed` is what decides.
SHARING = {"deleted_at": None, "delete_tried_at": None}


def check_settings(changes: object) -> tuple[dict, dict]:
    """The changes as they will be saved, and the refused ones with a short reason each (`api.plain` turns a
    reason into the sentence the owner reads; the copied lock and door tests check these reasons). Unknown names
    are refused.

    Agent ids are checked for shape here; that the agent exists and is not Hunch's own is checked by the app,
    which knows the agents, as is that the microphone's room is in the home."""
    if not isinstance(changes, Mapping):
        return {}, {"settings": "not a set of settings"}
    clean, errors = {}, {}
    for name, value in changes.items():
        if name not in DEFAULTS:
            errors[str(name)] = "no such setting"
        elif name in {"locks_and_doors", "keep_record", "share_counts", "listening"}:
            if not isinstance(value, bool):
                errors[name] = "not on or off"
            else:
                clean[name] = value
        elif name == "pass_to":
            if not isinstance(value, str) or not _AGENT.fullmatch(value):
                errors[name] = "not an agent"
            else:
                clean[name] = value
        elif name == "mic_room":
            if value is not None and (not isinstance(value, str) or not _AREA.fullmatch(value.strip())):
                errors[name] = "not a room"
            else:
                clean[name] = value.strip() if isinstance(value, str) else None
    return (clean, {}) if not errors else ({}, errors)


def write_atomic(path: Path, data: object) -> None:
    """Write JSON to `path` through a temporary file renamed over it, flushed to disk first."""
    part = path.with_name(path.name + ".part")
    with part.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, path)


def held_notice(path: Path, what: str) -> str:
    """What the owner reads when the damaged file `path` is left where it is."""
    full = len(set_aside_files(path)) >= MAX_SET_ASIDE
    return (HELD_FULL if full else HELD).format(**WHAT[what])


class NotSaved(OSError):
    """A file of the app's own was not saved: it could not be written, or it is a damaged one left as it is."""


class Unreadable(Exception):
    """A file of the app's own could not be read. `kept`: it was set aside; otherwise it is left where it is."""

    def __init__(self, name: str, kept: bool) -> None:
        super().__init__(name)
        self.kept = kept


def set_aside_files(path: Path) -> list[Path]:
    """Every damaged copy of `path` kept aside, oldest first: `<name>.unreadable`, then by day and counter."""
    later = []
    for found in path.parent.glob(glob.escape(path.name) + ".unreadable-*"):
        match = _LATER.fullmatch(found.name[len(path.name) + len(".unreadable-"):])
        if match:
            later.append(((match.group(1) or "", int(match.group(2))), found))
    first = path.with_name(path.name + ".unreadable")
    return ([first] if first.exists() or first.is_symlink() else []) + [found for _, found in sorted(later)]


def _move(source: Path, target: Path) -> None:
    """Move `source` to `target`, never replacing anything already at `target` (`FileExistsError`)."""
    try:
        os.link(source, target)   # fails when the name is taken: nothing is ever replaced
    except FileExistsError:
        raise
    except OSError:   # a folder in the file's place, or a file system without links
        if target.exists() or target.is_symlink():
            raise FileExistsError(target) from None
        os.rename(source, target)
        return
    os.unlink(source)


def set_aside(path: Path, *, copy: bool = False) -> Path | None:
    """Keep the damaged file `path` under a name of its own, never replacing another (with `copy`, keep a copy and
    leave the file). Returns where it went, or None when it was not kept: `MAX_SET_ASIDE` are kept already, or
    it could not be moved. Then the caller leaves the file as it is and saves nothing over it."""
    kept = set_aside_files(path)
    if len(kept) >= MAX_SET_ASIDE:
        LOG.warning("%s could not be read, and %d damaged copies are kept already; it is left as it is",
                    path.name, len(kept))
        return None
    day = datetime.now(UTC).date().isoformat()
    first = path.with_name(path.name + ".unreadable")
    names = [first] if not kept else []
    names += [path.with_name(f"{path.name}.unreadable-{day}-{n}") for n in range(len(kept) + 1, len(kept) + 100)]
    for target in names:
        try:
            if copy:
                with target.open("xb") as handle:   # "x": never over a file already there
                    handle.write(path.read_bytes())
            else:
                _move(path, target)
        except FileExistsError:
            continue
        except OSError:
            break
        LOG.warning("%s could not be read; it was kept aside as %s", path.name, target.name)
        return target
    LOG.warning("%s could not be read, and could not be kept aside; it is left as it is", path.name)
    return None


def _read(path: Path) -> object | None:
    """The JSON object in `path`; None when there is no file. Raises `Unreadable`, once the file is set aside
    (not deleted), when it cannot be read or is not a JSON object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):   # no permission, a folder in its place, not UTF-8, not JSON
        data = Unreadable
    if not isinstance(data, Mapping):
        raise Unreadable(path.name, set_aside(path) is not None)
    return data


class Store:
    """The settings and the record, loaded once and saved after each change."""

    def __init__(self, folder: Path | str, clock: Callable[[], float] = time.time) -> None:
        self.folder = Path(folder)
        self.clock = clock
        self.settings: dict = dict(DEFAULTS)
        self.record = Record()
        self.extras: dict[str, dict] = {}
        self.assistants: dict[str, dict] = {}   # pipeline id -> what the set-up changed
        self.pass_to_chosen = False             # the owner chose the agent to pass on to themselves
        self.notices: list[str] = []            # what could not be read at start, for the owner (`NOTICES`)
        self.sharing: dict = dict(SHARING)      # the counts' deletion as the page shows it (`counts.py`)
        self.held: set[str] = set()             # damaged files left where they are: never written over
        self._lock = asyncio.Lock()

    # Loading.

    def load(self) -> None:
        """Read both files (call once, before the event loop is busy). Missing files mean defaults."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self.notices = []
        self.held = set()
        stored = self._read(SETTINGS_FILE, "settings")
        if isinstance(stored, Mapping):
            # Each stored value is checked as a change would be; one that fails is its default again.
            for name in DEFAULTS:
                if name in stored:
                    clean, _ = check_settings({name: stored[name]})
                    self.settings.update(clean)
        data = self._read(RECORD_FILE, "record")
        rules = data.get("record") if isinstance(data, Mapping) else None
        try:
            self.record = Record.from_json(rules, self.clock())
        except Exception:  # noqa: BLE001 - JSON the rules cannot take: kept aside, never a reason not to start
            self._unreadable(RECORD_FILE, "record", set_aside(self.folder / RECORD_FILE) is not None)
            self.record, data = Record(), None
        sharing = data.get("sharing") if isinstance(data, Mapping) else None
        self.sharing = dict(SHARING)
        if isinstance(sharing, Mapping):
            self.sharing.update({key: float(sharing[key]) for key in SHARING
                                 if isinstance(sharing.get(key), int | float) and not isinstance(sharing[key], bool)})
        extras = data.get("turns") if isinstance(data, Mapping) else None
        if isinstance(extras, Mapping):
            known = {entry.id for entry in self.record.entries}
            self.extras = {key: {k: value.get(k) for k in EXTRA_KEYS} for key, value in extras.items()
                           if key in known and isinstance(value, Mapping)}
        setup = self._read(ASSISTANTS_FILE, "assistants")
        if isinstance(setup, Mapping):
            kept = setup.get("assistants")
            self.assistants = {str(key): dict(value) for key, value in (kept or {}).items()
                               if isinstance(value, Mapping) and isinstance(value.get("conversation_engine"), str)
                               } if isinstance(kept, Mapping) else {}
            self.pass_to_chosen = setup.get("pass_to_chosen") is True

    def _read(self, name: str, what: str) -> object | None:
        try:
            return _read(self.folder / name)
        except Unreadable as error:
            self._unreadable(name, what, error.kept)
            return None

    def _unreadable(self, name: str, what: str, kept: bool) -> None:
        if kept:
            self.notices.append(NOTICES[what])
            return
        self.held.add(name)
        self.notices.append(held_notice(self.folder / name, what))

    def _write(self, name: str, data: object) -> None:
        """Write one of the app's files, unless a damaged one is being left where it is."""
        if name in self.held:
            LOG.debug("%s is left as it is; not saved", name)
            return
        write_atomic(self.folder / name, data)

    # Settings.

    async def save_settings(self, clean: Mapping) -> dict:
        """Apply changes already checked by `check_settings`, and save. Returns all settings."""
        async with self._lock:
            self.settings.update(clean)
            snapshot = dict(self.settings)
            await asyncio.to_thread(self._write, SETTINGS_FILE, snapshot)
        return dict(self.settings)

    # What the one-button set-up changed.

    async def save_assistants(self) -> None:
        async with self._lock:
            data = {"assistants": {key: dict(value) for key, value in self.assistants.items()},
                    "pass_to_chosen": self.pass_to_chosen}
            await asyncio.to_thread(self._write, ASSISTANTS_FILE, data)

    async def remember_assistant(self, pipeline_id: str, before: Mapping) -> None:
        """Remember what an assistant had before, saved. Raises `NotSaved`, with nothing new remembered, when it
        cannot be saved (the file cannot be written, or a damaged one is left as it is): a set-up must not go
        ahead then, since after a restart nothing could put the assistant back."""
        previous = self.assistants.get(pipeline_id)
        self.assistants[pipeline_id] = dict(before)
        try:
            if ASSISTANTS_FILE in self.held:
                raise NotSaved(ASSISTANTS_FILE)
            await self.save_assistants()
        except OSError as error:
            if previous is None:
                self.assistants.pop(pipeline_id, None)
            else:
                self.assistants[pipeline_id] = previous
            raise NotSaved(ASSISTANTS_FILE) from error

    async def forget_assistant(self, pipeline_id: str) -> None:
        if self.assistants.pop(pipeline_id, None) is not None:
            await self.save_assistants()

    # The record.

    def keeps(self, source: str, outcome: str) -> bool:
        """Whether a turn is written at all: never an ignored sentence, and nothing while the record is
        switched off."""
        if outcome == IGNORED:
            return False
        return self.settings.get("keep_record", True) is True

    def add(self, entry: Entry, extra: Mapping) -> bool:
        """Add a turn (the rules' entry and the app's part), if it is kept. True when it was added."""
        if not self.keeps(str(extra.get("source")), str(extra.get("outcome"))):
            return False
        self.record.add(entry, self.clock())
        self.extras[entry.id] = {key: extra.get(key) for key in EXTRA_KEYS}
        self._prune_extras()
        return True

    def _prune_extras(self) -> None:
        if len(self.extras) > len(self.record.entries) or len(self.extras) > MAX_ENTRIES:
            known = {entry.id for entry in self.record.entries}
            self.extras = {key: value for key, value in self.extras.items() if key in known}

    def set_undone(self, entry_id: str, undone: str, by: str = "", entity_id: str = "") -> bool:
        return self.record.set_undone(entry_id, undone, by, entity_id)

    def clear(self) -> None:
        self.record.clear()
        self.extras = {}

    def entry(self, entry_id: str) -> Entry | None:
        return self.record.get(entry_id)

    def turns(self, limit: int) -> list[tuple[Entry, dict]]:
        """The newest `limit` turns, newest first, each with the app's part."""
        self.record.prune(self.clock())
        chosen = self.record.entries[-limit:] if limit > 0 else []
        return [(entry, self.extras.get(entry.id, {})) for entry in reversed(chosen)]

    def stop_watching(self) -> None:
        """Every entry still being watched becomes "not known" (a stop cut the watch short)."""
        from .logic.record import WATCHING
        from .logic.undo import NOT_KNOWN

        self.record.entries = [replace(entry, undone=NOT_KNOWN) if entry.undone == WATCHING else entry
                               for entry in self.record.entries]

    async def save_record(self) -> None:
        """Write the record now (in a worker thread; the data is copied first)."""
        async with self._lock:
            data = {"record": self.record.to_json(),
                    "turns": {key: dict(value) for key, value in self.extras.items()},
                    "sharing": dict(self.sharing)}
            await asyncio.to_thread(self._write, RECORD_FILE, data)
