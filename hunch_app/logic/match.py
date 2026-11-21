"""Finding the device and the room: the home's own names, matched against the sentence.

Hunch names the action and the kind of thing. It does not say which device or which room. This module finds
them among the names the home itself uses: the areas, and the entities exposed to the conversation agent, with
their names and aliases, restricted to the domains the kind of thing implies.

Home Assistant's own matching helper (`homeassistant.helpers.intent.async_match_targets`) matches a name it is
given exactly; it does not find a name inside a sentence. So this module finds the name, and the intent that the
plug-in fires passes it to Home Assistant, which matches it again and acts.

The rules, kept simple on purpose:

1. A name is found when its words appear in the sentence as whole words (case and punctuation ignored). A
   name that begins with "the" is also found without it: "the lamp" is found in "lamp off". The intent is
   still sent the name as the home spells it, since Home Assistant matches names exactly.
2. A name overlapping a longer name gives way to the longer one ("desk lamp" over "lamp"); equal lengths stay.
3. Two different names found in different places means two things: the sentence is handed back.
4. An area named in the sentence (outside the device's own name) narrows a name to that room; with no device
   named, it means every device of the domain in that room, where the row allows a room (for a row with
   room classes, only the devices of those classes).
5. With neither, a row that allows a room uses the room of the device that heard the command.
   With Locks and doors on, a room's name is never a name for something that opens the home: "unlock the
   garage", with a lock called "Garage" in the room Garage, is handed back (see `room_hides`).
6. When nothing matches, or more than one thing matches equally, the sentence is handed back.

A row may also reach a device of another domain by name only, when the name the sentence uses holds one of the
row's words: a lamp on a smart plug is a `switch`, and "turn on the lamp" may reach it when it is called
"Lamp". Its other names, and every switch whose name says nothing of a light, stay outside the answer's
kind. A room never reaches such a device.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .client import HandBack
from .table import NAMED, NAMED_OR_ROOM, OPTIONAL_NAME


@dataclass(frozen=True)
class Area:
    area_id: str
    names: tuple[str, ...]  # the area's name first, then its aliases


@dataclass(frozen=True)
class Thing:
    entity_id: str
    names: tuple[str, ...]  # the entity's name first, then its aliases
    area_id: str | None = None
    device_class: str | None = None
    # Read for lights only (None elsewhere, and when Home Assistant does not say): "on" or "off" (or
    # "unavailable"), whether it has a brightness, and its brightness from 1 to 100 while it is on.
    state: str | None = None
    dimmable: bool | None = None
    brightness: int | None = None

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


@dataclass(frozen=True)
class Home:
    """What the plug-in knows of the home: its areas, and the entities exposed to the conversation agent."""

    areas: tuple[Area, ...] = ()
    things: tuple[Thing, ...] = ()
    temperature_unit: str = "°C"

    def area_name(self, area_id: str | None) -> str | None:
        for area in self.areas:
            if area.area_id == area_id:
                return area.names[0] if area.names else area_id
        return None


@dataclass(frozen=True)
class Target:
    """What a command acts on. `name` and `area_id` become the intent's `name` and `area` slots."""

    entity_ids: tuple[str, ...]
    domains: tuple[str, ...]
    name: str | None = None
    area_id: str | None = None
    how: str = ""  # "named", "named room", "device's room" or "none"


def words_of(text: str) -> str:
    """Lower-case words separated by single spaces; punctuation and apostrophes become spaces."""
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    key: str    # the name in words_of form
    name: str   # the name as the home spells it
    owner: Area | Thing

    def gives_way_to(self, other: _Hit) -> bool:
        return self.start < other.end and other.start < self.end and len(self.key) < len(other.key)


def _keys(name: str) -> tuple[str, ...]:
    """The words a name is found by: its own, and without a leading "the" ("the lamp" is also "lamp")."""
    key = words_of(name)
    short = key.removeprefix("the ")
    return tuple(found for found in (key, short if short != key else "") if found)


def _find(sentence: str, key: str) -> list[tuple[int, int]]:
    padded, needle = f" {sentence} ", f" {key} "
    spans, start = [], padded.find(needle)
    while start != -1:
        spans.append((start, start + len(needle) - 1))
        start = padded.find(needle, start + 1)
    return spans


def _hits(sentence: str, owners: list[tuple[Area | Thing, tuple[str, ...]]]) -> list[_Hit]:
    hits = [_Hit(start, end, key, name, owner)
            for owner, names in owners for name in names for key in _keys(name)
            for start, end in _find(sentence, key)]
    return [hit for hit in hits if not any(hit.gives_way_to(other) for other in hits)]


def says_one_of(name: str, words: tuple[str, ...]) -> bool:
    """Whether a name holds one of the words as a whole word ("Lamp B" holds "lamp")."""
    return bool(set(words_of(name).split()).intersection(words))


def _overlaps(hit: _Hit, others: list[_Hit]) -> bool:
    return any(hit.start < other.end and other.start < hit.end for other in others)


