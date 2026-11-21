"""The daily counts: what sharing would send, computed from the local record, with no words in it.

One day (UTC) of the record becomes counts only: sentences by action and kind, by what the plug-in did and
why it handed back, by whether Home Assistant reported an error and whether the plan was undone, a coarse
histogram of Hunch's scores, the plug-in's and the model's versions, and a random install identifier made for
this alone (`record.Record.install_id`).

Never a sentence, a device or entity name, an entity ID, a room, a hand-back's detail, or anything a person
typed. That is kept by construction, not by filtering: every string in the summary is either one of the fixed
words listed here (an action or kind from the model's own lists, an outcome, a known hand-back reason, a
verdict, a score bin) or a value checked against a strict pattern (the day, the hexadecimal install
identifier, a version number). Anything else is counted as "other". `check` proves it again before sending.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping

from .client import NO_KIND, NOT_FOR_ME, REFUSED, VERSION
from .decide import ENTRY_REASON
from .record import OUTCOMES, UNDONE, Entry, day_of
from .table import ACTIONS, ACTIONS_1_1, KINDS

SCHEMA = 1
OTHER = "other"
UNKNOWN = "unknown"
KNOWN_ACTIONS = frozenset({*ACTIONS, *ACTIONS_1_1, NOT_FOR_ME})
KNOWN_KINDS = frozenset({*KINDS, NO_KIND})
# Every reason the plug-in hands a sentence back for (`HandBack(reason, ...)` in `hunch_logic` and the glue).
# A test reads the source and checks that none is missing.
KNOWN_REASONS = frozenset({
    "server down", "server slow", "server error", "bad reply", REFUSED, "declined", "not for me", "no intent",
    "the sentence and the answer disagree", "delayed command", "ambiguous", "needs a value", "no match",
    "no device", "no room", "already off", "not carried out", ENTRY_REASON,
})
# Scores (Hunch's similarity) in tenths from 0.5 up; lower is one bin, and no score is "none".
SCORE_BINS = ("below 0.5", "0.5 to 0.6", "0.6 to 0.7", "0.7 to 0.8", "0.8 to 0.9", "0.9 and above", "none")
KEYS = ("schema", "day", "install_id", "plugin_version", "model_versions", "sentences", "by_action_and_kind",
        "by_outcome", "handed_back_by_reason", "errors", "undone", "scores")

_HEX = re.compile(r"[0-9a-f]{32}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def score_bin(score: float | None) -> str:
    if score is None:
        return "none"
    if score < 0.5:
        return SCORE_BINS[0]
    return SCORE_BINS[min(5, int((score - 0.5) * 10 + 1e-9) + 1)]


def _version(value: str | None) -> str:
    return value if isinstance(value, str) and VERSION.fullmatch(value) else UNKNOWN


def _known(value: str | None, known: frozenset[str]) -> str:
    return value if value in known else OTHER


def summarise(entries: Iterable[Entry], day: str, install_id: str, plugin_version: str) -> dict:
    """The counts for one UTC day of the record ("2026-11-19")."""
    if not _DAY.fullmatch(day) or not _HEX.fullmatch(install_id):
        raise ValueError("a summary needs a day and a hexadecimal install identifier")
    chosen = [entry for entry in entries if day_of(entry.at) == day]
    pairs = Counter(f"{_known(e.action, KNOWN_ACTIONS)} / {_known(e.kind, KNOWN_KINDS)}"
                    for e in chosen if e.action is not None)
    return {
        "schema": SCHEMA,
        "day": day,
        "install_id": install_id,
        "plugin_version": _version(plugin_version),
        "model_versions": dict(Counter(_version(e.model_version) for e in chosen if e.action is not None)),
        "sentences": len(chosen),
        "by_action_and_kind": dict(sorted(pairs.items())),
        "by_outcome": dict(Counter(_known(e.outcome, frozenset(OUTCOMES)) for e in chosen)),
        "handed_back_by_reason": dict(Counter(_known(e.reason, KNOWN_REASONS) for e in chosen if e.reason)),
        "errors": sum(1 for e in chosen if e.error),
        "undone": dict(Counter(_known(e.undone, frozenset(UNDONE)) for e in chosen)),
        "scores": dict(Counter(score_bin(e.score) for e in chosen if e.action is not None)),
    }


def _words() -> frozenset[str]:
    pairs = {f"{action} / {kind}" for action in (*KNOWN_ACTIONS, OTHER) for kind in (*KNOWN_KINDS, OTHER)}
    return frozenset({*pairs, *OUTCOMES, *KNOWN_REASONS, *UNDONE, *SCORE_BINS, OTHER, UNKNOWN})


WORDS = _words()


def _fine(text: str) -> bool:
    return (text in WORDS or text in KEYS or bool(_DAY.fullmatch(text)) or bool(_HEX.fullmatch(text))
            or bool(VERSION.fullmatch(text)))


def check(summary: Mapping[str, object]) -> bool:
    """Whether a summary is exactly the shape `summarise` makes: its keys, whole numbers, and only strings
    that are fixed words or match the day, identifier and version patterns. The sender sends nothing else."""
    if not isinstance(summary, Mapping) or tuple(summary) != KEYS:
        return False

    def fine(value: object) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value >= 0
        if isinstance(value, str):
            return _fine(value)
        if isinstance(value, Mapping):
            return all(isinstance(key, str) and _fine(key) and fine(item) for key, item in value.items())
        return False

    return all(fine(value) for value in summary.values())
