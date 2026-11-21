"""Small readers for a value Hunch does not return: a number, a colour, a duration, an item for a list.

Hunch names the action and the kind of thing. A command such as "set the heating to 21" also needs a value, and
these readers take it from the sentence. They are deliberately limited, and each says what it reads:

- levels and temperatures are written in digits ("21", "21.5", "50%"); number words are not read for these;
- a colour is one of the short list `COLOURS`;
- a duration is digits or English number words from one to ninety-nine with hours, minutes or seconds,
  or "an hour", "a minute", "half an hour";
- an item is the words between a verb ("add", "remove") and the list it goes to;
- `names_a_level` says only whether a sentence names a level at all ("brighter" does not; "to 80%",
  "by ten" and "half" do), so a plain "brighter" can be a step.

Each returns None when it cannot read the value, or when the sentence holds two values and it cannot tell which
is meant. None always means: hand the sentence back.
"""

from __future__ import annotations

import re

_NUMBER = r"(?<![\w.,+-])([+-]?\d+(?:[.,]\d+)?)"
_PERCENT = re.compile(_NUMBER + r"\s*(?:%|per\s*cent\b|percent\b)")
_DEGREES = re.compile(_NUMBER + r"\s*(?:°\s*[cf]?|degrees?\b(?:\s+(?:celsius|centigrade|fahrenheit|c|f)\b)?"
                      r"|celsius\b|centigrade\b|fahrenheit\b|c\b|f\b)")
_ANY_NUMBER = re.compile(_NUMBER + r"(?![\w.,])")

# The colours read from a sentence: common names Home Assistant's colour slot accepts.
COLOURS: dict[str, str] = {
    "red": "red", "orange": "orange", "yellow": "yellow", "green": "green", "blue": "blue",
    "purple": "purple", "violet": "violet", "pink": "pink", "white": "white", "cyan": "cyan",
    "magenta": "magenta", "turquoise": "turquoise", "gold": "gold", "brown": "brown",
}

_DURATION_UNITS = {
    "hours": ("hours", "hour", "hrs", "hr", "h"),
    "minutes": ("minutes", "minute", "mins", "min", "m"),
    "seconds": ("seconds", "second", "secs", "sec", "s"),
}
_DURATION_WORDS = dict(zip(
    ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
     "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"), range(1, 20), strict=True))
