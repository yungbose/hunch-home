"""Carrying a plan out: the only place the app switches a device.

Every device is switched through Home Assistant's `POST /api/intent/handle`, one request per intent call of
the plan, each built by `intent_request` and sent by `send_intent`, which nothing else in the app calls. Each
request carries `"assistant": "conversation"`: with it, Home Assistant matches only the devices exposed to Assist
and refuses the rest ("No matching entities are exposed to the assistant"); without it, a hidden device is
switched (seen in the test machine, 20 Nov 2026, check 4). A test inspects every intent request the stand-in
Home Assistant receives and fails if one lacks it.

The REST endpoint wraps each value in `data` as a slot itself (`{"value": ...}`), so slots are sent plain. The
language goes with each request, and the device and satellite the sentence came from when known, so Home
Assistant can prefer that room as its own agent would. A room found for the sentence is sent as
`preferred_area_id` unless the plan addresses each device by its entity id (as the plug-in did).

The first call that Home Assistant answers with an error or a failed device stops the plan
(`logic.decide.response_problem`). What happens then depends on whether anything has acted:

- **Nothing acted**: the result is not `ok`, and the turn passes the sentence on.
- **Something acted** (an earlier call succeeded, or one reply names devices that changed and devices that did
  not): the result is `ok` and `partial`, with only the devices that changed, and `missed` names the ones that
  did not. The sentence is never passed on: another agent could act a second time on what already changed (a
  room-wide "brighter" raising the same lights twice).

A call that gets no answer in time is different again: Home Assistant may still carry it out, so with nothing
acted the result is marked uncertain and the turn does not pass the sentence on either.

**A device listed as switched that did not change** (`checked`). Home Assistant's intent reply lists an
unavailable light under "success" though nothing happened to it (the test machine, 20 Nov 2026, image check 11:
"Turned on Reading light and Floor lamp" with the Floor lamp unavailable). So once the reply is in, each device it
names is looked up in the states the app already holds, which follow Home Assistant's change events: one whose
state is then "unavailable", or "unknown" for a kind of device where that is not its ordinary resting state, is
taken out of what was done and named as not available. With none left, nothing acted. No wait is added: the
check uses what the app knows at the moment the reply comes. What it cannot know then: a device that came back
during the call and whose change event has not arrived yet is counted as not available (it did change, and the
record says it did not), and a device that goes unavailable after the reply is counted as done.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from .ha import HomeAssistant, Unavailable
from .logic import speech
from .logic.decide import Plan, response_problem

LOG = logging.getLogger("hunch_app.act")

INTENT_PATH = "/intent/handle"
ASSISTANT = "conversation"   # Home Assistant's exposure check for Assist: never send an intent without it
LIMIT = 5.0                  # seconds for the whole plan
# Kinds of device whose state is "unknown" in the ordinary way (a scene or a button never used, a list's count
# not yet read): for them only "unavailable" means the device is not there.
UNKNOWN_IS_USUAL = frozenset({"scene", "button", "input_button", "event", "todo", "weather", "notify", "image",
                              "conversation", "stt", "tts"})
# Plans that ask rather than switch: nothing is checked.
ASKING = frozenset({"HassGetWeather", "HassGetState"})


@dataclass
class Result:
    """What Home Assistant made of a plan."""

    ok: bool
    speech: str = ""                                   # Home Assistant's own words, or the app's
    names: list[str] = field(default_factory=list)     # the devices acted on, as Home Assistant names them
    entity_ids: list[str] = field(default_factory=list)
    problem: str = ""                                  # why not, in Home Assistant's words or the app's
    # True when Home Assistant gave no answer in time: the device may still change, so the sentence must not
    # be handed to another agent as well (one sentence, one hand-off).
    uncertain: bool = False
    # Some devices acted and some did not: `names` and `entity_ids` are the ones that did, `missed` the names
    # of the ones that did not (empty when Home Assistant did not say), `late` True when the rest got no answer
    # in time and may still change.
    partial: bool = False
    missed: list[str] = field(default_factory=list)
    late: bool = False
    # The entity ids of `missed`, where Home Assistant gave them.
    missed_ids: list[str] = field(default_factory=list)
    # Devices Home Assistant listed as switched that are not available (`checked`): their names and entity ids.
    # They are in neither `names` nor `missed`.
    unavailable: list[str] = field(default_factory=list)
    unavailable_ids: list[str] = field(default_factory=list)


def plain_calls(plan: Plan) -> list[tuple[str, dict]]:
    """Each intent call of the plan with its slots as plain values (the REST endpoint wraps them itself)."""
    return [(intent, {name: slot["value"] for name, slot in slots.items()}) for intent, slots in plan.ha_calls()]


def intent_request(intent: str, slots: dict, language: str, device_id: str | None = None,
                   satellite_id: str | None = None) -> dict:
    """The body of one intent request. Always with the exposure check."""
    body = {"name": intent, "data": dict(slots), "language": language, "assistant": ASSISTANT}
    if device_id:
        body["device_id"] = device_id
    if satellite_id:
        body["satellite_id"] = satellite_id
    return body


async def send_intent(ha: HomeAssistant, body: dict, timeout: float) -> tuple[int, object]:
    """The one function that sends an intent request to Home Assistant."""
    if body.get("assistant") != ASSISTANT:
        raise ValueError("an intent request must carry the exposure check")
    return await ha.rest("POST", INTENT_PATH, body, timeout=timeout)


def _speech_of(reply: dict) -> str:
    plain = ((reply.get("speech") or {}).get("plain") or {}) if isinstance(reply, dict) else {}
    text = plain.get("speech")
    return text if isinstance(text, str) else ""


async def carry_out(ha: HomeAssistant, plan: Plan, language: str = "en", device_id: str | None = None,
                    satellite_id: str | None = None, area_id: str | None = None,
                    states: dict | None = None, limit: float = LIMIT) -> Result:
    """Send every call of the plan, in order, within `limit` seconds in all; stop at the first failure."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    names: list[str] = []
    entity_ids: list[str] = []
    said = ""

    def stopped(problem: str, uncertain: bool = False, missed: list[str] | None = None,
                missed_ids: list[str] | None = None) -> Result:
        if not names and not entity_ids:
            return Result(False, problem=problem, uncertain=uncertain)
        # Something has acted: the plan counts as carried out for those devices, and is never handed on.
        return Result(True, speech.done(plan, names), list(names), list(entity_ids), problem=problem,
                      partial=True, missed=list(missed or []), late=uncertain, missed_ids=list(missed_ids or []))

    for intent, slots in plain_calls(plan):
        if area_id is not None and not plan.by_entity:
            slots["preferred_area_id"] = area_id
        body = intent_request(intent, slots, language, device_id, satellite_id)
        remaining = deadline - loop.time()
        if remaining <= 0:
            return stopped("Home Assistant did not answer in time. A device may still change.", uncertain=True)
        try:
            status, reply = await send_intent(ha, body, remaining)
        except TimeoutError:
            LOG.info("an intent had no answer in time")
            return stopped("Home Assistant did not answer in time. A device may still change.",
                           uncertain=True)
        except Unavailable as error:
            LOG.info("an intent was not carried out (%s)", type(error).__name__)
            return stopped("Hunch could not reach Home Assistant.", uncertain=bool(names))
        if status != 200 or not isinstance(reply, dict):
            return stopped("Home Assistant gave an answer Hunch could not read.")
        data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
        failed = [t for t in data.get("failed") or () if isinstance(t, dict)]
        targets = [t for t in data.get("success") or () if isinstance(t, dict)]
        entities = [t for t in targets if t.get("type") == "entity"] or targets
        names += [str(t.get("name")) for t in entities if t.get("name")]
        entity_ids += [str(t.get("id")) for t in targets if t.get("type") == "entity" and t.get("id")]
        if response_problem(str(reply.get("response_type")), bool(failed)) is not None:
            words = _speech_of(reply)
            return stopped(f"Home Assistant could not do it: {words}" if words else
                           "Home Assistant could not do it.",
                           missed=[str(t.get("name")) for t in failed if t.get("name")],
                           missed_ids=[str(t.get("id")) for t in failed if t.get("type") == "entity" and t.get("id")])
        said = said or _speech_of(reply)
    if plan.intent == "HassGetWeather":
        state = (states or {}).get(entity_ids[0] if entity_ids else (plan.entity_ids or ("",))[0]) or {}
        attributes = state.get("attributes") or {}
        said = speech.weather(state.get("state"), attributes.get("temperature"),
                              attributes.get("temperature_unit"))
    elif plan.by_entity or not said:
        said = speech.done(plan, names)
    return Result(True, said, names, entity_ids)


