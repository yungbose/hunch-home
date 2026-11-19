"""Was it undone? The cheapest honest sign that the plug-in did the wrong thing.

A plan that Home Assistant carried out is "undone" when, within `WINDOW` seconds, something other than that
plan puts a device it changed back the other way: a person at the switch, someone in the app, an automation
started by something else, or another voice command (the plug-in's own included). The rule, entity by entity:

What the plan did, and what counts as the other way (`way_of`, `is_other_way`):

- Turned on or off a light, switch, fan, input boolean, media player or thermostat: the other way is off or on.
  A media player counts as off when "off" or "standby", and on in any other known state; a thermostat counts
  as off when its mode is "off", and on in any other.
- Locked or unlocked a lock: the other way is unlocked, unlocking, open or opening, or locked or locking. A
  jammed lock says nothing.
- Opened or closed a cover: the other way is moving the other way ("closing" after an open, "opening" after a
  close), or arriving at the other end straight from the plan's end ("closed" just after "open", "open" just
  after "closed", for covers that report no movement). Home Assistant reads a cover stopped half way as
  "open", so "open" straight after "closing" says nothing either way: a stop of the plan's own closing is not
  a reversal, and a person who started closing an opened cover and stopped still moved it back.
- Paused or resumed a media player: the other way is playing, or paused.
- Set a light's brightness up or down (a step, or a level that is higher or lower than the light's level just
  before): the other way is a level back within `TOLERANCE` points of the level before, or beyond it; after a
  step up, turning the light off is the other way too, and after a step down it is not.
- Everything else has no meaningful opposite, and the plug-in does not guess: a colour, a temperature, a
  volume, the next track, a scene, a vacuum, a timer, a list, the weather, an announcement, and a level set to
  the light's present level or to a light whose level Home Assistant does not report. If nothing in a plan
  can be watched, the record says "not known" at once.

What does not count:

- The plan's own changes: every state change made under the turn's context (Home Assistant passes the
  sentence's context to each intent's service calls), so the plug-in's later calls in the same turn, a step
  that mixes on with a level, and the device's own late reports of them never count.
- An automation the plan itself set off directly: its context names the turn's context as its parent, so it is
  left out. An automation set off at one remove (by another automation, or by a sensor the action moved) has
  no such mark and is counted: unavoidable noise, which makes the count a little higher than the truth. So is a
  device that fails after its service call returned and reports its old state under a new context (a lock that
  does not catch, a cover that stalls and backs off).
- A device that bounces while reporting: a change the other way counts only if it is still the other way
  `SETTLE` seconds later; a report back the plan's way within that time, from anything at all, cancels it.
  "unavailable" and "unknown" say nothing either way.
- Changes after the window: only a change the other way within `WINDOW` seconds of the plan counts (it may
  then take `SETTLE` more to be sure of).

When the plug-in commands the same device again within the window (a later sentence, its own context): that
later command speaks for the device, and nothing else does from then on. If it puts the device back the other
way, the earlier entry is undone "by a later Hunch command" ("lights off", answered on, then "no, off"); if it
goes the same way, the earlier entry's watch on that device ends there. A person undoing the later command is
the later entry's business, not the earlier one's.

One entity put back is enough: the entry is undone, with the first device confirmed and what did it. A plan
with several devices, some of which cannot be watched, is judged on those that can.

The tracker below is pure: the glue feeds it state changes and the time, and asks it what has been decided.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .decide import Plan

WINDOW = 30.0   # seconds after the plan was carried out
SETTLE = 3.0    # seconds a change the other way must last
TOLERANCE = 2   # brightness points (Home Assistant's 0 to 255, read as 1 to 100, rounds by a point or so)

ON, OFF = "on", "off"
LOCKED, UNLOCKED = "locked", "unlocked"
OPEN, CLOSED = "open", "closed"
PLAYING, PAUSED = "playing", "paused"
UP, DOWN = "up", "down"

# Verdicts, as the record keeps them.
YES, NO, NOT_KNOWN = "yes", "no", "not known"
# Who put it back.
BY_LATER_HUNCH = "a later Hunch command"
BY_SIGNED_IN = "someone signed in to Home Assistant"  # the app, a dashboard or typed Assist
BY_OTHER = "something else: a switch, a device, an automation or a voice command"

ON_OFF_DOMAINS = frozenset({"light", "switch", "fan", "input_boolean", "media_player", "climate"})
SILENT = frozenset({"unavailable", "unknown"})
_MEDIA_OFF = frozenset({"off", "standby"})
_MEDIA_ON = frozenset({"on", "playing", "paused", "idle", "buffering"})
_LOCK_SHUT = frozenset({"locked", "locking"})
_LOCK_OPEN = frozenset({"unlocked", "unlocking", "open", "opening"})
_COVER_STATES = frozenset({"open", "opening", "closed", "closing"})


@dataclass(frozen=True)
class Seen:
    """One entity's state as the tracker reads it: the state, and for a light its brightness from 0 to 100
    (0 while off; None when on and Home Assistant gives no brightness)."""

    state: str
    level: int | None = None


def seen_from(entity_id: str, state: str, attributes: Mapping[str, object]) -> Seen:
    """A Home Assistant state, as the tracker reads it."""
    if not entity_id.startswith("light."):
        return Seen(state)
    if state == "off":
        return Seen(state, 0)
    raw = attributes.get("brightness")
    if state == "on" and isinstance(raw, int | float) and not isinstance(raw, bool) and 0 <= raw <= 255:
        return Seen(state, max(1, round(raw * 100 / 255)))
    return Seen(state)


@dataclass(frozen=True)
class Watch:
    """Which way the plan moved one entity; for a level, the level before."""

    entity_id: str
    way: str
    before: int | None = None


def way_of(intent: str, entity_id: str, slots: Mapping[str, object], before: Seen | None) -> Watch | None:
    """Which way one intent call moved one entity, or None when it has no meaningful opposite."""
    domain = entity_id.split(".", 1)[0]
    if intent in {"HassTurnOn", "HassTurnOff"}:
        on = intent == "HassTurnOn"
        if domain in ON_OFF_DOMAINS:
            return Watch(entity_id, ON if on else OFF)
        if domain == "lock":
            return Watch(entity_id, LOCKED if on else UNLOCKED)
        if domain == "cover":
            return Watch(entity_id, OPEN if on else CLOSED)
        return None
    if intent in {"HassMediaPause", "HassMediaUnpause"} and domain == "media_player":
        return Watch(entity_id, PAUSED if intent == "HassMediaPause" else PLAYING)
    level_only = "brightness" in slots and not {"color", "temperature"} & set(slots)
    if intent == "HassLightSet" and domain == "light" and level_only:
        target = slots["brightness"]
        if (before is None or before.level is None or isinstance(target, bool)
                or not isinstance(target, int | float)):
            return None
        if target > before.level:
            return Watch(entity_id, UP, before.level)
        if target < before.level:
            return Watch(entity_id, DOWN, before.level)
    return None


def watches_for(plan: Plan, before: Mapping[str, Seen]) -> tuple[Watch, ...]:
    """What can be watched of a carried-out plan: one `Watch` per entity with a meaningful opposite."""
    if plan.calls:
        calls = [(intent, str(slots.get("name")), slots) for intent, slots in plan.calls]
    else:
        calls = [(plan.intent, entity_id, plan.slots) for entity_id in plan.entity_ids]
    watches = {}
    for intent, entity_id, slots in calls:
        watch = way_of(intent, entity_id, slots, before.get(entity_id))
        if watch is not None:
            watches[entity_id] = watch
    return tuple(watches.values())


def _is_on(domain: str, state: str) -> bool | None:
    if domain == "media_player":
        return False if state in _MEDIA_OFF else True if state in _MEDIA_ON else None
    if domain == "climate":
        return state != "off"
    return {"on": True, "off": False}.get(state)


def is_other_way(watch: Watch, seen: Seen, previous: str | None = None) -> bool | None:
    """Whether a state is the other way from what the plan did; None when it says nothing either way.
    `previous` is the entity's state just before this one (used for covers)."""
    state = seen.state
    if state in SILENT:
        return None
    if watch.way in {ON, OFF}:
        on = _is_on(watch.entity_id.split(".", 1)[0], state)
        return None if on is None else on != (watch.way == ON)
    if watch.way in {LOCKED, UNLOCKED}:
        shut, open_ = (_LOCK_SHUT, _LOCK_OPEN) if watch.way == LOCKED else (_LOCK_OPEN, _LOCK_SHUT)
        return True if state in open_ else False if state in shut else None
    if watch.way in {OPEN, CLOSED}:
        end, moving_back, other_end = (("open", "closing", "closed") if watch.way == OPEN
                                       else ("closed", "opening", "open"))
        if state == moving_back:
            return True
        if state == other_end:
            # Straight from the plan's end, or the end of a move the other way. From the plan's own movement it
            # is a stop half way (Home Assistant reads a part-open cover as "open"), which says nothing.
            return True if previous in {end, moving_back} else None
        if state == "open" and previous == "closing":
            return None  # stopped while being closed: still part of the way back, or a stop of the plan's own
        return False if state in _COVER_STATES else None
    if watch.way == PAUSED:
        return state == "playing"
    if watch.way == PLAYING:
        return state == "paused"
    if watch.way == UP:
        if state == "off":
            return True
        return None if seen.level is None else seen.level <= (watch.before or 0) + TOLERANCE
    if watch.way == DOWN:
        if state == "off":
            return False
        return None if seen.level is None else seen.level >= (watch.before or 0) - TOLERANCE
    return None


