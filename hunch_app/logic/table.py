"""From Hunch's two answers to a Home Assistant intent: the translation table.

Hunch answers what to do (one of the actions) and what kind of thing (one of the kinds). Home Assistant acts
through built-in intents such as `HassTurnOn` with slots (`name`, `area`, `domain` and a value). Each pair maps to
one or more rows here. A row says which intent, which entity domains the kind of thing implies, how the device
or room is found, and whether a value is needed that Hunch does not return ("needs a value"): that value is read
from the sentence by `values.py`, and when it cannot be read the sentence is handed back. One exception: raise
and lower for Lights with no brightness in the sentence change each light by a step from its present level
(`STEP`, `FLOOR`; see `decide.py`).

To add a row (version 1.1 adds "previous" and "announce", already mapped below): add the action to `ACTIONS`
if it is new, and add `(action, kind): (Row(...),)` to `TABLE`; use `ANY_KIND` when the kind does not matter.
The tests check every row's intent and slots against `KNOWN_INTENTS`, Home Assistant's list of built-in
intents and their slots, read from https://github.com/OHF-Voice/intents/blob/main/intents.yaml on 15 Nov 2026.
"""

from __future__ import annotations

from dataclasses import dataclass

NOT_FOR_ME = "not for me"
NO_KIND = "none"
ANY_KIND = "*"

# What to do: Hunch-1 Home's 15 actions, then the two that version 1.1 added.
ACTIONS: tuple[str, ...] = (
    "turn on", "turn off", "set level", "raise", "lower", "set colour", "set temperature", "lock", "unlock",
    "open", "close", "next", "ask", "add", "remove",
)
ACTIONS_1_1: tuple[str, ...] = ("previous", "announce")

# What kind of thing: Hunch-1 Home's 10 kinds.
KINDS: tuple[str, ...] = (
    "Lights", "Climate", "Security", "Speakers & TVs", "Water", "Scenes", "Timers and alarms", "Weather",
    "Lists", "Other device",
)

# How a row finds what it acts on (see match.py).
NAMED = "named"                  # a device named in the sentence, by its name or an alias
NAMED_OR_ROOM = "named or room"  # a named device; else every one in a named room; else in the room of the
#                                  device that heard the command
OPTIONAL_NAME = "optional name"  # a named device if one is named; otherwise none
NO_TARGET = "none"               # nothing to find (weather without a name, timers, announcements)

# Values a row may need, and the reader in values.py that supplies each (see decide.py).
BRIGHTNESS = "brightness"
COLOUR = "colour"
TEMPERATURE = "temperature"
VOLUME = "volume"
VOLUME_UP = "volume up"
VOLUME_DOWN = "volume down"
ITEM = "item"
REMOVED_ITEM = "removed item"
DURATION = "duration"
SEARCH = "search"
MESSAGE = "message"

TIMER_WORDS = ("timer", "timers")
# Covers that cover a window. Not "window": in Home Assistant that class is a motorised window or skylight, an
# opening into the home, so it is handed back like a door.
WINDOW_COVERINGS = ("awning", "blind", "curtain", "shade", "shutter")
# Words that ask about a time Home Assistant's weather intent does not answer: it gives the weather now.
LATER_WORDS = ("tomorrow", "tonight", "later", "weekend", "week", "monday", "tuesday", "wednesday", "thursday",
               "friday", "saturday", "sunday", "next", "forecast")


@dataclass(frozen=True)
class Row:
    """One way to carry out a pair of answers in Home Assistant."""

    intent: str
    domains: tuple[str, ...] = ()
    target: str = NO_TARGET
    needs: str | None = None
    words: tuple[str, ...] = ()           # used only when one of these words is in the sentence
    refuse_words: tuple[str, ...] = ()    # handed back when one of these words is in the sentence
    domain_slot: bool = False             # send the `domain` slot (for the intents that read it)
    intent_by_domain: tuple[tuple[str, str], ...] = ()  # another intent when the device found is of a domain
    since: str = "1.0"
    note: str = ""
    device_classes: tuple[str, ...] = ()  # only these classes may match; also sent to intents that accept them
    by_entity: bool = False               # one intent call per device found, by entity ID, never by room
    named_only: tuple[str, ...] = ()      # domains a room command never reaches: the device must be named
    room_classes: tuple[str, ...] = ()    # the only classes a room command reaches; a name reaches any class
    also_named: tuple[str, ...] = ()      # other domains reached only by a name holding one of `name_words`
    name_words: tuple[str, ...] = ()


