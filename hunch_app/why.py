"""Why Hunch would pass a sentence on, in one plain sentence for the owner (the try box's `why`). A sentence Hunch
would answer itself ("Hunch would do nothing") says `hold.WHY` instead.

Every reason a hand-back can carry (the rules' own, the model's client's, the turn's and `unsure.py`'s; the daily
counts know them all as `logic.summary.KNOWN_REASONS`) has one sentence here; a test checks none is left out. A
note from `unsure.py` is said in its own words, and Locks and doors switched off is said as itself.
"""

from __future__ import annotations

from .logic.client import REFUSED, HandBack
from .logic.decide import ENTRY_REASON
from .unsure import CHECK_FAILED_NOTE, CONTRADICTION_NOTE, LATER_NOTE, NUMBER_NOTE

PLAIN = {
    "server down": "Hunch's model did not answer.",
    "server slow": "Hunch's model took too long to answer.",
    "server error": "Hunch's model is not ready yet.",
    REFUSED: "Hunch's model did not answer.",
    "bad reply": "Hunch's answer could not be read.",
    "declined": "Hunch was not sure enough what you meant.",
    "not for me": "Hunch did not take it for a home command.",
    "no intent": "Home Assistant has no way to do what Hunch understood.",
    "the sentence and the answer disagree": "Hunch's reading did not match the words.",
    "delayed command": "Hunch cannot act at a later time.",
    "ambiguous": "More than one device or room fits, so Hunch did not guess.",
    "needs a value": "Hunch could not read the number or setting the sentence needs.",
    "no match": "Nothing Hunch can find matches what the sentence names.",
    "no device": "The sentence names no device Hunch can find.",
    "no room": "The sentence names no device or room, and Hunch could not tell which room it came from.",
    "already off": "Every light it found is already off, so there is nothing to dim.",
    "not carried out": "Home Assistant could not carry it out.",
    ENTRY_REASON: "Hunch moves a lock, door or gate only when the sentence names it.",
}
NOTES = {
    NUMBER_NOTE: "Hunch could not be sure of the number in this sentence.",
    LATER_NOTE: "Hunch cannot act at a later time.",
    CONTRADICTION_NOTE: "Hunch's reading did not match the words.",
    CHECK_FAILED_NOTE: "Hunch could not finish checking this sentence.",
}
NEGATED = "The sentence holds a \"don't\", an \"if\" or more than one command, so Hunch did not guess."
LOCKS_OFF = "Locks and doors is off, so Hunch leaves locks and doors to {agent}."
OTHER = "Hunch was not sure, so it did not act."
_ENTRY_ACTIONS = frozenset({"unlock", "open", "close"})


def plain(result: HandBack, locks_and_doors: bool, agent: str, answer: object = None) -> str:
    """One plain sentence for why `result` passes the sentence on."""
    if result.detail in NOTES:
        return NOTES[result.detail]
    action = getattr(answer, "action", None)
    kind = getattr(answer, "kind", None)
    if not locks_and_doors and action in _ENTRY_ACTIONS and (
            (kind == "Security" and result.reason in {"no intent", ENTRY_REASON})
            or (kind == "Climate" and "not a known window covering" in (result.detail or ""))):
        return LOCKS_OFF.format(agent=agent)
    if result.reason == "ambiguous" and any(word in (result.detail or "") for word in ("negation", "condition")):
        return NEGATED
    return PLAIN.get(result.reason, OTHER)