@dataclass(frozen=True)
class Verdict:
    """What the tracker decided about one record entry."""

    key: str
    undone: str            # YES, NO or NOT_KNOWN
    by: str = ""           # for YES: BY_LATER_HUNCH, BY_SIGNED_IN or BY_OTHER
    entity_id: str = ""    # for YES: the entity put back


@dataclass
class _Open:
    context_id: str
    started: float
    watches: dict[str, Watch]
    last: dict[str, str] = field(default_factory=dict)                   # entity -> state last seen
    later: dict[str, str] = field(default_factory=dict)                  # entity -> later Hunch context
    pending: dict[str, tuple[float, str]] = field(default_factory=dict)  # entity -> (since, by)


class Tracker:
    """The open watches, one per record entry, decided by the rule in the module's docstring."""

    def __init__(self) -> None:
        self._open: dict[str, _Open] = {}

    def __contains__(self, key: str) -> bool:
        return key in self._open

    def turn(self, context_id: str, entity_ids: tuple[str, ...]) -> None:
        """A new plug-in command is about to act on these entities: from now on, it alone speaks for them in
        every earlier entry still being watched. Called before the command is carried out, so that its own
        state changes are already known as a later Hunch command when they arrive."""
        for watch in self._open.values():
            for entity_id in entity_ids:
                if entity_id in watch.watches and watch.context_id != context_id:
                    watch.later.setdefault(entity_id, context_id)

    def release(self, context_id: str) -> None:
        """A command announced by `turn` was not carried out after all: it speaks for nothing."""
        for watch in self._open.values():
            for entity_id in [entity_id for entity_id, later in watch.later.items() if later == context_id]:
                del watch.later[entity_id]

    def start(self, key: str, context_id: str, watches: tuple[Watch, ...], now: float,
              states: Mapping[str, str] | None = None) -> Verdict | None:
        """Start watching a carried-out plan; a `Verdict` of NOT_KNOWN at once when nothing can be watched.
        `states` are the entities' states as the plan left them, where known."""
        if not watches:
            return Verdict(key, NOT_KNOWN)
        self._open[key] = _Open(context_id, now, {watch.entity_id: watch for watch in watches},
                                dict(states or {}))
        return None

    def change(self, key: str, entity_id: str, seen: Seen, context_id: str | None, parent_id: str | None,
               user_id: str | None, now: float) -> None:
        """One state change of a watched entity, with the context Home Assistant gave it."""
        watch = self._open.get(key)
        if watch is None or entity_id not in watch.watches:
            return
        other = is_other_way(watch.watches[entity_id], seen, watch.last.get(entity_id))
        if seen.state not in SILENT:
            watch.last[entity_id] = seen.state
        if other is None:
            return
        later = watch.later.get(entity_id)
        by_later = later is not None and later in {context_id, parent_id}
        if not other:
            # Back the plan's way, from anything: a bounce is over. A later command going the same way ends
            # this entry's watch on the entity.
            watch.pending.pop(entity_id, None)
            if by_later:
                del watch.watches[entity_id]
            return
        if entity_id in watch.pending or now > watch.started + WINDOW:
            return
        if watch.context_id in {context_id, parent_id}:
            return  # the plan's own change, or an automation it set off directly
        if later is not None and not by_later:
            return  # after a later Hunch command, only that command speaks for the entity
        by = BY_LATER_HUNCH if by_later else BY_SIGNED_IN if user_id else BY_OTHER
        watch.pending[entity_id] = (now, by)

    def due(self, now: float) -> list[Verdict]:
        """The entries decided by `now`; each is decided once and then forgotten."""
        verdicts = []
        for key, watch in list(self._open.items()):
            confirmed = sorted((since, entity_id, by) for entity_id, (since, by) in watch.pending.items()
                               if now >= since + SETTLE)
            if confirmed:
                _, entity_id, by = confirmed[0]
                verdicts.append(Verdict(key, YES, by, entity_id))
            elif not watch.pending and (now >= watch.started + WINDOW or not watch.watches):
                verdicts.append(Verdict(key, NO))
            else:
                continue
            del self._open[key]
        return verdicts

    def next_due(self) -> float | None:
        """When `due` may next decide something, or None when nothing is being watched."""
        times = []
        for watch in self._open.values():
            if watch.pending:  # a change the other way decides it, once it has lasted
                times.append(min(since for since, _ in watch.pending.values()) + SETTLE)
            else:
                times.append(watch.started + WINDOW if watch.watches else watch.started)
        return min(times, default=None)

    def stop(self) -> list[Verdict]:
        """Stop every watch (an unload, a restart, a cleared record): each becomes NOT_KNOWN."""
        verdicts = [Verdict(key, NOT_KNOWN) for key in self._open]
        self._open.clear()
        return verdicts
