"""The one rule (21 Nov 2026: "just keep it simple"): if Hunch reads a sentence as a home command of a kind
it carries out, Hunch handles it. It does it, or it says in one short line why not, and nothing is passed on.
Everything else goes to the person's own assistant, whichever agent that is.

Passed on before, the sentences Hunch stops at were either carried out wrongly by another agent (in the test
machine, 20 Nov 2026, Home Assistant's own agent locked the door for "no need to lock the front door", set 1% for
"1,000 percent" and switched off at once for "in the morning"), or met with "Sorry, I couldn't understand that",
which tells the person nothing: Home Assistant had already had its own go at the sentence before Hunch saw it.

`held` says which of `REPLIES` a hand-back stays with Hunch for, or None when it is passed on:

| Why Hunch stops | Reply |
|---|---|
| no device by that name ("no device", "no match"); more than one device fits; a name shared with a room | `DEVICE` |
| more than one room named | `ROOM` |
| no device or room it knows named, and no room to look in ("no room": an unknown name and an unknown room look the same to the rules) | `DEVICE_OR_ROOM` |
| a later time | `LATER` |
| a plain refusal of the very command it read ("don't turn off the reading light", "no need to lock the front door"): nothing to do, and nothing done | `OKAY` |
| a number it cannot be sure of, any other "don't" or "if", words against its reading, its own checks failing, nothing to dim | `UNSURE` |
| Locks and doors switched off, for a lock or door command | `LOCKS` |
| Home Assistant refused or failed the action | `FAILED`, or `unavailable` naming the devices when that is why |

Still passed on: Hunch declined, or took it for "not for me"; the model's answer is a question ("ask"); a kind or
action the rules have no way to do (a relative change of heating or volume, a named timer, a search to play, a
setting with no number, a device that cannot dim or does not say how bright it is, more than one command in a
sentence, an action or kind pair with no row); Hunch's own time running out, or its model failing or giving a reply
that cannot be read (the other agent is the fallback). `SIDES` says which side each reason falls on.

From listening nothing changes: a sentence not acted on is ignored and nothing is kept (`turn.py`).

The replies never name a rule, a word list, a score or a check. The page shows `SHORT` as the headline of a
"Hunch did nothing" turn, and the try box says `WHY`.
"""

from __future__ import annotations

import re

from . import unsure
from .api import NOTHING
from .logic import speech
from .logic.client import REFUSED, Answer, HandBack
from .logic.decide import ENTRY_REASON

DEVICE, ROOM, DEVICE_OR_ROOM = "device", "room", "device or room"
LATER, UNSURE, LOCKS, FAILED, OKAY = "later", "unsure", "locks", "failed", "okay"
REPLIES = {
    DEVICE: "Sorry, I couldn't work out which device you meant.",
    ROOM: "Sorry, I couldn't work out which room you meant.",
    DEVICE_OR_ROOM: "Sorry, I couldn't work out which device or room you meant.",
    LATER: "Sorry, I can't do things at a later time yet.",
    UNSURE: "Sorry, I wasn't sure what you meant, so I did nothing.",
    LOCKS: "Sorry, locks and doors are switched off for me.",
    FAILED: "Sorry, that didn't work.",
    OKAY: "Okay.",
}
# The same, as a headline under "Hunch did nothing" on the page.
SHORT = {
    DEVICE: "Couldn't work out which device you meant",
    ROOM: "Couldn't work out which room you meant",
    DEVICE_OR_ROOM: "Couldn't work out which device or room you meant",
    LATER: "Can't do things at a later time yet",
    UNSURE: "Wasn't sure what you meant",
    LOCKS: "Locks and doors are switched off",
    FAILED: "That didn't work",
    OKAY: "Left it as it is",
}
# The same, in the try box, where nothing is carried out.
WHY = {
    DEVICE: "Hunch would do nothing: it couldn't work out which device you meant.",
    ROOM: "Hunch would do nothing: it couldn't work out which room you meant.",
    DEVICE_OR_ROOM: "Hunch would do nothing: it couldn't work out which device or room you meant.",
    LATER: "Hunch would do nothing: it can't do things at a later time yet.",
    UNSURE: "Hunch would do nothing: it wasn't sure what you meant.",
    LOCKS: "Hunch would do nothing: locks and doors are switched off for it.",
    FAILED: "Hunch would do nothing: Home Assistant could not do it.",
    OKAY: "Hunch would do nothing: you asked it not to.",
}
_SHORT_BY_REPLY = {REPLIES[key]: SHORT[key] for key in REPLIES}
# Home Assistant's failure was that the devices are not available: the reply names them (`unavailable`).
_UNAVAILABLE = re.compile(r"Sorry, (.+ (?:isn't|aren't) available just now)\.")

