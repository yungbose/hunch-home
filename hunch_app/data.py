"""Your data: what the page's "Your data" section shows, and its two downloads.

The plug-in gave owners three things about their own data, which the privacy notice relies on: the whole
record, shown to them alone; a diagnostics file made to be pasted into a bug report, which showed under
`sharing` exactly what the daily counts would send; and an install reference to quote when writing in. This
module gives the same from the app's page:

- `view`: the `GET api/data` reply. How much the record holds and its limits; the daily counts for yesterday
  (UTC) as they would be sent, whether sharing is on or off, as counts and as the message itself; the day last
  sent; the deletion of what was sent (none, being asked for, owed, done); and the install reference, with
  every damaged record set aside (each one meant a new install number), newest first, and the reference of the
  number in each, where it can still be read.
- `full`: the whole record, sentences, names and replies included, for the owner to keep.
- `report`: the version made for a bug report. The newest 50 turns as the rules' `redacted_view` has them (no
  sentence, the hand-back's detail left out, each entity id cut to its kind, such as `light`), with only the
  app's words that name nothing (where the sentence came from, the outcome, how long it took): never the
  label, which names the agent, the devices' names, the reply or the note. The versions, the settings that
  matter as on or off (the agent passed on to only as "Home Assistant" or "another agent", the microphone's
  room only as set or not), listening's state and the sharing state.

The install number is never in any of them: it is what deletes this home's counts. The shown message carries
`HIDDEN` in its place, and is otherwise byte for byte what `counts.py` sends (`share.request_for`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from .counts import SHARE_ENDPOINT, Counts, summary_for
from .logic import share
from .logic.record import (
    DIAGNOSTICS_ENTRIES,
    MAX_AGE,
    MAX_ENTRIES,
    day_of,
    full_view,
    install_reference,
    redacted_view,
)
from .passon import DEFAULT_AGENT
from .store import RECORD_FILE, Store, set_aside_files
from .turn import iso

FULL, REPORT = "full", "report"
NOT_KNOWN = "not known"   # where a turn came from, when its app-side details were lost
KINDS = (FULL, REPORT)
# Stands in for the install number in the message the page shows.
HIDDEN = "not shown: your install number"
_INSTALL = re.compile(rb'"install_id"\s*:\s*"([0-9a-f]{32})"')
_READ_AT_MOST = 4 * 1024 * 1024   # bytes of a set-aside record read for its install number

FULL_ABOUT = ("Everything Hunch Home keeps in its record on this machine, sentences included. It is yours: read "
              "it before you share it. The install number is left out, because it is what deletes this home's "
              "daily counts; the install reference stands for it.")
REPORT_ABOUT = ("Made to be pasted into a bug report. Left out: sentences, the names of devices, rooms and agents, "
                "replies and notes, and the install number. Entity ids are cut to their kind, such as light. The "
                f"newest {DIAGNOSTICS_ENTRIES} turns are shown.")


def _on(value: object) -> str:
    return "on" if value is True else "off"


def _at(seconds: object) -> str | None:
    return iso(seconds) if isinstance(seconds, int | float) and not isinstance(seconds, bool) else None


def shown_message(counts: Mapping) -> tuple[dict, str]:
    """The counts with the install number hidden, and the message as `share.request_for` makes it from them."""
    hidden = {**counts, "install_id": HIDDEN}
    return hidden, share.request_for(SHARE_ENDPOINT, hidden).body.decode("utf-8")


def earlier_reference(path: Path) -> str | None:
    """The install reference of the number in a set-aside record, when it can still be read there."""
    try:
        with path.open("rb") as handle:
            found = _INSTALL.search(handle.read(_READ_AT_MOST))
    except OSError:
        return None
    return install_reference(found.group(1).decode("ascii")) if found else None


def _deletion(store: Store, counts: Counts | None) -> dict:
    """What became of the deletion of the counts sent: "none" (none asked for), "asking" (being asked for just
    now), "owed" (asked for, not yet taken; retried every hour; `tried` is the last failed try) or "done"."""
    if counts is not None and counts.deleting:
        status = "asking"
    elif store.record.delete_owed:
        status = "owed"
    elif _at(store.sharing.get("deleted_at")):
        status = "done"
    else:
        status = "none"
    return {"status": status, "tried": _at(store.sharing.get("delete_tried_at")) if status == "owed" else None,
            "done": _at(store.sharing.get("deleted_at")) if status == "done" else None}


def view(store: Store, counts: Counts | None, *, now: float, endpoint: str = SHARE_ENDPOINT) -> dict:
    """The `GET api/data` reply (shapes in `web.Backend`). `counts`: the running sender, or None before it starts."""
    store.record.prune(now)
    day = day_of(now - 86400)
    made = summary_for(store, day)
    hidden, message = shown_message(made)
    earlier = [{"file": path.name, "reference": earlier_reference(path)}
               for path in reversed(set_aside_files(store.folder / RECORD_FILE))]
    return {
        "kept": {"on": store.settings.get("keep_record", True) is True, "turns": len(store.record.entries),
                 "most": MAX_ENTRIES, "days": MAX_AGE // 86400},
        "sharing": {
            "offered": share.offered(endpoint),
            "chosen": share.offered(endpoint) and store.settings.get("share_counts") is True,
            "day": day,
            "counts": hidden,
            "message": message,
            "sends": bool(made["sentences"]),
            "last_sent_day": store.record.last_sent_day,
            "deletion": _deletion(store, counts),
        },
        "install": {"reference": install_reference(store.record.install_id), "set_aside": bool(earlier),
                    "earlier": earlier},
    }


def full(store: Store, *, version: str, model_version: str | None, now: float) -> dict:
    """The whole record, newest first, for the owner: everything but the install number."""
    store.record.prune(now)
    turns = []
    for entry, extra in store.turns(MAX_ENTRIES):
        turns.append({**full_view(entry), "source": extra.get("source") or NOT_KNOWN, "outcome": extra.get("outcome"),
                      "label": extra.get("label"), "devices": list(extra.get("entities") or []),
                      "reply": extra.get("reply") or "", "note": extra.get("note") or "", "ms": extra.get("ms")})
    return {
        "about": FULL_ABOUT,
        "made": iso(now),
        "versions": {"app": version, "model": model_version},
        "record": {"on": _on(store.settings.get("keep_record", True)), "kept": len(turns), "most_kept": MAX_ENTRIES,
                   "kept_for_days": MAX_AGE // 86400, "turns": turns},
        "install_reference": install_reference(store.record.install_id),
    }


def _redacted(entry, extra: Mapping) -> dict:
    shown = redacted_view(entry)
    shown.update(source=extra.get("source") or NOT_KNOWN, outcome=extra.get("outcome"), ms=extra.get("ms"))
    return shown


def report(store: Store, *, version: str, model_version: str | None, model_ready: bool, listening: Mapping,
           now: float) -> dict:
    """The version made for a bug report: nothing that names anything in the home, and no install number."""
    store.record.prune(now)
    settings = store.settings
    model = listening.get("model") if isinstance(listening.get("model"), Mapping) else {}
    return {
        "about": REPORT_ABOUT,
        "made": iso(now),
        "versions": {"app": version, "model": model_version},
        "model_ready": model_ready is True,
        "settings": {
            "locks_and_doors": _on(settings.get("locks_and_doors")),
            "keep_record": _on(settings.get("keep_record")),
            "share_counts": _on(settings.get("share_counts")),
            "listening": _on(settings.get("listening")),
            "passes_on_to": "Home Assistant" if settings.get("pass_to") == DEFAULT_AGENT else "another agent",
            "microphone_room": "set" if settings.get("mic_room") else "not set",
        },
        "listening": {"status": str(listening.get("status") or "off"),
                      "microphone_found": listening.get("available") is True,
                      "speech_model_ready": model.get("ready") is True},
        "record": {"on": _on(settings.get("keep_record", True)), "kept": len(store.record.entries),
                   "most_kept": MAX_ENTRIES, "kept_for_days": MAX_AGE // 86400,
                   "newest": [_redacted(entry, extra) for entry, extra in store.turns(DIAGNOSTICS_ENTRIES)]},
        "sharing": {"on": _on(settings.get("share_counts")), "deletion_owed": store.record.delete_owed,
                    "last_sent_day": store.record.last_sent_day,
                    "install_reference": install_reference(store.record.install_id)},
    }


def download(store: Store, kind: str, *, version: str, model_version: str | None, model_ready: bool,
             listening: Mapping, now: float) -> dict:
    """One of the two downloads; `ValueError` (plain words) for any other kind."""
    if kind == FULL:
        return full(store, version=version, model_version=model_version, now=now)
    if kind == REPORT:
        return report(store, version=version, model_version=model_version, model_ready=model_ready,
                      listening=listening, now=now)
    raise ValueError("Hunch cannot make that download.")
