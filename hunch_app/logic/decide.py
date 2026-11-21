"""The whole decision for one sentence: a plan for Home Assistant to carry out, or a reason to hand it back.

`decide` takes the sentence, Hunch's answer (or the reason there is none), and what the plug-in knows of the
home. It returns a `Plan` (an intent and its slots) or a `HandBack`. Every case of doubt is a `HandBack`:
"not for me", an answer below the cut-off, a pair of answers with no intent, a value that cannot be read, a
device or room that cannot be found or is found more than once. The glue then gives the sentence to Home
Assistant's own agent, which answers as if the plug-in were not there.

Raise and lower for Lights with no brightness in the sentence ("brighter", "dim the lights") are worked out
from what the home view says of each light found: one call per light, by entity ID. A light that can dim gets
`HassLightSet` with a brightness `STEP` points up or down from its present level (an off light counts as 0, so
raise turns it on at `STEP`); lower never goes below `FLOOR`, never raises a light already below it, and
leaves an off light off. A light that cannot dim gets `HassTurnOn` for raise and `HassTurnOff` for lower; so
does a switch named by a name that says lamp or light. A light whose state or dimming Home Assistant does not
report hands the whole sentence back.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import values
from .client import NOT_FOR_ME, Answer, HandBack
from .match import Home, Thing, find_target, words_of
from .table import (
    ACTIONS,
    ACTIONS_1_1,
    BRIGHTNESS,
    COLOUR,
    DURATION,
    FLOOR,
    ITEM,
    KINDS,
    KNOWN_INTENTS,
    MESSAGE,
    NO_INTENT,
    NO_TARGET,
    REMOVED_ITEM,
    SEARCH,
    STEP,
    TABLE,
    TABLE_WITH_LOCKS_AND_DOORS,
    TEMPERATURE,
    VOLUME,
    VOLUME_DOWN,
    VOLUME_UP,
    WINDOW_COVERINGS,
    Row,
    choose,
    rows_for,
)


@dataclass(frozen=True)
class Plan:
    """An intent to fire, with plain slot values (the glue wraps each as `{"value": ...}`)."""

    intent: str
    slots: dict[str, object] = field(default_factory=dict)
    entity_ids: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    action: str = ""
    kind: str = ""
    how: str = ""  # how the device or room was found
    by_entity: bool = False  # restricted covers are always addressed individually, including room commands
    # Each call's own intent and slots, by entity ID, when the devices found need different calls (a step up
    # or down in brightness). Empty for every other plan, which `intent` and `slots` describe in full.
    calls: tuple[tuple[str, dict[str, object]], ...] = ()

    def ha_slots(self) -> dict[str, dict[str, object]]:
        """The slots in the form `homeassistant.helpers.intent.async_handle` takes."""
        return {name: {"value": value} for name, value in self.slots.items()}

    def ha_slot_sets(self) -> tuple[dict[str, dict[str, object]], ...]:
        """One intent call per restricted cover; other plans use one call with their usual slots."""
        if self.calls:
            return tuple(slots for _, slots in self.ha_calls())
        if self.by_entity:
            return tuple(self.ha_slots() | {"name": {"value": entity_id}} for entity_id in self.entity_ids)
        return (self.ha_slots(),)

    def ha_calls(self) -> tuple[tuple[str, dict[str, dict[str, object]]], ...]:
        """Every intent call to make, in order, with its slots in Home Assistant's form."""
        if self.calls:
            return tuple((intent, {name: {"value": value} for name, value in slots.items()})
                         for intent, slots in self.calls)
        return tuple((self.intent, slots) for slots in self.ha_slot_sets())


def _single(slot: str, value: object | None) -> dict[str, object] | None:
    return None if value is None else {slot: value}


def _search(sentence: str) -> dict[str, object] | None:
    query = values.search_query(sentence)
    return None if query is None or query in values.VAGUE_MEDIA else {"search_query": query}