def find_target(sentence: str, home: Home, domains: tuple[str, ...], mode: str,
                device_area_id: str | None = None, device_classes: tuple[str, ...] = (),
                named_only: tuple[str, ...] = (), also_named: tuple[str, ...] = (),
                name_words: tuple[str, ...] = (), room_classes: tuple[str, ...] = (),
                room_hides: Callable[[Thing], bool] | None = None) -> Target | HandBack:
    """The device or room a command acts on, or why the sentence is handed back.

    Devices of a domain in `named_only` are found only by name, never as part of a room. With `room_classes`,
    a room reaches only devices of those classes, while a name reaches any class. Devices of a domain in
    `also_named` are found only by a name holding one of `name_words`; their other names count as outside the
    answer's kind, and a room never reaches them. When a device `room_hides` picks out is matched on or inside
    a room's name in the sentence ("Garage" in "the garage" or "the garage hall"), the sentence is handed back
    as ambiguous; a longer name holding the room's ("the garage door") still names the device."""
    text = words_of(sentence)
    things = [thing for thing in home.things if thing.domain in domains
              and (not device_classes or thing.device_class in device_classes)]
    extra = [thing for thing in home.things if thing.domain in also_named and thing not in things]
    thing_hits = _hits(text, [(thing, thing.names) for thing in things]
                       + [(thing, tuple(name for name in thing.names if says_one_of(name, name_words)))
                          for thing in extra])
    room_hits = _hits(text, [(area, area.names) for area in home.areas])
    if room_hides is not None and any(room_hides(hit.owner) and any(
            room.start <= hit.start and hit.end <= room.end for room in room_hits) for hit in thing_hits):
        # The room or the device may be meant, and acting on either (or on a twin of the same name, which Home
        # Assistant could resolve to the device) is a guess: Home Assistant's own agent answers instead.
        return HandBack("ambiguous", "a lock, door or opener shares a room's name")
    other_hits = _hits(text, [(thing, thing.names) for thing in home.things if thing not in things + extra]
                       + [(thing, tuple(name for name in thing.names if not says_one_of(name, name_words)))
                          for thing in extra])
    # A named excluded cover must not fall through to a blind elsewhere in the same room, even when a
    # shorter alias of the blind overlaps the door's name.
    if device_classes and any(hit.owner.domain in domains for hit in other_hits):
        return HandBack("no match", "a named cover is not a known window covering")
    # An outside-kind name gives way only to a strictly longer overlapping eligible name.
    if any(not any(hit.gives_way_to(other) for other in thing_hits) for hit in other_hits):
        return HandBack("no match", "a named device is outside the answer's kind")
    found = {hit.owner.entity_id: hit.owner for hit in thing_hits if isinstance(hit.owner, Thing)}
    if len({hit.key for hit in thing_hits}) > 1 and len(found) > 1:
        names = ", ".join(sorted({hit.name for hit in thing_hits}))
        return HandBack("ambiguous", f"more than one device is named: {names}")

    area_hits = [hit for hit in room_hits if not _overlaps(hit, thing_hits)]
    named_areas = {hit.owner.area_id for hit in area_hits if isinstance(hit.owner, Area)}
    if len(named_areas) > 1:
        return HandBack("ambiguous", "more than one room is named")
    area_id = next(iter(named_areas), None)

    if thing_hits:
        chosen = list(found.values())
        name = max(thing_hits, key=lambda hit: (hit.end - hit.start, -hit.start)).name
        shared = len(chosen) > 1
        if area_id is not None:
            chosen = [thing for thing in chosen if thing.area_id == area_id]
            if not chosen:
                return HandBack("no match", f"nothing called {name!r} is in {home.area_name(area_id)!r}")
        elif shared and device_area_id is not None:
            here = [thing for thing in chosen if thing.area_id == device_area_id]
            chosen = here or chosen
        if len(chosen) > 1:
            return HandBack("ambiguous", f"more than one device is called {name!r}")
        thing = chosen[0]
        slot_area = area_id if area_id is not None else (thing.area_id if shared else None)
        return Target((thing.entity_id,), (thing.domain,), name=name, area_id=slot_area, how="named")

    if mode == OPTIONAL_NAME:
        if area_id is not None or len(things) != 1:
            return HandBack("no match", "no single weather service can be selected")
        return Target((things[0].entity_id,), (things[0].domain,), how="none")
    if mode == NAMED:
        return HandBack("no device", f"no {' or '.join(domains)} is named in the sentence")
    if mode != NAMED_OR_ROOM:
        raise ValueError(f"unknown target mode {mode!r}")

    how = "named room"
    if area_id is None:
        area_id, how = device_area_id, "device's room"
    if area_id is None:
        return HandBack("no room", "no device or room is named, and the device that heard it has no room")
    in_room = [thing for thing in things if thing.area_id == area_id and thing.domain not in named_only
               and (not room_classes or thing.device_class in room_classes)]
    if not in_room:
        what = (", ".join(room_classes[:-1]) + f" or {room_classes[-1]}" if room_classes
                else " or ".join(domains))
        return HandBack("no match", f"no {what} in {home.area_name(area_id)!r}")
    return Target(tuple(thing.entity_id for thing in in_room), tuple(sorted({t.domain for t in in_room})),
                  area_id=area_id, how=how)