# Words in a name that say a switch is a light: a lamp on a smart plug is a `switch` to Home Assistant.
LIGHT_WORDS = ("lamp", "lamps", "light", "lights", "lighting", "bulb", "bulbs", "lantern", "lanterns",
               "sconce", "sconces", "chandelier", "chandeliers")
# Raising or lowering a light with no brightness in the sentence (see decide.py):
# a light that can dim moves by STEP percentage points, an off light counting as 0; lowering never takes it
# below FLOOR, nor raises one already below it; a light that cannot dim, or a named switch, is turned on by
# raise and off by lower. Home Assistant's own "brightness increase" device action steps by 10; that is for a
# button pressed again and again, and one spoken "brighter" should be seen to change, so the step is 25.
STEP = 25
FLOOR = 10
# Words in a name that say a fan or a switch is for heating or cooling (a fan, or a heater on a smart plug): a
# Climate answer reaches it only when the sentence names it by such a name, never as part of a room. "Air con" is
# two words; either is enough.
CLIMATE_WORDS = ("fan", "fans", "heater", "heaters", "radiator", "radiators", "air", "aircon", "con",
                 "dehumidifier", "dehumidifiers", "humidifier", "humidifiers")

TABLE: dict[tuple[str, str], tuple[Row, ...]] = {
    # Lights: a room reaches lights only; a switch is reached only when the sentence names it by a name that
    # says lamp or light (a lamp on a smart plug).
    ("turn on", "Lights"): (Row("HassTurnOn", ("light",), NAMED_OR_ROOM, domain_slot=True,
                                also_named=("switch",), name_words=LIGHT_WORDS),),
    ("turn off", "Lights"): (Row("HassTurnOff", ("light",), NAMED_OR_ROOM, domain_slot=True,
                                 also_named=("switch",), name_words=LIGHT_WORDS),),
    ("set level", "Lights"): (Row("HassLightSet", ("light",), NAMED_OR_ROOM, BRIGHTNESS, domain_slot=True),),
    ("raise", "Lights"): (Row("HassLightSet", ("light",), NAMED_OR_ROOM, BRIGHTNESS, domain_slot=True,
                              also_named=("switch",), name_words=LIGHT_WORDS,
                              note="a brightness in the sentence ('to 80%'); without one, a step up for each "
                                   "light that can dim, on for one that cannot or for a named switch"),),
    ("lower", "Lights"): (Row("HassLightSet", ("light",), NAMED_OR_ROOM, BRIGHTNESS, domain_slot=True,
                              also_named=("switch",), name_words=LIGHT_WORDS,
                              note="a brightness in the sentence ('to 10%'); without one, a step down to "
                                   "no less than the floor, off for a light that cannot dim or a named "
                                   "switch"),),
    ("set colour", "Lights"): (Row("HassLightSet", ("light",), NAMED_OR_ROOM, COLOUR, domain_slot=True),),
    # Climate: heating, fans and blinds (Hunch files window coverings under Climate, as Apple's Home app does).
    # With Locks and doors switched off, open and close reach window coverings only. On and off reach a
    # thermostat by name or by room, and a fan or a switch only by a name that says it is a fan or a heater, never
    # as part of a room (app, 20 Nov 2026: Hunch answers "kill the desk fan" as Climate).
    ("set temperature", "Climate"): (Row("HassClimateSetTemperature", ("climate",), NAMED_OR_ROOM, TEMPERATURE),),
    ("turn on", "Climate"): (Row("HassTurnOn", ("climate",), NAMED_OR_ROOM, domain_slot=True,
                                 also_named=("fan", "switch"), name_words=CLIMATE_WORDS),),
    ("turn off", "Climate"): (Row("HassTurnOff", ("climate",), NAMED_OR_ROOM, domain_slot=True,
                                  also_named=("fan", "switch"), name_words=CLIMATE_WORDS),),
    ("open", "Climate"): (Row("HassTurnOn", ("cover",), NAMED_OR_ROOM, domain_slot=True, note="blinds",
                              device_classes=WINDOW_COVERINGS),),
    ("close", "Climate"): (Row("HassTurnOff", ("cover",), NAMED_OR_ROOM, domain_slot=True, note="blinds",
                               device_classes=WINDOW_COVERINGS),),
    # Security: locking every lock in a room is allowed. Unlocking, doors, gates and garage doors are in
    # LOCKS_AND_DOORS (below), used unless the owner switches that setting off.
    ("lock", "Security"): (Row("HassTurnOn", ("lock",), NAMED_OR_ROOM, domain_slot=True),),
    # Speakers & TVs
    ("turn on", "Speakers & TVs"): (
        Row("HassMediaPlayerUnmute", ("media_player",), NAMED_OR_ROOM, words=("unmute",)),
        Row("HassMediaUnpause", ("media_player",), NAMED_OR_ROOM, words=("resume", "unpause", "continue")),
        Row("HassMediaSearchAndPlay", ("media_player",), NAMED_OR_ROOM, SEARCH, words=("play",)),
        Row("HassTurnOn", ("media_player",), NAMED_OR_ROOM, domain_slot=True),
    ),
    ("turn off", "Speakers & TVs"): (
        Row("HassMediaPlayerMute", ("media_player",), NAMED_OR_ROOM, words=("mute", "silence")),
        Row("HassMediaPause", ("media_player",), NAMED_OR_ROOM, words=("pause", "stop")),
        Row("HassTurnOff", ("media_player",), NAMED_OR_ROOM, domain_slot=True),
    ),
    ("raise", "Speakers & TVs"): (Row("HassSetVolumeRelative", ("media_player",), NAMED_OR_ROOM, VOLUME_UP),),
    ("lower", "Speakers & TVs"): (Row("HassSetVolumeRelative", ("media_player",), NAMED_OR_ROOM, VOLUME_DOWN),),
    ("set level", "Speakers & TVs"): (Row("HassSetVolume", ("media_player",), NAMED_OR_ROOM, VOLUME),),
    ("next", "Speakers & TVs"): (Row("HassMediaNext", ("media_player",), NAMED_OR_ROOM),),
    # Scenes: a scene must be named
    ("turn on", "Scenes"): (Row("HassTurnOn", ("scene",), NAMED, domain_slot=True),),
    # Timers and alarms: Home Assistant has timer intents and no alarm intent, so only a sentence that says
    # "timer" is carried out; an alarm is handed back. Timers also need a device that supports them.
    ("turn on", "Timers and alarms"): (Row("HassStartTimer", needs=DURATION, words=TIMER_WORDS),),
    ("turn off", "Timers and alarms"): (Row("HassCancelTimer", words=TIMER_WORDS),),
    ("remove", "Timers and alarms"): (Row("HassCancelTimer", words=TIMER_WORDS),),
    ("ask", "Timers and alarms"): (Row("HassTimerStatus", words=TIMER_WORDS),),
    ("raise", "Timers and alarms"): (Row("HassIncreaseTimer", needs=DURATION, words=TIMER_WORDS),),
    ("lower", "Timers and alarms"): (Row("HassDecreaseTimer", needs=DURATION, words=TIMER_WORDS),),
    # Weather: the weather now; a question about later is handed back
    ("ask", "Weather"): (Row("HassGetWeather", ("weather",), OPTIONAL_NAME, refuse_words=LATER_WORDS),),
    # Lists: the to-do intents, on a list named in the sentence (the shopping list is the to-do list called
    # "Shopping List" when Home Assistant's shopping list is set up)
    ("add", "Lists"): (Row("HassListAddItem", ("todo",), NAMED, ITEM),),
    ("remove", "Lists"): (Row("HassListRemoveItem", ("todo",), NAMED, REMOVED_ITEM),),
    # Other device: plugs, fans, switches and the vacuum; the device must be named
    ("turn on", "Other device"): (Row("HassTurnOn", ("switch", "fan", "input_boolean", "vacuum"), NAMED,
                                      domain_slot=True, intent_by_domain=(("vacuum", "HassVacuumStart"),)),),
    ("turn off", "Other device"): (Row("HassTurnOff", ("switch", "fan", "input_boolean", "vacuum"), NAMED,
                                       domain_slot=True,
                                       intent_by_domain=(("vacuum", "HassVacuumReturnToBase"),)),),
    # From version 1.1
    ("previous", "Speakers & TVs"): (Row("HassMediaPrevious", ("media_player",), NAMED_OR_ROOM, since="1.1"),),
    ("announce", ANY_KIND): (Row("HassBroadcast", needs=MESSAGE, since="1.1"),),
}