# Every reason the rules and the checks give (the daily counts' `KNOWN_REASONS`), and which side it falls on:
# "held" (always, for an answer that is not a question), "passed" (always), or "depends" (said in `held`).
SIDES = {
    "the sentence and the answer disagree": "held",
    "delayed command": "held",             # but a question ("what's the weather tomorrow") is passed on
    "no device": "held",
    "no match": "held",
    "no room": "held",
    "already off": "held",
    "not carried out": "held",             # Home Assistant failed it, or the checks did not finish
    ENTRY_REASON: "held",                  # only with Locks and doors off
    "ambiguous": "depends",                # passed for more than one command in a sentence
    "needs a value": "depends",            # held for a number it cannot be sure of; passed for no way to do it
    "no intent": "depends",                # held for a lock or door with Locks and doors off; else passed
    "declined": "passed",
    "not for me": "passed",
    "server down": "passed",
    "server slow": "passed",
    "server error": "passed",
    "bad reply": "passed",
    REFUSED: "passed",
}
# The values the rules read as numbers: a sentence that holds a number and still has none read is a guess.
NUMERIC = frozenset({"brightness", "temperature", "volume", "volume up", "volume down", "duration"})
_NO_VALUE = re.compile(r"\S+ needs an? (.+), and none could be read")
_BARE_LEVEL = "raising or lowering brightness needs an explicit absolute setting"
_TIMER = "only a simple unnamed timer is supported"
_NOT_A_WINDOW_COVERING = "a named cover is not a known window covering"
_FRACTION = re.compile(r"\d\s*[.,/]\s*\d")
_NEGATIONS = frozenset({"not", "don", "dont", "never", "unless", "if", "except", "instead"})
_ENTRY_ACTIONS = frozenset({"unlock", "open", "close"})
_A_NUMBER = re.compile(r"\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
                       r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
                       r"seventy|eighty|ninety|hundred|minus|half|quarter)\b", re.IGNORECASE)


# A plain refusal (`refused`): what may come first, the refusal itself, and what it is followed by. Narrow on
# purpose: anything else keeps the "wasn't sure" line, since "Okay." would claim the app did what was asked.
_LEAD_IN = re.compile(r"^(?:(?:please|hey|ok|okay|oh)\b[\s,]*|no\s*,\s*)*")   # "no, don't"; "no need to" stays
_REFUSAL = re.compile(r"^(?:please\s+)?(?:don'?t|do\s+not|no\s+need\s+to|never\s+mind)\b\s*")
_COMMAND_VERBS = frozenset({"turn", "switch", "put", "power", "lock", "unlock", "open", "close", "shut", "dim",
                            "brighten", "raise", "lower", "set", "start", "add", "remove", "play", "pause",
                            "change", "make", "run", "heat", "cool", "boil", "announce"})
_THINGS = frozenset({"the", "my", "our", "that", "this", "those", "these"})   # "never mind the lamp"
# A second clause, a condition or a question: the sentence asks for something besides "don't".
_MORE = re.compile(r"[,;:?!]|\s[-–]\s|\b(?:and|or|but|then|instead|unless|if|except|until|when|while|because|"
                   r"before|after|otherwise|rather)\b")


