"""What the plug-in says after Home Assistant has carried out a plan.

Home Assistant's built-in intent handlers mostly leave the reply empty: its own agent fills it from its sentence
files' response templates. The plug-in fires the intents directly, so it says something short itself. When
Home Assistant does set a reply, the glue uses that instead.
"""

from __future__ import annotations

from .decide import Plan

_ON = {"cover": "Opened", "lock": "Locked", "scene": "Activated", "climate": "Turned on"}
_OFF = {"cover": "Closed", "lock": "Unlocked", "climate": "Turned off"}

_FIXED = {
    "HassMediaPause": "Paused.",
    "HassMediaUnpause": "Resumed.",
    "HassMediaNext": "Skipped to the next track.",
    "HassMediaPrevious": "Back to the previous track.",
    "HassMediaPlayerMute": "Muted.",
    "HassMediaPlayerUnmute": "Unmuted.",
    "HassCancelTimer": "Timer cancelled.",
    "HassBroadcast": "Announced.",
}

_WEATHER = {
    "clear-night": "clear", "partlycloudy": "partly cloudy", "lightning": "stormy",
    "lightning-rainy": "stormy with rain", "snowy-rainy": "sleety", "windy-variant": "windy and cloudy",
    "exceptional": "unusual",
}


def join(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    names = [name for name in names if name]
    if not names:
        return "it"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _duration(slots: dict[str, object]) -> str:
    parts = []
    for slot, unit in (("hours", "hour"), ("minutes", "minute"), ("seconds", "second")):
        amount = slots.get(slot)
        if isinstance(amount, int) and amount:
            parts.append(f"{amount} {unit}{'' if amount == 1 else 's'}")
    return join(parts)


def done(plan: Plan, names: list[str]) -> str:
    """A short reply for a plan Home Assistant carried out on the things called `names`."""
    slots, who = plan.slots, join(names)
    domain = plan.domains[0] if len(plan.domains) == 1 else ""
    if plan.intent in _FIXED:
        return _FIXED[plan.intent]
    if plan.action in {"open", "close"} and plan.intent in {"HassTurnOn", "HassTurnOff"}:
        # A gate or garage door wired as a switch was opened or closed, not "turned on".
        return f"{'Opened' if plan.action == 'open' else 'Closed'} {who}."
    if plan.intent == "HassTurnOn":
        return f"{_ON.get(domain, 'Turned on')} {who}."
    if plan.intent == "HassTurnOff":
        return f"{_OFF.get(domain, 'Turned off')} {who}."
    if plan.intent == "HassLightSet":
        if plan.calls:
            # A step up or down: one level for every light, or else which way they went.
            levels = {call.get("brightness") for _, call in plan.calls}
            if len(levels) == 1 and None not in levels:
                return f"Set {who} to {levels.pop()}%."
            return f"Made {who} {'brighter' if plan.action == 'raise' else 'dimmer'}."
        if "brightness" in slots:
            return f"Set {who} to {slots['brightness']}%."
        return f"Set {who} to {slots.get('color')}."
    if plan.intent == "HassClimateSetTemperature":
        return f"Set {who} to {slots.get('temperature')} degrees."
    if plan.intent == "HassSetVolume":
        return f"Set the volume to {slots.get('volume_level')}%."
    if plan.intent == "HassSetVolumeRelative":
        step = slots.get("volume_step")
        up = step == "up" or (isinstance(step, int) and step > 0)
        return f"Turned the volume {'up' if up else 'down'}."
    if plan.intent == "HassMediaSearchAndPlay":
        return f"Playing {slots.get('search_query')}."
    if plan.intent == "HassListAddItem":
        return f"Added {slots.get('item')} to {slots.get('name')}."
    if plan.intent == "HassListRemoveItem":
        return f"Removed {slots.get('item')} from {slots.get('name')}."
    if plan.intent == "HassStartTimer":
        return f"Timer started for {_duration(slots)}."
    if plan.intent == "HassIncreaseTimer":
        return f"Added {_duration(slots)} to the timer."
    if plan.intent == "HassDecreaseTimer":
        return f"Took {_duration(slots)} off the timer."
    if plan.intent == "HassVacuumStart":
        return f"Started {who}."
    if plan.intent == "HassVacuumReturnToBase":
        return f"Sent {who} back to its base."
    return "Done."


def weather(condition: str | None, temperature: object, unit: str | None) -> str:
    """'It is 14 °C and partly cloudy.' from a weather entity's state and attributes."""
    words = _WEATHER.get(condition or "", (condition or "").replace("-", " ").replace("_", " "))
    if isinstance(temperature, int | float) and not isinstance(temperature, bool):
        reading = f"{temperature:g} {unit}".strip() if unit else f"{temperature:g} degrees"
        return f"It is {reading} and {words}." if words else f"It is {reading}."
    return f"It is {words}." if words else "I could not read the weather."