_DURATION_TENS = dict(zip(
    ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"), range(20, 100, 10), strict=True))
_WORD_AMOUNT = (r"(?:" + "|".join(_DURATION_TENS) + r")(?:[ -](?:"
                + "|".join(list(_DURATION_WORDS)[:9]) + r"))?|(?:" + "|".join(_DURATION_WORDS) + r")")
_UNIT_WORDS = "|".join(name for names in _DURATION_UNITS.values() for name in names)
_SPOKEN_DURATION = re.compile(r"\b(" + _WORD_AMOUNT + r")\s+(?=(?:" + _UNIT_WORDS + r")\b)")
_DELAY = re.compile(
    r"\b(?:tomorrow|tonight|later|after|before|until|till|when|whenever|soon|shortly|once|bedtime|overnight)\b"
    r"|\bo['’]?clock\b|\bthis\s+(?:morning|afternoon|evening)\b"
    r"|\bin\s+(?:a\s+)?(?:bit|while|moment|sec|tick)\b|\bin\s+(?:\d+|" + _WORD_AMOUNT + r")\b(?!\s*%)"
    r"|\b(?:at|by)\s+(?:\d+|noon|midnight|" + _WORD_AMOUNT + r")\b"
    r"|\b(?:\d+(?::\d{2})?|" + _WORD_AMOUNT + r")\s*(?:a\.?m\.?|p\.?m\.?)(?!\w)"
    r"|\b(?:in|for)\s+(?:[\w.,+-]+\s+){0,5}(?:seconds?|minutes?|hours?)\b"
    r"|(?:\b\d+(?:[.,]\d+)?\s*|\b(?:an?|half an?)\s+)(?:" + _UNIT_WORDS + r")\b")


def normalise(sentence: str) -> str:
    """Lower case, with runs of space made one space."""
    return " ".join(sentence.casefold().split())


def _duration_text(sentence: str) -> str:
    def number(match: re.Match) -> str:
        amounts = _DURATION_WORDS | _DURATION_TENS
        return str(sum(amounts[word] for word in re.split(r"[ -]", match[1]))) + " "

    return _SPOKEN_DURATION.sub(number, normalise(sentence))


def has_delay(sentence: str) -> bool:
    """A duration or clock/delay wording, even when it is not a readable timer value."""
    text = _duration_text(sentence)
    # Explicit levels and temperatures, such as 'at 35%' or 'by 10%', are not clock times.
    text = _DEGREES.sub(" ", _PERCENT.sub(" ", text))
    return bool(_DELAY.search(text))


def _to_float(text: str) -> float:
    return float(text.replace(",", "."))


def _numbers(sentence: str) -> list[float]:
    return [_to_float(found) for found in _ANY_NUMBER.findall(normalise(sentence))]


def _one(values: list[float]) -> float | None:
    return values[0] if len(values) == 1 else None


def percentage(sentence: str) -> int | None:
    """A level from 0 to 100: a number marked as a percentage, or else the only number in the sentence."""
    text = normalise(sentence)
    if re.search(r"\d\s*[-/]\s*\d|\d[eE][+-]?\d|\b(?:degrees?|celsius|fahrenheit|kelvin)\b|°", text):
        return None
    marked = [_to_float(found) for found in _PERCENT.findall(text)]
    value = _one(marked) if marked else _one(_numbers(text))
    if value is None or not 0 <= value <= 100:
        return None
    return round(value)


def temperature(sentence: str, target_unit: str | None = None) -> float | None:
    """A temperature: a number marked in degrees, or else the only number in the sentence.

    With target_unit (the home's unit), explicit Celsius or Fahrenheit is converted to it. Without a unit,
    the number is already in the home's unit (Celsius if no unit is supplied here). Only positive settings
    up to 35 Celsius or 95 Fahrenheit are read, after conversion.
    """
    text = normalise(sentence)
    if re.search(r"\d\s*[-/]\s*\d|\d[eE][+-]?\d|%|\b(?:percent|per\s+cent)\b", text):
        return None
    marked = [_to_float(found) for found in _DEGREES.findall(text)]
    value = _one(marked) if marked else _one(_numbers(text))
    if value is None or not 0 < value < 100:
        return None
    units = set(re.findall(r"(?:°\s*|degrees?\s+)([cf])\b|\b(celsius|centigrade|fahrenheit)\b", text))
    scales = {"f" if (letter == "f" or word == "fahrenheit") else "c" for letter, word in units}
    # A bare suffix, such as 68F, is accepted too.
    scales.update(re.findall(r"\d\s*([cf])\b", text))
    if len(scales) > 1 or re.search(r"\b(?:kelvin|k)\b", text):
        return None
    source = next(iter(scales), None)
    target = target_unit.casefold().removeprefix("°") if target_unit else None
    if source and target is not None:
        if target not in {"c", "f"}:
            return None
        if source != target:
            value = (value - 32) * 5 / 9 if target == "c" else value * 9 / 5 + 32
            value = round(value, 2)
    unit = target or source or "c"
    if unit not in {"c", "f"} or not 0 < value <= (95 if unit == "f" else 35):
        return None
    return int(value) if value == int(value) else value


_LEVEL_WORDS = frozenset({
    "to", "at", "by", "percent", "per", "cent", "half", "quarter", "full", "fully", "max", "maximum", "min",
    "minimum", "brightest", "dimmest", "lowest", "highest", "zero", "hundred", "completely",
}) | frozenset(_DURATION_WORDS) | frozenset(_DURATION_TENS)


def names_a_level(sentence: str) -> bool:
    """Whether the sentence names a level or an amount in any way: digits, a percentage, a number word, or a
    word such as "to", "by", "half" or "full". Only a sentence with none of these ("brighter", "dim the
    lights") is a plain step up or down; any other is read as a level, or handed back."""
    text = normalise(sentence)
    if re.search(r"\d|%|\ball the way\b", text):
        return True
    return bool(_LEVEL_WORDS.intersection(re.findall(r"[a-z]+", text)))


def colour(sentence: str) -> str | None:
    """One colour name from `COLOURS`; None for none or for two different colours."""
    words = re.findall(r"[a-z]+", normalise(sentence))
    found = {COLOURS[word] for word in words if word in COLOURS}
    return found.pop() if len(found) == 1 else None


def duration(sentence: str) -> dict[str, int] | None:
    """Hours, minutes and seconds named in the sentence, as Home Assistant's timer slots; None if there are none."""
    text = _duration_text(sentence)
    found: dict[str, int] = {}
    if re.search(r"\bhalf an hour\b", text):
        found["minutes"] = 30
        text = re.sub(r"\bhalf an hour\b", " ", text)
    for slot, names in _DURATION_UNITS.items():
        # Digits may take any unit ("10m", "10 min"); "a", "an" and "one" only a whole word ("an hour").
        digits = _NUMBER + r"\s*(?:" + "|".join(names) + r")\b"
        words = r"\b(?:an|a|one)\s+(?:" + "|".join(name for name in names if len(name) > 1) + r")\b"
        raw = re.findall(digits, text)
        if any(not amount.isdigit() or int(amount) <= 0 for amount in raw):
            return None
        amounts = [int(amount) for amount in raw] + [1 for _ in re.findall(words, text)]
        if len(amounts) > 1 or (amounts and slot in found):
            return None
        if amounts:
            found[slot] = amounts[0]
        text = re.sub(digits, " ", text)
        text = re.sub(words, " ", text)
    units = [name for names in _DURATION_UNITS.values() for name in names]
    if re.search(r"\d|\b(?:" + "|".join(units) + r"|half|quarter)\b", text):
        return None
    return found or None


def simple_timer(sentence: str, needs_duration: bool) -> bool:
    """Only an unnamed timer, without a room, a start-duration selector or a delayed command.

    Named timers and 'add 2 minutes to the 10 minute timer' need slots this parser cannot safely distinguish.
    They go back to Home Assistant instead of silently selecting a different timer.
    """
    text = _duration_text(sentence)
    if needs_duration:
        if duration(text) is None:
            return False
        text = re.sub(r"\bhalf an hour\b", " ", text)
        units = "|".join(name for names in _DURATION_UNITS.values() for name in names)
        text = re.sub(r"(?:\d+|an|a|one)\s*(?:" + units + r")\b", " ", text)
    allowed = {"set", "start", "a", "an", "the", "my", "timer", "for", "please", "stop", "cancel",
               "how", "long", "much", "time", "is", "left", "on", "add", "to", "take", "off",
               "remove", "increase", "decrease", "by", "and"}
    return all(word in allowed for word in re.findall(r"\w+", text))


_ITEM_LEAD = frozenset({"some", "a", "an", "the", "more"})
_MEDIA_LEAD = frozenset({"me", "us", "some", "a", "an"})


def _between(text: str, starts: tuple[str, ...], stops: tuple[str, ...],
             lead: frozenset[str] = _ITEM_LEAD) -> str | None:
    start = None
    for word in starts:
        hit = re.search(r"\b" + re.escape(word) + r"\b", text)
        if hit and (start is None or hit.end() < start):
            start = hit.end()
    if start is None:
        return None
    rest = text[start:]
    end = len(rest)
    for stop in stops:
        hit = re.search(r"\s" + re.escape(stop) + r"\b", rest)
        if hit:
            end = min(end, hit.start())
    words = rest[:end].split()
    while words and words[0] in lead:
        words = words[1:]
    item = " ".join(words).strip(" .,!?")
    return item or None


def list_item(sentence: str) -> str | None:
    """The thing to add: the words after "add" (or "put") up to "to my", "to the", "to", "on" or the end."""
    return _between(normalise(sentence), ("add", "put"), ("to my", "to the", "to", "on my", "on the", "onto"))


def removed_item(sentence: str) -> str | None:
    """The thing to remove: the words after "remove", "delete", "take" or "cross" up to "from" or "off"."""
    return _between(normalise(sentence), ("remove", "delete", "take", "cross"), ("from", "off"))


def search_query(sentence: str) -> str | None:
    """What to play: the words after "play" up to "on" or "in" (where a speaker or room is named)."""
    return _between(normalise(sentence), ("play",), ("on", "in"), _MEDIA_LEAD)


def message(sentence: str) -> str | None:
    """What to announce: the words after "announce" or "broadcast", without a leading "that"."""
    found = _between(normalise(sentence), ("announce", "broadcast"), (), frozenset())
    if found and found.startswith("that "):
        found = found[len("that "):].strip() or None
    return found


# Words for media that name no particular thing to play: "play some music" says nothing to search for.
VAGUE_MEDIA = frozenset({"music", "something", "anything", "a song", "songs", "a tune", "tunes", "the radio",
                         "radio", "my music", "it"})


def volume_step(sentence: str, direction: str) -> int | str | None:
    """How far to move the volume: a percentage named in the sentence, signed by `direction` ("up" or "down"),
    or else the word itself, which Home Assistant reads as its default step."""
    amount = percentage(sentence)
    if amount is None and (re.search(r"\d|\b(?:by|to)\b", sentence)):
        return None
    if amount is None:
        return direction
    return amount if direction == "up" else -amount