# Locks and doors (experimental): a setting, on by default. With it on, these rows replace the ones above.
# Nothing that opens the home moves unless the sentence names it (0.1.1). Unlock is `HassTurnOff` on a lock
# the sentence names, sent by entity ID; no lock named, or only a room, hands back. A room's name is never a
# name for anything that opens the home (`decide._opens_the_home`, `match.find_target`). Open and close are
# `HassTurnOn` and `HassTurnOff` on a cover the sentence names, whatever its device class (doors, gates, garage
# doors, motorised windows and blinds alike), or on a switch it names (an entry wired as a switch, by its own on
# and off). A command that names no device reaches only the window coverings in the room, as with the setting
# off. Hunch
# 1.1 answers doors, gates and garage doors as open or close / Security and blinds as open or close / Climate;
# both reach the same devices. Each device found gets its own call, so a room command never reaches more than
# the plug-in found, and a room command also sends the window-covering classes for Home Assistant to check.
_OPEN = Row("HassTurnOn", ("cover", "switch"), NAMED_OR_ROOM, domain_slot=True, by_entity=True,
            named_only=("switch",), room_classes=WINDOW_COVERINGS,
            note="a named cover or switch; window coverings in a room")
_CLOSE = Row("HassTurnOff", ("cover", "switch"), NAMED_OR_ROOM, domain_slot=True, by_entity=True,
             named_only=("switch",), room_classes=WINDOW_COVERINGS,
             note="a named cover or switch; window coverings in a room")