def refused(sentence: str) -> bool:
    """Whether the sentence plainly asks for the command it names not to happen: it begins (after an optional
    "please", "hey", "ok", "no", "oh") with "don't", "do not", "no need to", "never mind" or "please don't",
    directly followed by a command verb ("don't turn off the reading light"), or, after "never mind", by the
    device ("never mind the lamp"); and nothing after it asks for anything else (no second clause, condition or
    question). "Don't forget to", "don't leave", "don't let", "why don't you", "don't you want to" and "if you
    don't" are never plain refusals."""
    text = " ".join(sentence.casefold().replace("\u2019", "'").split()).rstrip(".")
    text = text[_LEAD_IN.match(text).end():]
    opener = _REFUSAL.match(text)
    if opener is None:
        return False
    rest = text[opener.end():]
    first = rest.split(" ", 1)[0] if rest else ""
    allowed = _COMMAND_VERBS | (_THINGS if opener.group(0).startswith("never") else frozenset())
    return first in allowed and _MORE.search(rest) is None


def _words(sentence: str) -> set[str]:
    return set(re.findall(r"[a-z]+", sentence.casefold().replace("'", "")))


def unavailable(names: list[str]) -> str:
    """The reply when Home Assistant failed the action because these devices are not available."""
    verb = "isn't" if len(names) == 1 else "aren't"
    return f"Sorry, {speech.join(names)} {verb} available just now."


def short(outcome: str, reply: str) -> str:
    """The headline for a turn's reply: `SHORT` (or the devices not available) for a "Hunch did nothing" turn,
    "" for anything else."""
    if outcome != NOTHING:
        return ""
    found = _UNAVAILABLE.fullmatch(reply)
    return found.group(1) if found else _SHORT_BY_REPLY.get(reply, "")


def _locks_off(answer: Answer, reason: str, detail: str) -> bool:
    """A lock or door command that the rules have no way to do only because Locks and doors is off."""
    return answer.action in _ENTRY_ACTIONS and (
        (answer.kind == "Security" and reason == "no intent")
        or (answer.kind == "Climate" and detail == _NOT_A_WINDOW_COVERING))


def _a_number(sentence: str, detail: str) -> bool:
    """A "needs a value" hand-back for a number Hunch cannot be sure of (not for a thing it has no way to do)."""
    if detail == _BARE_LEVEL:
        return True
    if detail == _TIMER:
        return _FRACTION.search(sentence) is not None   # "a timer for 1.5 hours", not a named timer
    needs = _NO_VALUE.fullmatch(detail)
    return bool(needs and needs.group(1) in NUMERIC and _A_NUMBER.search(sentence))


def held(answer: object, result: object, sentence: str, locks_and_doors: bool = True) -> str | None:
    """Which of `REPLIES` a hand-back stays with Hunch for, or None when it is passed on."""
    if not isinstance(answer, Answer) or not isinstance(result, HandBack) or answer.action == "ask":
        return None
    reason, detail = result.reason, result.detail or ""
    if detail == unsure.CHECK_FAILED_NOTE or detail == unsure.NUMBER_NOTE:
        return UNSURE
    if detail == unsure.LATER_NOTE or reason == "delayed command":
        return LATER
    if detail == unsure.CONTRADICTION_NOTE or reason == unsure.DISAGREE:
        return OKAY if refused(sentence) else UNSURE
    if reason == "not carried out":
        return FAILED
    if reason == ENTRY_REASON or (not locks_and_doors and _locks_off(answer, reason, detail)):
        return LOCKS
    if reason == "ambiguous":
        if detail.startswith("negation"):
            if not _words(sentence) & _NEGATIONS:
                return None   # more than one command
            return OKAY if refused(sentence) else UNSURE
        return ROOM if detail == "more than one room is named" else DEVICE
    if reason == "no room":
        return DEVICE_OR_ROOM
    if reason in ("no device", "no match"):
        return DEVICE
    if reason == "already off":
        return UNSURE
    if reason == "needs a value" and _a_number(sentence, detail):
        return UNSURE
    return None