VALUE_READERS: dict[str, Callable[[str], dict[str, object] | None]] = {
    BRIGHTNESS: lambda s: _single("brightness", values.percentage(s)),
    COLOUR: lambda s: _single("color", values.colour(s)),
    TEMPERATURE: lambda s: _single("temperature", values.temperature(s)),
    VOLUME: lambda s: _single("volume_level", values.percentage(s)),
    VOLUME_UP: lambda s: _single("volume_step", values.volume_step(s, "up")),
    VOLUME_DOWN: lambda s: _single("volume_step", values.volume_step(s, "down")),
    ITEM: lambda s: _single("item", values.list_item(s)),
    REMOVED_ITEM: lambda s: _single("item", values.removed_item(s)),
    DURATION: lambda s: dict(values.duration(s) or {}) or None,
    SEARCH: _search,
    MESSAGE: lambda s: _single("message", values.message(s)),
}

def _words(text: str, cancelled_by: frozenset[str] = frozenset()) -> dict[str, frozenset[str]]:
    return dict.fromkeys(text.split(), cancelled_by)


# These word tables are deliberately small and English only. They catch the plainest mistakes; they do not
# prove that the sentence and the answer agree.
#
# Words that name what the open, close and lock rows do. An on or off answer to a sentence holding one of them
# disagrees with it ("close the gate" answered "turn on"). "shut off" and "shut down" are ordinary ways to say
# off, so for "turn off" a "shut" is cancelled by "off" or "down".
_OPENING_WORDS = (_words("open opens opened close closes closed lock locks locked unlock unlocks unlocked")
                  | _words("shut", frozenset({"off", "down"})))
# For each action: each word that says the opposite, with the words that cancel that word's check
# ("turn on the kettle off and on" is not a disagreement).
ACTION_CONFLICTS: dict[str, dict[str, frozenset[str]]] = {
    "lock": _words("unlock unlocked open let"),
    "unlock": _words("lock locks locked", frozenset({"unlock", "unlocked"})),
    "turn on": _OPENING_WORDS | _words("off", frozenset({"on"}))
               | _words("kill disable deactivate stop cut shut out"),
    "turn off": _OPENING_WORDS | _words("on", frozenset({"off"})) | _words("start enable activate resume"),
    "open": _words("close closed shut"),
    "close": _words("open"),
    # A light raised or lowered with no number moves on Hunch's word alone, so a plain light word for the
    # other direction ("dim the lights" answered raise) hands back too.
    "raise": _words("down lower quieter dim dimmer darker"),
    "lower": _words("up raise louder brighter brighten"),
}

# Words that mark a device as a way into the home. A switch, scene or other device that can do anything when
# turned on, with one of these in its name, its entity ID or the sentence, is left to Home Assistant's own
# sentences, which need the person to say exactly "turn on" or "turn off". Applied only when the Locks and
# doors setting is switched off.
ENTRY_WORDS = frozenset({
    "door", "doors", "gate", "gates", "garage", "garages", "opener", "openers", "latch", "latches", "lock", "locks",
    "bolt", "bolts", "entry", "entries", "entrance", "entrances", "shutter", "shutters", "buzzer", "buzzers",
    "intercom", "intercoms",
})
# Domains whose "on" can do anything, an entry included.
GENERIC_DOMAINS = frozenset({"switch", "input_boolean", "script", "scene", "button", "input_button", "automation"})
ENTRY_REASON = "entry devices are left to Home Assistant's own sentences"


def _opens_the_home(thing: Thing, action: str, words: list[str]) -> bool:
    """With Locks and doors on, what a room's name in the sentence can never name (0.1.1): a lock being
    unlocked; a door, gate, garage door or other cover that is not a window covering, or a switch, being opened
    or closed; and a switch, scene or the like that the setting-off entry check would refuse."""
    if thing.domain == "lock":
        return action == "unlock"
    if action in {"open", "close"} and thing.domain in {"cover", "switch"}:
        return thing.domain == "switch" or thing.device_class not in WINDOW_COVERINGS
    return thing.domain in GENERIC_DOMAINS and bool(
        ENTRY_WORDS & (set(words) | _tokens(" ".join((thing.entity_id, *thing.names)))))