LOCKS_AND_DOORS: dict[tuple[str, str], tuple[Row, ...]] = {
    ("unlock", "Security"): (Row("HassTurnOff", ("lock",), NAMED, domain_slot=True, by_entity=True,
                                 note="a named lock only, by entity ID"),),
    ("open", "Climate"): (_OPEN,),
    ("close", "Climate"): (_CLOSE,),
    ("open", "Security"): (_OPEN,),
    ("close", "Security"): (_CLOSE,),
}
TABLE_WITH_LOCKS_AND_DOORS: dict[tuple[str, str], tuple[Row, ...]] = {**TABLE, **LOCKS_AND_DOORS}

# Pairs Hunch can answer that are handed back on purpose, with the reason. Any other pair without a row is
# handed back too ("no intent for this pair").
NO_INTENT: dict[tuple[str, str], str] = {
    ("unlock", "Security"): "unlocking is left to Home Assistant's own sentences",
    ("raise", "Climate"): "no built-in intent changes a set temperature by a step",
    ("lower", "Climate"): "no built-in intent changes a set temperature by a step",
    ("ask", "Lists"): "no built-in intent reads a list out",
    ("ask", "Climate"): "the two answers do not say which state is asked about",
    ("ask", "Lights"): "the two answers do not say which state is asked about",
    ("ask", "Security"): "the two answers do not say which state is asked about",
    ("ask", "Speakers & TVs"): "the two answers do not say which state is asked about",
    ("ask", "Other device"): "the two answers do not say which state is asked about",
    ("turn on", "Water"): "Water is not covered by Hunch Home",
    ("turn off", "Water"): "Water is not covered by Hunch Home",
}

