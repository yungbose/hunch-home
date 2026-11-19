"""What the plug-in may see of the home, read from Home Assistant's registries (glue only).

Only entities exposed to Assist (Settings > Voice assistants > Expose) are included, with the names and aliases
Home Assistant itself matches on. The matching is in `hunch_logic.match`.

For a light it also reads, from the state as it is when the sentence arrives, whether it is on, whether it
can dim (any supported colour mode but "onoff", as Home Assistant's own `brightness_supported` has it) and its
brightness as a percentage, so a step up or down can be worked out (`hunch_logic.decide`).
"""

from __future__ import annotations

from homeassistant.components.conversation import DOMAIN as CONVERSATION_DOMAIN
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from .hunch_logic.match import Area, Home, Thing


@callback
def async_home_view(hass: HomeAssistant) -> Home:
    """The areas, and the entities exposed to the conversation agent, with their names and rooms."""
    areas = tuple(Area(area.id, (area.name, *sorted(area.aliases or ())))
                  for area in ar.async_get(hass).async_list_areas())
    entities, devices = er.async_get(hass), dr.async_get(hass)
    things = []
    for state in hass.states.async_all():
        if not async_should_expose(hass, CONVERSATION_DOMAIN, state.entity_id):
            continue
        entry = entities.async_get(state.entity_id)
        names = tuple(name for name in intent.async_get_entity_aliases(hass, entry, state=state) if name)
        area_id = entry.area_id if entry else None
        if entry and area_id is None and entry.device_id and (device := devices.async_get(entry.device_id)):
            area_id = dr.async_get_effective_area_id(hass, device)
        light = _light(state.state, state.attributes) if state.entity_id.startswith("light.") else (None,) * 3
        things.append(Thing(state.entity_id, names, area_id, state.attributes.get("device_class"), *light))
    return Home(areas, tuple(things), hass.config.units.temperature_unit)


# Home Assistant's colour modes that are not a brightness: "onoff", and "unknown" (light/const.py, 2026.11.0).
_NO_BRIGHTNESS = frozenset({"onoff", "unknown"})


def _light(state: str, attributes) -> tuple[str, bool | None, int | None]:
    """A light's state, whether it can dim (None when Home Assistant does not say) and its brightness from 1
    to 100 while on (Home Assistant's `brightness` runs from 0 to 255; an on light is never read as 0)."""
    modes = attributes.get("supported_color_modes")
    known = [mode for mode in modes or () if mode != "unknown"]
    dimmable = any(mode not in _NO_BRIGHTNESS for mode in known) if known else None
    raw = attributes.get("brightness")
    brightness = None
    if state == "on" and isinstance(raw, int | float) and not isinstance(raw, bool) and 0 <= raw <= 255:
        brightness = max(1, round(raw * 100 / 255))
    return state, dimmable, brightness


@callback
def async_device_area(hass: HomeAssistant, device_id: str | None, satellite_id: str | None) -> str | None:
    """The room of the device that heard the command, if Home Assistant knows it."""
    if satellite_id and (entry := er.async_get(hass).async_get(satellite_id)):
        if entry.area_id:
            return entry.area_id
        device_id = device_id or entry.device_id
    if device_id and (device := dr.async_get(hass).async_get(device_id)):
        return dr.async_get_effective_area_id(hass, device)
    return None