def not_there(entity_id: str, states: Mapping) -> bool:
    """Whether the app's states say this device is not available: "unavailable", or "unknown" where that is not
    the kind's usual resting state. A device the app holds no state for is taken to be there."""
    state = (states.get(entity_id) or {}).get("state")
    if state == "unavailable":
        return True
    return state == "unknown" and entity_id.split(".", 1)[0] not in UNKNOWN_IS_USUAL


def not_available(names: list[str]) -> str:
    """What the owner reads, such as: Floor lamp is not available just now."""
    return f"{speech.join(names)} {'is' if len(names) == 1 else 'are'} not available just now."


def checked(result: Result, plan: Plan, states: Mapping, name_of: Callable[[str], str]) -> Result:
    """The result with every device that is not available taken out of what was done (the module's docstring).
    `states` are the app's own, as they are when the reply comes; `name_of` names a device the reply did not."""
    if not result.ok or plan.intent in ASKING:
        return result
    away = [entity_id for entity_id in result.entity_ids if not_there(entity_id, states)]
    missed_away = [entity_id for entity_id in result.missed_ids if not_there(entity_id, states)]
    if not away and not missed_away:
        return result
    done_names = (dict(zip(result.entity_ids, result.names, strict=True))
                  if len(result.names) == len(result.entity_ids) else {})
    missed_names = (dict(zip(result.missed_ids, result.missed, strict=True))
                    if len(result.missed) == len(result.missed_ids) else {})
    kept_ids = [entity_id for entity_id in result.entity_ids if entity_id not in away]
    kept = [done_names.get(entity_id) or name_of(entity_id) for entity_id in kept_ids]
    gone = ([done_names.get(entity_id) or name_of(entity_id) for entity_id in away]
            + [missed_names.get(entity_id) or name_of(entity_id) for entity_id in missed_away])
    missed = [name for name in result.missed if name not in gone]
    missed_ids = [entity_id for entity_id in result.missed_ids if entity_id not in missed_away]
    if not kept_ids:
        LOG.info("every device Home Assistant reported was not available; nothing acted")
        said = " ".join(part for part in (f"{speech.join(missed)} did not respond." if missed else "",
                                          not_available(gone)) if part)
        return Result(False, problem=said, uncertain=result.late, missed=missed, missed_ids=missed_ids,
                      unavailable=gone, unavailable_ids=away + missed_away)
    return replace(result, speech=speech.done(plan, kept), names=kept, entity_ids=kept_ids, partial=True,
                   missed=missed, missed_ids=missed_ids, unavailable=gone, unavailable_ids=away + missed_away)