# Home Assistant's built-in intents used here, with the slots each takes (OHF-Voice/intents, 15 Nov 2026).
KNOWN_INTENTS: dict[str, frozenset[str]] = {
    "HassTurnOn": frozenset({"name", "area", "floor", "domain", "device_class"}),
    "HassTurnOff": frozenset({"name", "area", "floor", "domain", "device_class"}),
    "HassLightSet": frozenset({"name", "area", "floor", "domain", "brightness", "color", "temperature"}),
    "HassClimateSetTemperature": frozenset({"name", "area", "floor", "temperature"}),
    "HassGetWeather": frozenset({"name"}),
    "HassListAddItem": frozenset({"item", "name"}),
    "HassListRemoveItem": frozenset({"item", "name"}),
    "HassShoppingListAddItem": frozenset({"item"}),
    "HassVacuumStart": frozenset({"name", "area", "floor"}),
    "HassVacuumReturnToBase": frozenset({"name", "area"}),
    "HassMediaPause": frozenset({"name", "area"}),
    "HassMediaUnpause": frozenset({"name", "area"}),
    "HassMediaNext": frozenset({"name", "area"}),
    "HassMediaPrevious": frozenset({"name", "area"}),
    "HassSetVolume": frozenset({"name", "area", "volume_level"}),
    "HassSetVolumeRelative": frozenset({"name", "area", "floor", "volume_step"}),
    "HassMediaPlayerMute": frozenset({"name", "area"}),
    "HassMediaPlayerUnmute": frozenset({"name", "area"}),
    "HassMediaSearchAndPlay": frozenset({"name", "area", "search_query", "media_class"}),
    "HassStartTimer": frozenset({"hours", "minutes", "seconds", "name", "conversation_command"}),
    "HassCancelTimer": frozenset({"start_hours", "start_minutes", "start_seconds", "name", "area"}),
    "HassIncreaseTimer": frozenset({"hours", "minutes", "seconds", "start_hours", "start_minutes",
                                    "start_seconds", "name", "area"}),
    "HassDecreaseTimer": frozenset({"hours", "minutes", "seconds", "start_hours", "start_minutes",
                                    "start_seconds", "name", "area"}),
    "HassTimerStatus": frozenset({"start_hours", "start_minutes", "start_seconds", "name", "area"}),
    "HassBroadcast": frozenset({"message"}),
}

# The slots each value fills (decide.py fills them from values.py).
VALUE_SLOTS: dict[str, tuple[str, ...]] = {
    BRIGHTNESS: ("brightness",), COLOUR: ("color",), TEMPERATURE: ("temperature",), VOLUME: ("volume_level",),
    VOLUME_UP: ("volume_step",), VOLUME_DOWN: ("volume_step",), ITEM: ("item",), REMOVED_ITEM: ("item",),
    DURATION: ("hours", "minutes", "seconds"), SEARCH: ("search_query",), MESSAGE: ("message",),
}


def rows_for(action: str, kind: str, table: dict[tuple[str, str], tuple[Row, ...]] | None = None
             ) -> tuple[Row, ...]:
    """The rows for a pair: its own, or else the action's rows for any kind; () when there are none."""
    rows = TABLE if table is None else table
    return rows.get((action, kind)) or rows.get((action, ANY_KIND)) or ()


def _has_word(words: list[str], wanted: tuple[str, ...]) -> bool:
    return any(word in wanted for word in words)


def choose(rows: tuple[Row, ...], words: list[str]) -> Row | None:
    """The first row whose words are in the sentence (a row with no words always applies)."""
    for row in rows:
        if not row.words or _has_word(words, row.words):
            return row
    return None