def _disagrees(action: str, words: list[str]) -> bool:
    present = set(words)
    return any(word in present and not cancelled_by & present
               for word, cancelled_by in ACTION_CONFLICTS.get(action, {}).items())


def _tokens(text: str) -> set[str]:
    """Whole words of a name or an entity ID ("switch.gate_opener" gives switch, gate and opener)."""
    return set(re.split(r"[\W_]+", text.casefold())) - {""}


def _without_names(sentence: str, home: Home, entity_ids: tuple[str, ...]) -> str:
    """The sentence without the names of the devices found and of every room: "dim Lamp 2" names no level."""
    names = [name for thing in home.things if thing.entity_id in entity_ids for name in thing.names]
    names += [name for area in home.areas for name in area.names]
    for name in sorted(names, key=len, reverse=True):
        sentence = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " ", sentence, flags=re.IGNORECASE)
    return sentence


def _step(answer: Answer, home: Home, entity_ids: tuple[str, ...], how: str) -> Plan | HandBack:
    """A step up or down for each light found, one call each by entity ID (see the module's docstring)."""
    up = answer.action == "raise"
    calls: list[tuple[str, dict[str, object]]] = []
    for entity_id in entity_ids:
        thing = next(thing for thing in home.things if thing.entity_id == entity_id)
        if thing.dimmable is None or thing.state not in {"on", "off"}:
            return HandBack("needs a value",
                            f"Home Assistant does not say whether {entity_id} is on or can dim")
        if not thing.dimmable:
            calls.append(("HassTurnOn" if up else "HassTurnOff", {"name": entity_id, "domain": ["light"]}))
            continue
        if thing.state == "off":
            if up:
                calls.append(("HassLightSet", {"name": entity_id, "brightness": STEP}))
            continue  # lowering an off light leaves it off
        if thing.brightness is None or not 1 <= thing.brightness <= 100:
            return HandBack("needs a value", f"Home Assistant does not say how bright {entity_id} is")
        level = thing.brightness
        # Never 0 (Home Assistant turns a light off at 0), never below the floor, never up when lowering.
        brightness = min(100, level + STEP) if up else min(level, max(FLOOR, level - STEP))
        calls.append(("HassLightSet", {"name": entity_id, "brightness": brightness}))
    if not calls:
        return HandBack("already off", "nothing to dim: every light found is off")
    intents = {intent for intent, _ in calls}
    intent = intents.pop() if len(intents) == 1 else "HassLightSet"
    return Plan(intent, {}, tuple(str(slots["name"]) for _, slots in calls), ("light",), answer.action,
                answer.kind, how, by_entity=True, calls=tuple(calls))


