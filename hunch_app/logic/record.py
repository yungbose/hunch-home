"""The local record: what the plug-in did with each sentence, kept in the user's own Home Assistant.

It never leaves the machine. The glue keeps it with Home Assistant's own storage helper (a private JSON file
under `.storage`), shows it whole through the `hunch_home.show_record` action, and puts a version without the
sentences, the hand-backs' details and the entity IDs in the diagnostics file (`diagnostics_view`), which is
made to be posted in bug reports. The daily counts (`summary.py`) are computed from it.

Each entry holds: when; the sentence; Hunch's action, kind and score (its similarity) and the model's version
when the server gives one; what the plug-in did (the plan's intents and entity IDs, or the reason it handed
the sentence back, with the reason's detail); whether Home Assistant reported an error; and whether the plan
was undone (`undo.py`).

Bounded: at most `MAX_ENTRIES` entries and none older than `MAX_AGE`; the oldest go first. A thousand entries
of about 400 bytes each keep the file near 400 KB, small enough for Home Assistant's JSON storage helper,
which rewrites the whole file on each save, and for a diagnostics download; thirty days cover a month of
ordinary use for looking back, and more than the one day the summary needs. A household giving a hundred
commands a day reaches the cap in ten days, and from then on keeps its latest thousand.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from .client import Answer, HandBack
from .decide import Plan
from .undo import NO, NOT_KNOWN, YES

MAX_ENTRIES = 1000
MAX_AGE = 30 * 24 * 3600  # seconds
DIAGNOSTICS_ENTRIES = 50  # the newest entries shown, redacted, in the diagnostics file
REDACTED = "**REDACTED**"  # Home Assistant's own mark for redacted diagnostics values

# What the plug-in did.
CARRIED_OUT = "carried out"
HANDED_BACK = "handed back"
ERROR = "error"  # Home Assistant reported an error carrying out the plan; the sentence was then handed back
HA_FIRST = "answered by Home Assistant first"  # Home Assistant's own exact sentences answered; Hunch not asked
OUTCOMES = (CARRIED_OUT, HANDED_BACK, ERROR, HA_FIRST)

# Whether it was undone: YES, NO and NOT_KNOWN from `undo`, and these two.
WATCHING = "watching"              # within the window: not yet decided
NOT_APPLICABLE = "not applicable"  # nothing was carried out
UNDONE = (YES, NO, NOT_KNOWN, WATCHING, NOT_APPLICABLE)

_HEX = re.compile(r"[0-9a-f]{32}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Entry:
    """One sentence and what became of it."""

    id: str
    at: float  # seconds since 1970, UTC
    sentence: str
    action: str | None = None
    kind: str | None = None
    score: float | None = None
    declined: bool | None = None
    model_version: str | None = None
    outcome: str = HANDED_BACK
    reason: str = ""     # why it was handed back
    detail: str = ""     # the hand-back's detail, which may name a device or a room
    intents: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    error: bool = False  # Home Assistant reported an error
    undone: str = NOT_APPLICABLE
    undone_by: str = ""
    undone_entity: str = ""


def new_id() -> str:
    return uuid.uuid4().hex


def _plain(entry: Entry) -> dict:
    """An entry as plain JSON data: lists, not tuples."""
    data = asdict(entry)
    data["intents"], data["entity_ids"] = list(entry.intents), list(entry.entity_ids)
    return data


def entry_for(sentence: str, answer: Answer | HandBack | None, result: Plan | HandBack | None, now: float,
              *, error: bool = False, plan: Plan | None = None, entry_id: str | None = None) -> Entry:
    """The entry for one sentence.

    `answer` is what the server said (None when Home Assistant's own sentences answered first); `result` is
    the plan carried out, or why the sentence was handed back (None when Home Assistant answered first).
    `error` with `plan`: Home Assistant reported an error carrying out that plan."""
    entry = Entry(entry_id or new_id(), now, sentence)
    if isinstance(answer, Answer):
        entry = replace(entry, action=answer.action, kind=answer.kind, score=answer.similarity,
                        declined=answer.declined, model_version=answer.version)
    done = result if isinstance(result, Plan) else plan
    if done is not None:
        intents = tuple(dict.fromkeys(intent for intent, _ in done.ha_calls()))
        entry = replace(entry, intents=intents, entity_ids=tuple(done.entity_ids))
    if result is None and answer is None:
        return replace(entry, outcome=HA_FIRST)
    if isinstance(result, Plan) and not error:
        return replace(entry, outcome=CARRIED_OUT, undone=WATCHING)
    why = result if isinstance(result, HandBack) else HandBack("not carried out")
    return replace(entry, outcome=ERROR if error else HANDED_BACK, reason=why.reason, detail=why.detail,
                   error=error)


def _entry_from(data: object) -> Entry | None:
    """An entry read back from storage, or None when it is not one (a damaged or hand-edited file)."""
    if not isinstance(data, Mapping):
        return None
    try:
        entry = Entry(
            id=str(data["id"]), at=float(data["at"]), sentence=str(data["sentence"]),
            action=_text(data.get("action")), kind=_text(data.get("kind")), score=_score(data.get("score")),
            declined=data.get("declined") if isinstance(data.get("declined"), bool) else None,
            model_version=_text(data.get("model_version")),
            outcome=str(data.get("outcome", HANDED_BACK)), reason=str(data.get("reason", "")),
            detail=str(data.get("detail", "")),
            intents=tuple(str(item) for item in data.get("intents") or ()),
            entity_ids=tuple(str(item) for item in data.get("entity_ids") or ()),
            error=data.get("error") is True, undone=str(data.get("undone", NOT_APPLICABLE)),
            undone_by=str(data.get("undone_by", "")), undone_entity=str(data.get("undone_entity", "")))
    except (KeyError, TypeError, ValueError):
        return None
    if entry.outcome not in OUTCOMES or entry.undone not in UNDONE:
        return None
    return entry


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _score(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


class Record:
    """The entries, newest last, with the install identifier made for the summary, the day last sent, the choice
    to share as last seen and whether a deletion of what was sent is owed (`share.settle`)."""

    def __init__(self, entries: Iterable[Entry] = (), install_id: str | None = None,
                 last_sent_day: str | None = None, share_chosen: bool = False, delete_owed: bool = False) -> None:
        self.entries: list[Entry] = list(entries)
        self.install_id = install_id if isinstance(install_id, str) and _HEX.fullmatch(install_id) else new_id()
        self.last_sent_day = (last_sent_day if isinstance(last_sent_day, str) and _DAY.fullmatch(last_sent_day)
                              else None)
        self.share_chosen = share_chosen is True
        self.delete_owed = delete_owed is True

    def add(self, entry: Entry, now: float) -> Entry:
        self.entries.append(entry)
        self.prune(now)
        return entry

    def prune(self, now: float) -> None:
        """Drop entries older than `MAX_AGE`, then the oldest beyond `MAX_ENTRIES`."""
        kept = sorted((entry for entry in self.entries if now - entry.at <= MAX_AGE), key=lambda e: e.at)
        self.entries = kept[-MAX_ENTRIES:]

    def get(self, entry_id: str) -> Entry | None:
        return next((entry for entry in self.entries if entry.id == entry_id), None)

    def set_undone(self, entry_id: str, undone: str, by: str = "", entity_id: str = "") -> bool:
        """Settle whether an entry still being watched was undone; False when there is no such entry."""
        for index, entry in enumerate(self.entries):
            if entry.id == entry_id and entry.undone == WATCHING:
                self.entries[index] = replace(entry, undone=undone, undone_by=by, undone_entity=entity_id)
                return True
        return False

    def clear(self) -> None:
        """Delete every entry. The install identifier, the day last sent and the sharing state are not words, and
        stay."""
        self.entries = []

    def to_json(self) -> dict:
        """Plain data for storage: new lists and dicts, safe to serialise away from the event loop."""
        return {"install_id": self.install_id, "last_sent_day": self.last_sent_day,
                "share_chosen": self.share_chosen, "delete_owed": self.delete_owed,
                "entries": [_plain(entry) for entry in self.entries]}

    @classmethod
    def from_json(cls, data: object, now: float) -> Record:
        """The record as stored, pruned. An entry still being watched when Home Assistant stopped was cut
        short, so it becomes "not known". Anything unreadable is dropped."""
        if not isinstance(data, Mapping):
            return cls()
        entries = [entry for entry in map(_entry_from, data.get("entries") or ()) if entry is not None]
        entries = [replace(entry, undone=NOT_KNOWN) if entry.undone == WATCHING else entry for entry in entries]
        record = cls(entries, data.get("install_id"), data.get("last_sent_day"), data.get("share_chosen") is True,
                     data.get("delete_owed") is True)
        record.prune(now)
        return record


def day_of(at: float) -> str:
    """The UTC day of a time, as "2026-11-19"."""
    return datetime.fromtimestamp(at, UTC).date().isoformat()


def full_view(entry: Entry) -> dict:
    """An entry as the user's own export shows it: everything, with a readable time."""
    return {"time": datetime.fromtimestamp(entry.at, UTC).isoformat(timespec="seconds"), **_plain(entry)}


def redacted_view(entry: Entry) -> dict:
    """An entry for the diagnostics file: no sentence, no detail, and each entity ID cut to its domain."""
    view = full_view(entry)
    view["sentence"] = REDACTED
    view["detail"] = REDACTED if entry.detail else ""
    view["entity_ids"] = [entity_id.split(".", 1)[0] for entity_id in entry.entity_ids]
    view["undone_entity"] = entry.undone_entity.split(".", 1)[0]
    del view["id"]
    return view


def install_reference(install_id: str) -> str:
    """A name for an install that is safe to show in public: the first 16 hexadecimal digits of the SHA-256 of
    its identifier. The identifier itself is what the receiving end takes as proof for a deletion (and for
    replacing a day's counts), so it is never shown; the reference cannot be turned back into it."""
    return hashlib.sha256(install_id.encode("ascii", "replace")).hexdigest()[:16]


def shown_sharing(sharing: Mapping[str, object], install_id: str | None) -> dict:
    """The sharing state as diagnostics show it: what would be sent, with the install identifier replaced by
    the redaction mark, and the install's public reference beside it (when the record is loaded)."""
    shown = dict(sharing)
    would = shown.get("what_would_be_sent")
    if isinstance(would, Mapping) and "install_id" in would:
        shown["what_would_be_sent"] = {**would, "install_id": REDACTED}
    if install_id:
        shown["install_reference"] = install_reference(install_id)
    return shown


def diagnostics_view(record: Record, settings: Mapping[str, object], options: Mapping[str, object],
                     sharing: Mapping[str, object]) -> dict:
    """The diagnostics file: the settings without the address or the secret, the newest entries without the
    sentences or names, and the sharing state with what would be sent (`sharing`, made by the glue), the
    install identifier left out (`shown_sharing`)."""
    loaded = sharing.get("loaded", True) is not False
    return {
        "about": ("Made to be shared in a bug report: sentences, the hand-backs' details, entity IDs (cut to "
                  "their domain), the server's address and its secret are left out. The action "
                  "hunch_home.show_record shows the whole record, sentences included, to you alone."),
        "settings": {
            "timeout": settings.get("timeout"),
            "ha_first": settings.get("ha_first"),
            "locks_and_doors": settings.get("locks_and_doors"),
            "secret_set": bool(settings.get("secret")),
            "url": REDACTED,
        },
        "record": {
            "on": options.get("record", True) is not False,
            "entries": len(record.entries),
            "most_kept": MAX_ENTRIES,
            "kept_for_days": MAX_AGE // 86400,
            "newest": [redacted_view(entry) for entry in record.entries[-DIAGNOSTICS_ENTRIES:]],
        },
        "sharing": shown_sharing(sharing, record.install_id if loaded else None),
    }
