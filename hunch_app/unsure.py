"""Sentences the rules would act on wrongly: words against Hunch's answer, a number they misread, or a later time
they do not see as one.

Every sentence passes here after the rules (`logic/`, which this does not change) have decided, typed into
Assist, spoken through Home Assistant's own speech-to-text or heard in listening mode (`turn.Turns.think`). When
the rules would act, and the sentence holds one of these forms, it is not acted on: through Assist Hunch says so
itself in one short line and passes nothing on (`hold.py`); from listening it is ignored and nothing is kept.

- **Words against the answer** (checked first): the rules' own check that the words do not contradict the
  answer matches whole words only, so "the front door needs unlocking", answered lock, was locked (seen in a
  real Home Assistant, 20 Nov 2026). Here each direction is a set of word forms: unlock (unlock, unlocks,
  unlocking, unlocked) and lock (lock, locks, locking, locked, as a verb: "the lock" is a thing); open (open,
  opens, opening, opened, open up) and close (close, closes, closing, closed, shut, shuts, shutting); on
  ("switch on", "turn on", "turn the lamp on", start, "get ... going") and off ("switch off", "turn off", "turn
  the lamp off", "off" as the last word, kill, stop); brighter (brighter, brighten, "too dark") and dimmer
  (dimmer, dim as a verb, "too bright"). A sentence holding the direction against the answer, whether or not it
  also holds the answer's own ("don't lock, unlock it"), or holding the answer's own direction negated ("don't
  unlock the door"), is not acted on. The names of what the plan acts on are set aside first (see below), so "the
  Shed Lock" or a room called "Open plan" is not a word against the answer. Its reason is the rules' own, "the
  sentence and the answer disagree", which the daily counts already have.
- **A number the rules would read as another number** (only when the action carries a number, such as a
  temperature, a brightness, a volume or a duration): "minus 2" (read as 2), "21 point 5" (as 5), "21 and a
  half" (as 21), "1,000", "1.000" or "1 000" (as 1 or 0), "2 1" (as 1), "21 plus 1" (as 1), a correction such
  as "21 degrees, no, 22" (as 21), and a range or a choice such as "21 to 23", "between 21 and 23" or "21 or
  22" (as the last).
- **A later time the rules' own delay check misses** (any action but a timer's, as the rules' check has it):
  clock times in words ("half past seven", "quarter to eight", "half 7", "at half seven"), parts of the day ("in
  the morning", "at night", "at sunset", "at lunchtime", "by morning", "first thing"), days and repeats ("on
  Monday", "next week", "this weekend", "every day", "on the hour") and rough or boundary times (about, around,
  roughly, approximately, nearly, from, until, till, after, before, since, past or by with an hour: "around 7",
  "from 7"), unless plainly an amount (`_plainly_an_amount`: "turn the speaker down by about 10", "set the lamp
  to around 10 percent").
- **Every number in digits accounted for** (`_unaccounted`, the eighth read, 20 Nov 2026): read whole with what
  is attached to it ("7o'clock", "10.5", "12,5", "10%"), each must be a value the plan sends, claimed once, with
  no clock mark and no boundary word before it, and a rough one must be plainly an amount. Anything else holds
  the sentence. A single number word is read as a time only after a rough or boundary word; two or more
  together ("seven thirty", "nineteen hundred", "ten to eight") are a clock time unless they spell a value the
  plan sends ("a timer for twenty five minutes"; ruled on 20 Nov 2026).

Every check reads only the words of the command: the names that identified what the plan acts on (its devices,
their rooms, the room it names: "the Morning Room") and the words the rules took out for the action (an announced
message, a list item, a search query: "add vitamins for every day to the shopping list") are set aside first.
Never another device's or room's name, and a name only where it occurs once: with a scene called "Night", "turn
on Night at night" keeps its "at night", since which "night" was the name cannot be told (found by the other
model family's closing read, 20 Nov 2026; the scene ran at once before).

Each hand-back uses a reason the daily counts already have: "needs a value" for a number, "delayed command" for
a time.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from .logic.client import HandBack
from .logic.decide import Plan
from .logic.match import Home

LOG = logging.getLogger("hunch_app.unsure")

# Each note is the hand-back's detail, which `hold.held` reads. A command is then answered by Hunch in one short
# line (`hold.REPLIES`); a question ("ask") is still passed on, and the note goes with it to the page.
NUMBER_NOTE = "Hunch could not be sure of the number in this sentence."
LATER_NOTE = "Hunch cannot act at a later time."
CONTRADICTION_NOTE = "Hunch's reading did not match the words."
# When a check itself fails (a registry with a shape nobody expected): the sentence is held, never acted on. Its
# reason is one the daily counts already have.
CHECK_FAILED_NOTE = "Hunch could not finish checking this sentence."
CHECK_FAILED_REASON = "not carried out"
NOTES = frozenset({NUMBER_NOTE, LATER_NOTE, CONTRADICTION_NOTE, CHECK_FAILED_NOTE})
DISAGREE = "the sentence and the answer disagree"   # the rules' own reason, known to the daily counts
# The slots that carry words from the sentence rather than the command: set aside before any check.
PAYLOAD = ("message", "item", "search_query")

# Each direction's word forms, as patterns over the sentence in lower case with its punctuation made spaces
# (apostrophes kept). "dim" counts only as a verb ("it's a bit dim in here" asks for more light), and "lock" only
# as a verb ("the lock" is a thing): `_NOT_VERBS` sets those uses aside first.
_DIRECTIONS = {
    "unlock": r"\bunlock(?:s|ing|ed)?\b",
    "lock": r"\block(?:s|ing|ed)?\b",
    "open": r"\bopen(?:s|ing|ed)?\b",
    "close": r"\b(?:close[sd]?|closing|shut|shuts|shutting)\b",
    "on": (r"\b(?:switch|turn|power)(?:s|ed|ing)?\s+(?:(?!off\b)[\w']+\s+){0,3}?on\b"
           r"|\bstart(?:s|ed|ing)?\b|\bget\s+(?:[\w']+\s+){0,4}?going\b"),
    "off": (r"\b(?:switch|turn|power|shut)(?:s|ed|ing)?\s+(?:(?!on\b)[\w']+\s+){0,3}?off\b|\boff$"
            r"|\bkill(?:s|ed|ing)?\b|\bstop(?:s|ped|ping)?\b"),
    "brighter": r"\bbrighter\b|\bbrighten(?:s|ed|ing)?\b|\btoo dark\b",
    "dimmer": r"\bdimmer\b|\bdim(?:s|med|ming)?\b|\btoo bright\b",
}
_NOT_VERBS = re.compile(r"\b(?:too|so|a bit|bit|very|quite|rather|pretty|really|getting|is|it's|its|are|that's)"
                        r"\s+dim\b|\b(?:the|a|my|our|that|this|door|smart)\s+locks?\b")
# Hunch's action -> (its own direction, the direction against it).
_AGAINST = {"lock": ("lock", "unlock"), "unlock": ("unlock", "lock"), "open": ("open", "close"),
            "close": ("close", "open"), "turn on": ("on", "off"), "turn off": ("off", "on"),
            "raise": ("brighter", "dimmer"), "lower": ("dimmer", "brighter")}
_NEGATED = r"\b(?:don't|dont|do not|never|not|no need to)\s+(?:[\w']+\s+){0,2}?(?:"

_WORD = (r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
         r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)")
_HOUR = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon|midnight)\b"
_UNIT = r"(?:\s*(?:%|°\s*[cf]?|per\s*cent\b|percent\b|degrees?\b))?"
_VALUE = r"\d+(?:[.,]\d+)?"

_NUMBER = re.compile(
    r"\d[.,]\d{3}(?!\d)"                                                     # 1,000 or 1.000
    r"|\d\s+point\b"                                                         # 21 point 5
    r"|(?:\d|\b" + _WORD + r")\s+and\s+an?\s+(?:half|quarter)\b"            # 21 and a half
    r"|\b(?:minus|negative)\s+(?:\d|" + _WORD + r"\b)"                      # minus 2
    r"|\b(?:to|at|from|between)\s+" + _VALUE + _UNIT + r"\s+(?:to|or|and)\s+-?\d"   # 21 to 23, 21 or 22
    r"|\d\s+\d"                                                             # 1 000, 2 1
    r"|\d\s*(?:plus|times|\+|\*)\s*\d"                                     # 21 plus 1
    r"|\d\D*\b(?:no|i mean|actually|sorry|rather|make that|make it)\b\D*\d")  # 21 degrees, no, 22

_DAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?"
_LATER = re.compile(
    r"\b(?:half|quarter|\d{1,2}|five|ten|twenty|twenty[ -]five)\s+past\s+" + _HOUR       # half past seven
    + r"|\b(?:half|quarter|five|ten|twenty|twenty[ -]five)\s+to\s+" + _HOUR               # quarter to eight
    + r"|\bhalf\s+" + _HOUR                                                              # half 7, half seven
    + r"|\b(?:in|during)\s+the\s+(?:morning|afternoon|evening|night)\b"
    r"|\b(?:at|by|before|until|till)\s+(?:night|sunset|sunrise|dawn|dusk|lunch(?:\s*time)?|dinner(?:\s*time)?"
    r"|breakfast|tea\s*time|morning|evening|the\s+weekend)\b"
    r"|\bon\s+(?:" + _DAYS + r"|the\s+hour|the\s+weekend)\b"
    r"|\b(?:next|this|every|each)\s+(?:" + _DAYS + r"|weekend|week|day|night|morning|evening|afternoon|hour)\b"
    r"|\b" + _DAYS + r"\b"
    r"|\bfirst\s+thing\b")
# A rough or boundary word with an hour: a later time ("around 7", "from 7 tonight", "until 7", "by 7") unless it is
# plainly an amount (`_plainly_an_amount`). The approximation words can begin an amount; the boundary words never do.
_APPROXIMATE = ("about", "around", "roughly", "approximately", "nearly")
_BOUNDARY = ("from", "until", "till", "after", "before", "since", "past", "by")
# Number words only: a number in digits is accounted for on its own (`_unaccounted`).
_ROUGH = re.compile(r"\b(?P<word>" + "|".join(_APPROXIMATE + _BOUNDARY) + r")\s+(?P<number>one|two|three|four|five|"
                    r"six|seven|eight|nine|ten|eleven|twelve)\b(?P<minutes>[:.]\d{2})?")
_A_TIME_AFTER = re.compile(r"\s*(?:am\b|pm\b|a\.m\.|p\.m\.|o'?clock\b|tonight\b)")
# The units of the plan's own kind, by the slot that carries its value.
_UNITS = {"brightness": r"%|per\s*cent\b|percent\b", "volume_level": r"%|per\s*cent\b|percent\b",
          "volume_step": r"%|per\s*cent\b|percent\b",
          "temperature": r"°|degrees?\b|[cf]\b|celsius\b|centigrade\b|fahrenheit\b"}


def _values(plan: Plan) -> dict[str, set[float]]:
    """The numbers the plan sends, by the slot that carries each (a brightness, a temperature, a volume step)."""
    found: dict[str, set[float]] = {}
    for _, slots in plan.ha_calls():
        for key, slot in slots.items():
            for value in slot.values():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    found.setdefault(key, set()).add(float(value))
    return found


def _plainly_an_amount(found: re.Match, text: str, values: dict[str, set[float]]) -> bool:
    """Whether a rough word with a number is plainly the command's own amount, and not a time. All of: an
    approximation word, never a boundary word ("down from 7" is a starting point, even though the frozen rules read
    it as a step of 7), directly after "by" or "to" ("by about 10") or with its number directly followed by a unit
    of the plan's own kind ("around 10 percent"); or "by" itself, the amount marker, with such a unit ("down by
    10%"); never a number followed by a time or a time's minutes; the number in digits, the
    size of a value the plan sends whatever its sign ("down by about 10" sends -10); and the number once in the
    sentence ("to 7 percent around 7": which was the value cannot be told). Anything else is held as a later time
    (found by the other model family's reads, 20 Nov 2026: three rounds of exceptions each let a time through)."""
    number, word = found["number"], found["word"]
    after = text[found.end():]
    if word not in (*_APPROXIMATE, "by") or found["minutes"] or not number.isdigit() or _A_TIME_AFTER.match(after):
        return False
    if float(number) not in {abs(value) for numbers in values.values() for value in numbers}:
        return False
    if len(re.findall(r"(?<![\d.,])" + number + r"(?!\d)", text)) != 1:
        return False
    units = [_UNITS[key] for key in values if key in _UNITS]
    own_unit = any(re.match(r"\s*(?:" + unit + ")", after) for unit in units)
    if word == "by":
        return own_unit   # "down by 10%": "by" is the amount marker itself, as the frozen rules read it too
    return own_unit or re.search(r"\b(?:by|to)\s*$", text[:found.start()]) is not None


# Every number in digits, read whole: a sign, digits, a decimal part (point or comma) or a time's minutes, and
# whatever is attached to it without a space ("7o'clock", "7pm", "10.5", "12,5", "10%", "21°", "7ish").
_TOKEN = re.compile(r"(?<![\w.,:])(?P<before>~)?(?P<sign>[-+])?(?P<whole>\d+)(?P<part>[.,:]\d+)?(?P<attached>\S*)")
_CLOCK_ATTACHED = re.compile(r"^(?:-?(?:am|pm|a\.m\.?|p\.m\.?|o'?clock))(?![a-z])")
# "19 hours" or "1900h" is a time of day, unless hours are the plan's own unit (a timer: "2 hours", "1h").
_HOURS_ATTACHED = re.compile(r"^(?:h|hrs?|hours?)(?![a-z])")
_HOURS_AFTER = re.compile(r"^\s*(?:hours?|hrs?)\b")
_CLOCK_AFTER = re.compile(r"^\s*(?:am\b|pm\b|a\.m\.|p\.m\.|o'?clock\b|tonight\b|tomorrow\b"
                          r"|this\s+(?:morning|afternoon|evening)\b|in\s+the\s+(?:morning|afternoon|evening)\b"
                          r"|at\s+night\b)")
_ISH = re.compile(r"^-?ish\b")
_BOUNDARY_BEFORE = re.compile(r"\b(?:from|until|till|after|before|since|past)\s*$")
_AT_OR_BY_BEFORE = re.compile(r"\b(?:at|by)\s*$")   # a boundary, unless a unit of the plan's kind follows ("at 50%")
_APPROXIMATE_BEFORE = re.compile(r"\b(?:" + "|".join(_APPROXIMATE) + r")\s*$")
_BY_OR_TO_BEFORE = re.compile(r"\b(?:by|to)\s*$")
_PERCENT_SLOTS = frozenset({"brightness", "volume_level", "volume_step"})
# A duration's units, by slot, as the rules read them (`logic/values.py`, `_DURATION_UNITS`), spaced or attached.
_DURATION_SLOTS = {"hours": r"(?:hours?|hrs?|h)\b", "minutes": r"(?:minutes?|mins?|m)\b",
                   "seconds": r"(?:seconds?|secs?|s)\b"}


def _own_unit(rest: str, values: dict[str, set[float]]) -> bool:
    """Whether `rest` (what follows a number, attached or after spaces) begins with a unit of the plan's own kind."""
    units = [_UNITS[key] for key in values if key in _UNITS] + [_DURATION_SLOTS[key] for key in values
                                                                 if key in _DURATION_SLOTS]
    return any(re.match(r"\s*(?:" + unit + ")", rest) for unit in units)


def _claims(number: float, rest: str, values: dict[str, set[float]], home_unit: str,
            claimed: set[tuple[str, float]] = frozenset()) -> tuple[str, float] | None:
    """The plan value a number in the sentence stands for, as (slot, value), or None. The slot whose unit follows
    the number claims it ("1 minute and 1 second": the 1 before "second" is the seconds); otherwise any slot of the
    same size not claimed yet. In magnitude, whatever sign a direction word gave it; a percentage as the rules
    round it; a temperature also in the other scale when the sentence says which ("70 degrees fahrenheit" in a
    Celsius home)."""
    def fits(key: str, value: float) -> bool:
        if abs(abs(value) - number) < 1e-9:
            return True
        if key in _PERCENT_SLOTS and abs(abs(value) - number) <= 0.5:
            return True
        if key == "temperature":
            scale = re.match(r"\s*(?:°\s*|degrees?\s+)?(?:(f|fahrenheit)|(c|celsius|centigrade))\b", rest)
            if scale:
                target = home_unit.casefold().removeprefix("°")
                said = "f" if scale.group(1) else "c"
                if said != target:
                    converted = (number - 32) * 5 / 9 if target == "c" else number * 9 / 5 + 32
                    return abs(round(converted, 2) - value) < 1e-6
        return False

    unit_of = next((key for key, unit in _DURATION_SLOTS.items() if key in values
                    and re.match(r"\s*" + unit, rest)), None)
    candidates = [(key, value) for key, numbers in values.items() for value in sorted(numbers)
                  if fits(key, value) and (key, value) not in claimed]
    if unit_of is not None:
        candidates = [candidate for candidate in candidates if candidate[0] == unit_of]
    return candidates[0] if candidates else None


# Number words. Two or more standing together (with "past", "to", "half", "quarter", "hundred" or "oh" between or
# beside them) are a clock time unless they spell a value the plan sends; one alone keeps its old reading.
_UNITS_WORDS = {word: n for n, word in enumerate((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"))}
_TENS_WORDS = {word: 10 * n for n, word in enumerate(
    ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"), 2)}
_CLOCK_WORDS = {"half", "quarter", "oh"}
_JOINERS = {"past", "to"}


def _spelled(words: list[str]) -> float | None:
    """The value a run of number words spells ("twenty five" 25, "one hundred" 100); None for anything else, such
    as a clock time ("half seven", "seven thirty" read as one number is not a value)."""
    total = current = 0
    last = None
    for word in words:
        if word in _UNITS_WORDS and (last is None or last == "tens" and _UNITS_WORDS[word] < 10):
            current, last = current + _UNITS_WORDS[word], "units"
        elif word in _TENS_WORDS and last in (None, "hundred"):
            current, last = current + _TENS_WORDS[word], "tens"
        elif word == "hundred" and last in ("units", "tens") and current < 100:
            current, last = current * 100, "hundred"
        else:
            return None
    return float(total + current)


def _runs_of_number_words(text: str) -> list[list[str]]:
    """Each run of two or more number words standing together."""
    runs, run = [], []
    numbers = set(_UNITS_WORDS) | set(_TENS_WORDS) | {"hundred"} | _CLOCK_WORDS
    for word in re.findall(r"[a-z']+|\S", text):
        if word in numbers or (word in _JOINERS and run):
            run.append(word)
            continue
        runs.append(run)
        run = []
    runs.append(run)
    out = []
    for found in runs:
        while found and found[-1] in _JOINERS:
            found = found[:-1]
        if sum(word in numbers for word in found) >= 2:
            out.append(found)
    return out


# Words that make a run of number words a station, a channel, an episode, a zip code or a year, not a clock time:
# the run gets the number reply, not the later-time one (the outcome, held, is the same).
_NOT_A_CLOCK = {"point", "fm", "station", "stations", "channel", "channels", "radio", "episode", "zip", "frequency"}


def _time_in_words(text: str, values: dict[str, set[float]]) -> str | None:
    """Whether a run of two or more number words that the plan does not send holds the sentence: "later" for a clock
    time ("turn off the lamp seven thirty", "nineteen hundred", "ten to eight"), "number" when every such run is
    plainly a station, a channel, an episode, a zip code or a year ("ninety nine point five", "channel to two
    hundred", "released between nineteen ninety and two thousand"), None when there is none. Which runs hold is
    decided by the runs alone; the words around them only choose the reply. Runs joined by "and" or "thousand"
    ("nine hundred and thirty five on radio") share the words around the whole number."""
    sent = {abs(value) for numbers in values.values() for value in numbers}
    words = re.findall(r"[a-z']+|\S", text)
    held = [run for run in _runs_of_number_words(text) if _spelled(run) not in sent]
    if not held:
        return None
    numbers = set(_UNITS_WORDS) | set(_TENS_WORDS) | {"hundred", "thousand", "and", "oh", "point"} | _CLOCK_WORDS
    kinds = set()
    for run in held:
        # The whole number the run belongs to: number words, "and", "thousand" and "point" either side of it.
        found = next((i for i in range(len(words)) if words[i:i + len(run)] == run), None)
        if found is None:
            kinds.add("later")
            continue
        first, last = found, found + len(run)
        while first > 0 and words[first - 1] in numbers:
            first -= 1
        while last < len(words) and words[last] in numbers:
            last += 1
        near = set(words[max(0, first - 2):first]) | set(words[last:last + 2]) | set(words[first:last])
        band = "am" in near and bool(set(words) & {"radio", "station", "stations"})
        year = re.search(r"\b(?:released|between)\b", " ".join(words[:first])) is not None
        kinds.add("number" if near & _NOT_A_CLOCK or band or year else "later")
    return "later" if "later" in kinds else "number"


def _unaccounted(text: str, values: dict[str, set[float]], home_unit: str = "°C") -> str | None:
    """Why a number in digits is not plainly the command's own value, or None when every one is. Each number,
    read whole with what is attached to it, must stand for a value the plan sends (each value claimed once), must
    carry no clock mark (am, pm, o'clock, hours, ":30", a ".30" or a leading zero unless a unit of the plan's kind
    follows, "tonight" after it), must not follow a boundary word (from, until, till, after, before, since, past;
    "at" or "by" unless a unit of the plan's kind follows: "at 50%", "by 10%"), and if it is rough ("about", "around", "roughly",
    "approximately", "nearly", "~", "-ish") it must follow "by" or "to" or have the plan's unit after it.
    "later" when a clock mark or a boundary word is what failed, "number" for anything else."""
    claimed: set[tuple[str, float]] = set()
    for found in _TOKEN.finditer(text):
        whole, part, attached = found["whole"], found["part"] or "", found["attached"]
        rest = text[found.end("part") if found["part"] else found.end("whole"):]
        before = text[:found.start()]
        own_unit = _own_unit(rest, values)   # directly after the number ("7-ish percent" has "-ish" between)
        hours_are_a_time = "hours" not in values and (
            _HOURS_ATTACHED.match(attached) or _HOURS_AFTER.match(text[found.end():]))
        if (part.startswith(":") or _CLOCK_ATTACHED.match(attached) or _CLOCK_AFTER.match(text[found.end():])
                or hours_are_a_time):
            return "later"
        looks_like_a_clock = (part[:1] == "." and len(part) == 3 and int(part[1:]) < 60) or (
            len(whole) > 1 and whole.startswith("0"))
        if looks_like_a_clock and not own_unit:
            return "later"
        rough_word = _APPROXIMATE_BEFORE.search(before)
        rough = rough_word is not None or found["before"] == "~" or _ISH.match(attached) is not None
        at_or_by = _AT_OR_BY_BEFORE.search(before)
        # "by ~7" is "by about 7", an amount; "by 7" and "at 7" with no unit of the plan's kind are times.
        if _BOUNDARY_BEFORE.search(before) or (at_or_by is not None and not own_unit and not (
                rough and at_or_by.group(0).strip() == "by")):
            return "later"
        if rough:
            lead = before[:rough_word.start()] if rough_word else before
            # A rough number in decimals needs the plan's unit after it: "by" or "to" is not enough.
            if not (own_unit or (_BY_OR_TO_BEFORE.search(lead) and not part)):
                return "later" if len(whole) <= 2 and 1 <= int(whole) <= 12 and not part else "number"
        if attached and not (_ISH.match(attached) or own_unit or re.match(r"^[.,!?;)\]]*$", attached)):
            return "number"
        number = float(whole + (part.replace(",", ".") if part else ""))
        claim = _claims(number, rest, values, home_unit, claimed)
        if claim is None:
            return "number"
        claimed.add(claim)
    return None


def _phrases(names: Iterable[str]) -> list[str]:
    """Each name as its words in lower case, longest first (so "Morning Room" goes before "Morning")."""
    return sorted({" ".join(re.findall(r"[\w']+", n.casefold().replace("\u2019", "'"))) for n in names} - {""},
                  key=len, reverse=True)


def _once(text: str, phrase: str, joiner: str) -> str:
    """`text` with `phrase` set aside where it occurs exactly once, as whole words. Where it occurs more than once,
    which occurrence was the name cannot be told, so none is set aside: the checks then read every word ("turn on
    Night at night" keeps its "at night")."""
    pattern = re.compile(r"(?<![\w'])" + joiner.join(re.escape(part) for part in phrase.split()) + r"(?![\w'])")
    return pattern.sub(" ", text) if len(pattern.findall(text)) == 1 else text


def _without_names(sentence: str, names: Iterable[str]) -> str:
    """The sentence in lower case, punctuation made spaces (apostrophes kept), each name in `names` set aside where
    it occurs once (`_once`)."""
    text = " ".join(re.sub(r"[^\w']+", " ", sentence.casefold().replace("\u2019", "'")).split())
    for name in _phrases(names):
        text = _once(text, name, " ")
    return " ".join(text.split())


def target_names(plan: Plan, home: Home) -> list[str]:
    """The names that identified what `plan` acts on: each device's names and its room's, and the room the plan
    names. Never another device's or room's: a light called "Seven" is not a reason to read past "half past seven"
    in a sentence about the kitchen light.

    Read from a live registry, so anything can be missing: a device's room no longer in the area list (the two are
    read separately, so a room deleted between the reads leaves exactly this), a device with no room or no name, an
    empty alias or a `None`, an entity in the plan that has since left the home. What is not there is skipped."""
    things = {thing.entity_id: thing for thing in home.things if isinstance(getattr(thing, "entity_id", None), str)}
    areas = {area.area_id: area for area in home.areas if isinstance(getattr(area, "area_id", None), str)}
    ids = {entity_id for entity_id in plan.entity_ids if entity_id in things}
    rooms: set[str] = set()
    for _, slots in plan.ha_calls():
        for key in ("name", "area"):
            slot = slots.get(key)
            value = slot.get("value") if isinstance(slot, dict) else None
            if isinstance(value, str):
                if value in things:
                    ids.add(value)
                elif value in areas:
                    rooms.add(value)
    names: list[str] = []
    for entity_id in ids:
        thing = things[entity_id]
        names += [name for name in (thing.names or ()) if isinstance(name, str) and name.strip()]
        if isinstance(thing.area_id, str) and thing.area_id in areas:
            rooms.add(thing.area_id)
    for area_id in rooms:
        names += [name for name in (areas[area_id].names or ()) if isinstance(name, str) and name.strip()]
    return names


def against(sentence: str, action: str, names: Iterable[str] = ()) -> bool:
    """Whether the sentence's words go against `action`: the other direction is there, or its own is negated."""
    if action not in _AGAINST:
        return False
    own, other = _AGAINST[action]
    text = " ".join(_NOT_VERBS.sub(" ", _without_names(sentence, names)).split())
    return bool(re.search(_DIRECTIONS[other], text) or re.search(_NEGATED + _DIRECTIONS[own] + ")", text))


def _payload(plan: Plan) -> list[str]:
    """The words the rules took out of the sentence for the action, as they will be sent: the announced message,
    the list item, the search query. They are what the owner wants said, added or played, not the command."""
    return [slot["value"] for _, slots in plan.ha_calls() for key, slot in slots.items()
            if key in PAYLOAD and isinstance(slot, dict) and isinstance(slot.get("value"), str)]


def _set_aside(text: str, words: Iterable[str]) -> str:
    """`text` (in lower case) with each of `words` set aside where it occurs once (`_once`), longest first, matched
    as whole words with any punctuation or spaces between them. Everything else is left as it is, so "21.5" or
    "7:30" keep their marks."""
    for phrase in _phrases(words):
        text = _once(text, phrase, r"[^\w']+")
    return " ".join(text.split())


def review(sentence: str, answer: object, result: Plan | HandBack,
           names: Home | Iterable[str] = ()) -> Plan | HandBack:
    """The rules' decision, or a hand-back when the rules would act on words against the answer, or on a form they
    misread. `names`: the home (`Home`), whose names for what the plan acts on (`target_names`) are set aside, or
    those names themselves; with the payload the rules took out for the action (`_payload`), they are set aside,
    each only where it occurs once, before any check reads the words."""
    if not isinstance(result, Plan):
        return result
    try:
        return _review(sentence, answer, result, names)
    except Exception as error:  # noqa: BLE001 - the turn goes on, and the command is never acted on unchecked
        LOG.warning("the not-sure checks failed (%s); doing nothing with the sentence", type(error).__name__)
        return HandBack(CHECK_FAILED_REASON, CHECK_FAILED_NOTE)


def _review(sentence: str, answer: object, result: Plan, names: Home | Iterable[str]) -> Plan | HandBack:
    targets = target_names(result, names) if isinstance(names, Home) else list(names)
    aside = [*targets, *_payload(result)]
    if against(sentence, result.action or str(getattr(answer, "action", "")), aside):
        return HandBack(DISAGREE, CONTRADICTION_NOTE)
    text = _set_aside(" ".join(sentence.casefold().replace("\u2019", "'").split()), aside)
    values = _values(result)
    if values and _NUMBER.search(text):
        return HandBack("needs a value", NUMBER_NOTE)
    # Every number in digits must be the command's own value, and plainly so (`_unaccounted`).
    home_unit = names.temperature_unit if isinstance(names, Home) else "°C"
    why = _unaccounted(text, values, home_unit)
    # A rough or boundary word with an hour in words is a later time unless it is plainly an amount.
    rough = any(not _plainly_an_amount(found, text, values) for found in _ROUGH.finditer(text))
    in_words = _time_in_words(text, values)
    later = why == "later" or _LATER.search(text) is not None or rough or in_words == "later"
    if later and getattr(answer, "kind", None) != "Timers and alarms":
        return HandBack("delayed command", LATER_NOTE)
    if why is not None or in_words is not None:
        return HandBack("needs a value", NUMBER_NOTE)
    return result