def decide(sentence: str, answer: Answer | HandBack, home: Home, device_area_id: str | None = None,
           table: dict[tuple[str, str], tuple[Row, ...]] | None = None,
           locks_and_doors: bool = True) -> Plan | HandBack:
    """A plan for the sentence, or why it goes back to Home Assistant's own agent.

    `locks_and_doors` is the experimental setting, on by default: it adds the unlock, open and close rows of
    `LOCKS_AND_DOORS` (a named lock, a named cover or switch, or the window coverings in a room). Switched off,
    unlock and entry covers hand back, and switches, scenes and other devices are refused for their entry
    words."""
    if isinstance(answer, HandBack):
        return answer
    if answer.declined:
        return HandBack("declined", "Hunch's best match was below its cut-off")
    if answer.action == NOT_FOR_ME:
        return HandBack("not for me")
    if answer.action not in ACTIONS + ACTIONS_1_1 or answer.kind not in KINDS:
        return HandBack("bad reply", "unknown action or kind")
    words = words_of(sentence).split()
    if table is None:
        table = TABLE_WITH_LOCKS_AND_DOORS if locks_and_doors else TABLE
    rows = rows_for(answer.action, answer.kind, table)
    # A pair with no row is handed back below whatever the sentence says.
    if rows and _disagrees(answer.action, words):
        return HandBack("the sentence and the answer disagree")
    if answer.kind != "Timers and alarms" and values.has_delay(sentence):
        return HandBack("delayed command", "the plug-in cannot delay an action")
    if not rows:
        reason = NO_INTENT.get((answer.action, answer.kind), "no intent for this pair")
        return HandBack("no intent", f"{answer.action} / {answer.kind}: {reason}")
    if answer.kind == "Lists" or answer.action == "announce":
        prefix = re.split(r"\b(?:add|put|remove|delete|take|cross|announce|broadcast)\b", sentence,
                          maxsplit=1, flags=re.IGNORECASE)[0]
        if set(words_of(prefix).split()) & {"not", "don", "dont", "never", "unless", "if"}:
            return HandBack("ambiguous", "negation or a condition before the requested operation")
    if (answer.kind not in {"Lists"} and answer.action != "announce"
            and (set(words) & {"not", "don", "dont", "never", "except", "unless", "if", "then", "instead"}
                 or re.search(r"\band\s+(?:turn|switch|set|lock|unlock|open|close|play|stop)\b", sentence,
                              flags=re.IGNORECASE))):
        return HandBack("ambiguous", "negation, a condition or more than one command")
    row = choose(rows, words)
    if row is None:
        needed = sorted({word for candidate in rows for word in candidate.words})
        return HandBack("no intent", f"{answer.action} / {answer.kind} needs one of: {', '.join(needed)}")
    if any(word in row.refuse_words for word in words):
        return HandBack("needs a value", "the sentence has words this intent cannot handle")
    if answer.kind == "Timers and alarms" and not values.simple_timer(sentence, row.needs == DURATION):
        return HandBack("needs a value", "only a simple unnamed timer is supported")
    if row.needs in {BRIGHTNESS, VOLUME, TEMPERATURE} and "by" in words:
        return HandBack("needs a value", "an absolute setting cannot represent a relative change")
    # Raise or lower for lights: a brightness in the sentence is set as it stands; with none, a step (below).
    level_change = row.needs == BRIGHTNESS and answer.action in {"raise", "lower"}
    if row.needs in {VOLUME_UP, VOLUME_DOWN} and "to" in words:
        return HandBack("needs a value", "an absolute volume cannot be represented by a relative step")

    slots: dict[str, object] = {}
    entity_ids: tuple[str, ...] = ()
    domains: tuple[str, ...] = row.domains
    how = "none"
    intent = row.intent
    on_off = row.intent in {"HassTurnOn", "HassTurnOff"}
    # With Locks and doors on, entry words no longer refuse anything: the setting is the owner's choice.
    generic = not locks_and_doors and (
        answer.kind == "Other device" or (on_off and bool(GENERIC_DOMAINS.intersection(row.domains))))
    if row.target != NO_TARGET and generic and ENTRY_WORDS.intersection(words):
        return HandBack(ENTRY_REASON)
    if row.target != NO_TARGET:
        room_hides = ((lambda thing: _opens_the_home(thing, answer.action, words)) if locks_and_doors
                      else None)
        target = find_target(sentence, home, row.domains, row.target, device_area_id, row.device_classes,
                             row.named_only, row.also_named, row.name_words, row.room_classes, room_hides)
        if isinstance(target, HandBack):
            return target
        found = [thing for thing in home.things if thing.entity_id in target.entity_ids]
        # A switch, scene or other device that can do anything when turned on, however it was reached (a
        # lamp's plug named in a Lights command included), is refused for an entry word with Locks and doors
        # off.
        checks_names = generic or (not locks_and_doors and GENERIC_DOMAINS.intersection(target.domains))
        if checks_names and (ENTRY_WORDS.intersection(words) or any(
                ENTRY_WORDS & _tokens(" ".join((thing.entity_id, *thing.names))) for thing in found)):
            return HandBack(ENTRY_REASON)
        entity_ids, how = target.entity_ids, target.how
        domains = target.domains or row.domains
        if target.name is not None:
            slots["name"] = target.name
        if target.area_id is not None:
            slots["area"] = target.area_id
            if "area" not in KNOWN_INTENTS[row.intent] and target.name is not None:
                # Pin a disambiguated list or weather service to the selected entity. Dropping its room
                # while sending a shared alias would let the intent pick a different device.
                slots["name"] = target.entity_ids[0]
        if row.domain_slot:
            slots["domain"] = list(domains)
        if row.device_classes or row.by_entity:
            # Never send an area-wide cover command. The glue fires one call per selected entity ID,
            # and the intent checks the allowed classes again, including for named covers.
            slots.pop("area", None)
            if len(entity_ids) == 1:
                slots["name"] = entity_ids[0]
            if row.device_classes and "device_class" in KNOWN_INTENTS[row.intent]:
                slots["device_class"] = list(row.device_classes)
            elif row.room_classes and target.how != "named" and "device_class" in KNOWN_INTENTS[row.intent]:
                # A room command (Locks and doors on) reaches window coverings only; Home Assistant checks too.
                slots["device_class"] = list(row.room_classes)
        if row.intent_by_domain and target.domains:
            by_domain = dict(row.intent_by_domain)
            intents = {by_domain.get(domain, row.intent) for domain in target.domains}
            if len(intents) > 1:
                return HandBack("ambiguous", "the devices found need different intents")
            intent = intents.pop()
            if intent != row.intent:
                slots.pop("domain", None)

    if level_change:
        named_level = values.names_a_level(_without_names(sentence, home, entity_ids))
        if domains != ("light",):
            # A lamp's plug, named: raise turns it on and lower turns it off; it has no brightness to set.
            if named_level:
                return HandBack("needs a value", "a switch has no brightness to set")
            intent = "HassTurnOn" if answer.action == "raise" else "HassTurnOff"
            slots = {name: value for name, value in slots.items() if name in KNOWN_INTENTS[intent]}
            return Plan(intent, slots, entity_ids, domains, answer.action, answer.kind, how)
        if not named_level:
            return _step(answer, home, entity_ids, how)
        if not {"to", "at"}.intersection(words):
            return HandBack("needs a value",
                            "raising or lowering brightness needs an explicit absolute setting")

    if row.needs is not None:
        value_sentence = sentence
        if row.needs in {BRIGHTNESS, COLOUR, TEMPERATURE, VOLUME, VOLUME_UP, VOLUME_DOWN}:
            # Digits and colour words in a device or room name are not values.
            value_sentence = _without_names(sentence, home, entity_ids)
        value = (_single("temperature", values.temperature(value_sentence, home.temperature_unit))
                 if row.needs == TEMPERATURE else VALUE_READERS[row.needs](value_sentence))
        if value is None:
            return HandBack("needs a value", f"{intent} needs a {row.needs}, and none could be read")
        slots.update(value)

    accepted = KNOWN_INTENTS.get(intent)
    if accepted is not None:
        # A room found for a to-do list or a weather service is not sent: those intents take no `area`. If the
        # name is shared, Home Assistant then reports it as such and the glue hands the sentence back.
        slots = {name: value for name, value in slots.items() if name in accepted}
    return Plan(intent, slots, entity_ids, domains, answer.action, answer.kind, how,
                by_entity=bool(row.device_classes) or row.by_entity)


def response_problem(response_type: str, has_failures: bool) -> HandBack | None:
    """An intent error or a partial failure must not be presented as success."""
    if response_type == "error" or has_failures:
        return HandBack("not carried out", "the intent returned an error or failed targets")
    return None
