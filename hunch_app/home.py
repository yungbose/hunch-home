"""What the app knows of the home: the rules' `Home`, built from Home Assistant's own lists and kept fresh.

Read over the websocket (`ha.py`) and held in memory:

- `homeassistant/expose_entity/list`: only entities exposed to Assist (`conversation`) are included;
- `config/entity_registry/list` and `config/entity_registry/get_entries` (asked in batches of `BATCH`): rooms,
  devices, other names (aliases) and device classes;
- `config/device_registry/list` and `config/area_registry/list`: devices' rooms and names, rooms' names and
  their other names;
- `get_states` once, then every `state_changed` event: each entity's state, which for a light says whether it
  is on, whether it can dim and how bright it is (as the plug-in's `homeview.py` read it);
- `get_config`: the home's temperature unit.

**Names are the ones Home Assistant itself matches on** (`homeassistant.helpers.intent.async_get_entity_aliases`
at 2026.11.2, which the plug-in called directly). For an entity in the registry, its names are its alias list
in order, where the null at the head stands for the entity's computed full name: the parent device's name (when
the device is a child device named after its parent), then the device's name (when the entity has no room of
its own, so that its `next_name_part` is "device"), then the entity's own name (the name the owner gave it, or
else its original name). An entity with no alias list has no names, as in Home Assistant. An entity with no
registry entry is known by its state's friendly name.

The registry part is read again when Home Assistant reports a change to the entity, device or area registry
(exposing or hiding a device changes the entity registry), when it has finished starting, when an entity it has
not seen before gets its first state, at most once a second, after every reconnection and every `REFRESH`
seconds in case a change went unreported. The app also reads it again when the page asks for its state and when
a sentence finds no device (`fresh`). The states follow the events; a refresh plays the changes that arrived
while it ran over what it read, so it never puts an older state back.

**Exposed by default.** `homeassistant/expose_entity/list` lists an entity only once Home Assistant has stored
an exposure setting for it (`exposed_entities.py` at 2026.11.2: the list reads stored settings only). Home
Assistant stores the default for every entity with a state when it has finished starting, and for each new
entity the moment it first gets a state (`conversation/default_agent.py`), so the list is complete except while
Home Assistant is starting. Hence the re-read on `homeassistant_started` and on an entity's first state. The
public interface has no call that gives the default without storing it, and Home Assistant's own exposure check
on every intent stays the final word.

It also works out the room a sentence came from: a satellite's own room, else its device's room, else the
device's room (a child device without a room takes its parent's), as the plug-in's `async_device_area` did.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .ha import HomeAssistant, Unavailable
from .logic.match import Area, Home, Target, Thing, find_target, words_of
from .logic.table import TABLE, WINDOW_COVERINGS

LOG = logging.getLogger("hunch_app.home")

BATCH = 100
REFRESH = 600.0   # seconds
DEBOUNCE = 1.0    # seconds
REGISTRY_EVENTS = ("entity_registry_updated", "device_registry_updated", "area_registry_updated",
                   "homeassistant_started")
# Home Assistant's colour modes that are not a brightness: "onoff", and "unknown" (light/const.py, 2026.11.0).
_NO_BRIGHTNESS = frozenset({"onoff", "unknown"})
# Domains where a device with no room is worth fixing (a shopping list or the weather has no room).
ROOM_MATTERS = frozenset({"light", "switch", "fan", "cover", "lock", "climate", "media_player", "vacuum",
                          "valve", "humidifier", "water_heater", "input_boolean"})
# Devices with no room are one "worth fixing" entry from this many on; fewer keep an entry each.
GROUP_FROM = 3
# Kinds counted by device class too, since the class changes the word the page uses (a garage door, not a
# "blind or door"; a TV). A switch that is an outlet is still a switch, so switches are counted by kind alone.
CLASS_NAMES_IT = frozenset({"cover", "media_player"})


def light_of(state: str | None, attributes: Mapping) -> tuple[str | None, bool | None, int | None]:
    """A light's state, whether it can dim (None when Home Assistant does not say) and its brightness from 1 to
    100 while on (Home Assistant's `brightness` runs from 0 to 255; an on light is never read as 0)."""
    modes = attributes.get("supported_color_modes")
    known = [mode for mode in modes or () if mode != "unknown"] if isinstance(modes, list) else []
    dimmable = any(mode not in _NO_BRIGHTNESS for mode in known) if known else None
    raw = attributes.get("brightness")
    brightness = None
    if state == "on" and isinstance(raw, int | float) and not isinstance(raw, bool) and 0 <= raw <= 255:
        brightness = max(1, round(raw * 100 / 255))
    return state, dimmable, brightness


def effective_area(device: Mapping | None, devices: Mapping[str, Mapping]) -> str | None:
    """A device's room; a child device without one takes its parent's (`async_get_effective_area_id`)."""
    if device is None:
        return None
    if device.get("area_id"):
        return device["area_id"]
    parent = devices.get(device.get("parent_device_id") or "")
    return parent.get("area_id") if parent else None


def computed_name(row: Mapping, devices: Mapping[str, Mapping]) -> str:
    """The entity's full name as Home Assistant computes it for a null alias (entity_registry.py,
    `async_get_full_entity_name`: the parent device, the device and the entity, each only where the
    `next_name_part` links reach it). The websocket's `original_name` is already without the device's name."""
    entity_name = row.get("name") if row.get("name") is not None else row.get("original_name")
    device_name = parent_name = None
    device = devices.get(row.get("device_id") or "")
    if device is not None and row.get("next_name_part") == "device":
        device_name = device.get("name_by_user") or device.get("name")
        parent = devices.get(device.get("parent_device_id") or "")
        if parent is not None and device.get("next_name_part") == "parent_device":
            parent_name = parent.get("name_by_user") or parent.get("name")
    return " ".join(part for part in (parent_name, device_name, entity_name) if part)


def names_of(entity_id: str, full: Mapping | None, row: Mapping | None, state: Mapping | None,
             devices: Mapping[str, Mapping]) -> tuple[str, ...]:
    """The names Home Assistant matches this entity on, in its order, without empty ones."""
    if full is None:
        friendly = ((state or {}).get("attributes") or {}).get("friendly_name")
        name = friendly if isinstance(friendly, str) and friendly.strip() else entity_id.split(".", 1)[1].replace(
            "_", " ")
        return (name.strip(),)
    names = []
    for alias in full.get("aliases") or ():
        if alias is None:
            alias = computed_name({**(row or {}), **full}, devices)
        if isinstance(alias, str) and alias.strip():
            names.append(alias.strip())
    return tuple(dict.fromkeys(names))


@dataclass
class Snapshot:
    """The registry part of the home, as last read."""

    areas: tuple[Area, ...] = ()
    area_names: dict[str, str] = field(default_factory=dict)
    exposed: dict[str, dict] = field(default_factory=dict)   # entity id -> {names, area_id, device_class}
    entity_rows: dict[str, dict] = field(default_factory=dict)
    devices: dict[str, dict] = field(default_factory=dict)
    temperature_unit: str = "°C"


def _lights_for(sentence: str, home: Home) -> Target | None:
    """What the rules' own matcher finds for a sentence asking to turn on a light, typed with no room."""
    row = TABLE[("turn on", "Lights")][0]
    found = find_target(sentence, home, row.domains, row.target, None, row.device_classes, row.named_only,
                        row.also_named, row.name_words, row.room_classes)
    return found if isinstance(found, Target) else None


def _reaches(light: Thing, home: Home, room: str | None) -> bool:
    """Whether the try box's sentences, exactly as the page writes them, reach this light: "turn on the <name>"
    finds it and nothing else, and "it's a bit dark in the <room>" (when it has a room) finds its room."""
    named = _lights_for(f"turn on the {light.names[0].lower()}", home)
    if named is None or named.entity_ids != (light.entity_id,):
        return False
    if room is None:
        return True
    in_room = _lights_for(f"it's a bit dark in the {room.lower()}", home)
    return in_room is not None and in_room.area_id == light.area_id and light.entity_id in in_room.entity_ids


class HomeView:
    """The home as the rules see it, refreshed from Home Assistant."""

    def __init__(self, ha: HomeAssistant, on_change: Callable[[dict], None] | None = None) -> None:
        self.ha = ha
        self.snapshot = Snapshot()
        self.states: dict[str, dict] = {}
        self.on_change = on_change
        self.state_listeners: list[Callable[[dict], None]] = []
        self.loaded = False
        self._pending: asyncio.Task | None = None
        self._timer: asyncio.Task | None = None
        self._reading: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._arrived: list | None = None   # state changes seen while a refresh runs
        self.read_at = 0.0                  # when the home was last read whole (monotonic seconds)
        ha.on_event("state_changed", self._state_changed)
        for event_type in REGISTRY_EVENTS:
            ha.on_event(event_type, self._registry_changed)
        ha.on_connect(self.refresh)

    # Reading.

    async def refresh(self) -> None:
        """Read everything again. Raises `Unavailable` when Home Assistant cannot be reached."""
        async with self._lock:
            # State changes that arrive while this runs are kept and played over the states read here, so
            # an older reading never replaces a newer change.
            self._arrived = []
            try:
                await self._read_all()
            finally:
                self._arrived = None
            self.read_at = time.monotonic()
        LOG.info("read the home: %d devices exposed to Assist", len(self.snapshot.exposed))
        if self.on_change is not None:
            self.on_change(self.summary())

    async def _read_all(self) -> None:
        exposed_list = await self.ha.call("homeassistant/expose_entity/list")
        rows = await self.ha.call("config/entity_registry/list")
        devices = await self.ha.call("config/device_registry/list")
        areas = await self.ha.call("config/area_registry/list")
        states = await self.ha.call("get_states")
        try:
            config = await self.ha.call("get_config")
        except Exception:  # noqa: BLE001 - the unit is a nicety; Celsius when it cannot be read
            config = {}
        exposed_ids = sorted(entity_id for entity_id, to in
                             ((exposed_list or {}).get("exposed_entities") or {}).items()
                             if isinstance(to, Mapping) and to.get("conversation"))
        full: dict[str, dict | None] = {}
        for start in range(0, len(exposed_ids), BATCH):
            part = await self.ha.call("config/entity_registry/get_entries",
                                      entity_ids=exposed_ids[start:start + BATCH])
            full.update(part or {})
        fresh = {s["entity_id"]: s for s in states or () if isinstance(s, Mapping) and "entity_id" in s}
        for entity_id, new_state in self._arrived or ():
            if new_state is None:
                fresh.pop(entity_id, None)
            else:
                fresh[entity_id] = new_state
        self.states = fresh
        self.snapshot = self._build(exposed_ids, rows or [], devices or [], areas or [], full, config or {})
        self.loaded = True

    def _build(self, exposed_ids, rows, devices, areas, full, config) -> Snapshot:
        device_map = {d["id"]: d for d in devices if isinstance(d, Mapping) and "id" in d}
        row_map = {r["entity_id"]: r for r in rows if isinstance(r, Mapping) and "entity_id" in r}
        area_list = tuple(Area(a["area_id"], (a["name"], *sorted(a.get("aliases") or ())))
                          for a in areas if isinstance(a, Mapping) and a.get("area_id") and a.get("name"))
        exposed = {}
        for entity_id in exposed_ids:
            entry = full.get(entity_id)
            row = row_map.get(entity_id)
            if entry is None and row is None and entity_id not in self.states:
                continue  # exposed but gone
            names = names_of(entity_id, entry, row, self.states.get(entity_id), device_map)
            source = {**(row or {}), **(entry or {})}
            area_id = source.get("area_id") or effective_area(device_map.get(source.get("device_id") or ""),
                                                               device_map)
            exposed[entity_id] = {"names": names, "area_id": area_id, "device_id": source.get("device_id")}
        unit = ((config.get("unit_system") or {}).get("temperature")) if isinstance(config, Mapping) else None
        return Snapshot(area_list, {a.area_id: a.names[0] for a in area_list}, exposed, row_map, device_map,
                        unit if isinstance(unit, str) and unit else "°C")

    def home(self) -> Home:
        """The rules' `Home` as it is now (states included)."""
        things = []
        for entity_id, info in self.snapshot.exposed.items():
            state = self.states.get(entity_id) or {}
            attributes = state.get("attributes") or {}
            light = light_of(state.get("state"), attributes) if entity_id.startswith("light.") else (None,) * 3
            device_class = attributes.get("device_class")
            things.append(Thing(entity_id, info["names"], info["area_id"],
                                device_class if isinstance(device_class, str) else None, *light))
        return Home(self.snapshot.areas, tuple(things), self.snapshot.temperature_unit)

    def name_of(self, entity_id: str) -> str:
        info = self.snapshot.exposed.get(entity_id)
        if info and info["names"]:
            return info["names"][0]
        friendly = ((self.states.get(entity_id) or {}).get("attributes") or {}).get("friendly_name")
        return friendly if isinstance(friendly, str) else entity_id

    def room_for(self, device_id: str | None, satellite_id: str | None) -> str | None:
        """The room (area id) of the device or satellite a sentence came from, if Home Assistant knows it."""
        devices = self.snapshot.devices
        if satellite_id and (row := self.snapshot.entity_rows.get(satellite_id)):
            if row.get("area_id"):
                return row["area_id"]
            device_id = device_id or row.get("device_id")
        if device_id:
            return effective_area(devices.get(device_id), devices)
        return None

    def area_id_for(self, room: str | None) -> str | None:
        """An area id from an area id or a room's name (or another name of it), any case."""
        if not room:
            return None
        key = words_of(room)
        for area in self.snapshot.areas:
            if room == area.area_id or key in {words_of(name) for name in area.names}:
                return area.area_id
        return None

    # Keeping it fresh.

    def _state_changed(self, event: dict) -> None:
        data = event.get("data") or {}
        entity_id, new = data.get("entity_id"), data.get("new_state")
        if not isinstance(entity_id, str):
            return
        if new is None:
            self.states.pop(entity_id, None)
        elif isinstance(new, Mapping):
            if data.get("old_state") is None and entity_id not in self.snapshot.exposed and self.loaded:
                self._registry_changed(event)   # a new entity: Home Assistant has just stored its exposure
            self.states[entity_id] = dict(new)
        if self._arrived is not None:
            self._arrived.append((entity_id, dict(new) if isinstance(new, Mapping) else None))
        for listener in self.state_listeners:
            listener(event)

    def _registry_changed(self, event: dict) -> None:
        if self._pending is None or self._pending.done():
            self._pending = asyncio.get_running_loop().create_task(self._refresh_soon())

    async def _refresh_soon(self) -> None:
        await asyncio.sleep(DEBOUNCE)
        try:
            await self.refresh()
        except Exception as error:  # noqa: BLE001 - the next change or the timer tries again
            LOG.warning("could not read the home again (%s)", type(error).__name__)

    async def fresh(self, older_than: float, limit: float) -> bool:
        """Read the home again unless it was read in the last `older_than` seconds, waiting at most `limit`
        seconds (the reading carries on if it takes longer). True when the home is that fresh now."""
        if self.loaded and time.monotonic() - self.read_at < older_than:
            return True
        if self._reading is None or self._reading.done():
            self._reading = asyncio.get_running_loop().create_task(self.refresh())
        try:
            await asyncio.wait_for(asyncio.shield(self._reading), limit)
        except TimeoutError:
            return False
        except Exception as error:  # noqa: BLE001 - the home as last read is used
            LOG.warning("could not read the home again (%s)", type(error).__name__)
            return False
        return True

    def start_timer(self) -> None:
        if self._timer is None:
            self._timer = asyncio.get_running_loop().create_task(self._every())

    async def _every(self) -> None:
        while True:
            await asyncio.sleep(REFRESH)
            try:
                await self.refresh()
            except Unavailable:
                pass  # the reconnection reads it again
            except Exception as error:  # noqa: BLE001 - the next timer tries again
                LOG.warning("could not read the home again (%s)", type(error).__name__)

    async def stop(self) -> None:
        for task in (self._pending, self._timer, self._reading):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._pending = self._timer = self._reading = None

    # What the page shows.

    def summary(self) -> dict:
        """The home as the page shows it (`web.Backend`): counts by kind (and by device class where that changes
        the word people use: a garage door, a TV) and by room, and what is worth fixing:
        `{"type": "no_room" | "same_name" | "lock_named_like_room", "names": [name], "room"?, "rooms"?}`, one
        entry per device, except that devices with no room are one entry with every name and their `count` when
        there are more than `GROUP_FROM - 1` of them (a new home with no rooms yet)."""
        home = self.home()
        rooms = self.snapshot.area_names
        by_kind = Counter((thing.domain, thing.device_class if thing.domain in CLASS_NAMES_IT else None)
                          for thing in home.things)
        by_room = Counter(thing.area_id for thing in home.things if thing.area_id)
        fix: list[dict] = []
        no_room = [thing.names[0] if thing.names else thing.entity_id
                   for thing in sorted(home.things, key=lambda t: (t.names[0] if t.names else t.entity_id).casefold())
                   if thing.area_id is None and thing.domain in ROOM_MATTERS]
        if len(no_room) >= GROUP_FROM:
            fix.append({"type": "no_room", "count": len(no_room), "names": no_room})
        else:
            fix += [{"type": "no_room", "count": 1, "names": [name]} for name in no_room]
        named: dict[str, list] = {}
        for thing in home.things:
            for key in dict.fromkeys(words_of(name) for name in thing.names):
                named.setdefault(key, []).append(thing)
        for key, things in sorted(named.items()):
            if len(things) > 1:
                shown = next(name for name in things[0].names if words_of(name) == key)
                fix.append({"type": "same_name", "names": [shown],
                            "rooms": [rooms[t.area_id] for t in things if t.area_id in rooms]})
        room_keys = {words_of(name): area.area_id for area in home.areas for name in area.names}
        for thing in home.things:
            opens = thing.domain == "lock" or (thing.domain == "cover"
                                                and thing.device_class not in WINDOW_COVERINGS)
            clash = next((name for name in thing.names if words_of(name) in room_keys), None)
            if opens and clash is not None:
                fix.append({"type": "lock_named_like_room", "names": [thing.names[0]],
                            "room": rooms.get(room_keys[words_of(clash)], clash)})
        kinds = [{"kind": kind, "count": count, **({"device_class": cls} if cls else {})}
                 for (kind, cls), count in sorted(by_kind.items(), key=lambda item: (item[0][0], item[0][1] is None,
                                                                                     item[0][1] or ""))]
        # The try box's examples: the first exposed light by name that the page's sentences reach and nothing else
        # (`_reaches`); None when there is none.
        lights = sorted((thing for thing in home.things if thing.domain == "light" and thing.names
                         and thing.state != "unavailable"), key=lambda thing: thing.names[0].casefold())
        example = next((thing for thing in lights if _reaches(thing, home, rooms.get(thing.area_id))), None)
        return {"devices": len(home.things),
                "kinds": kinds,
                "rooms": [{"name": area.names[0], "count": by_room.get(area.area_id, 0)} for area in home.areas],
                "fix": fix,
                "example_light": example.names[0] if example else None,
                "example_room": rooms.get(example.area_id) if example and example.area_id else None}
