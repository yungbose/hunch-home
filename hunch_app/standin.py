"""A stand-in Home Assistant and a stand-in model: for the app's offline tests and its demo (`--demo`).

Not Home Assistant and not the model. Every reply has the shape the real Home Assistant 2026.11.2 gave the
throwaway apps in the test machine on 20 Nov 2026 (their evidence is kept outside the repository): the exposed
list, the registries' rows, `get_entries` with each alias list starting with a null for the entity's own name,
`POST /api/intent/handle`'s reply with and without the exposure check, `POST /api/conversation/process`'s reply
(and its "Unauthorized" when given a conversation the caller does not own), `state_changed` events whose context
names the app's own system user, the agent list, the assistants (`assist_pipeline/pipeline/list`, `.../get`,
`.../update`, checked as Home Assistant 2026.11.2's `assist_pipeline/pipeline.py` checks them: every field written
back, nothing unknown) and the Supervisor's `/addons/self/info` and `/addons/self/options` (the app's own sidebar
entry, its chosen microphone). The home itself is made up, written by hand.

What it does not do: real sentence matching (its own agent knows two sentences), templates, or anything Hunch
never calls. Its name matching follows Home Assistant's own rule (an entity's names are its aliases, the null
standing for the computed name), with the computed names written out by hand in `HA_NAMES`, so a test can
compare them with the names the app works out.

The token below is made up; it opens nothing but this stand-in.
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import time

from aiohttp import WSMsgType, web

from .logic.client import Answer
from .logic.table import ACTIONS, ACTIONS_1_1, KINDS

TOKEN = "made-up-token-for-tests-0123456789"
APP_USER = "e39169113cc1477aa83c8fe4b90ccd36"   # the "Supervisor" system user, as in the evidence (check 9)
PERSON = "53be7812e28e4ea09678509e0158565c"     # a signed-in person ("Test" in the evidence)
VERSION = "2026.11.2"

AREAS = [
    {"aliases": [], "area_id": "living_room", "floor_id": None, "icon": "mdi:sofa", "labels": [],
     "name": "Living Room", "picture": None},
    {"aliases": ["cooking"], "area_id": "kitchen", "floor_id": None, "icon": "mdi:stove", "labels": [],
     "name": "Kitchen", "picture": None},
    {"aliases": [], "area_id": "bedroom", "floor_id": None, "icon": "mdi:bed", "labels": [], "name": "Bedroom",
     "picture": None},
    {"aliases": [], "area_id": "hall", "floor_id": None, "icon": None, "labels": [], "name": "Hall",
     "picture": None},
    {"aliases": [], "area_id": "garage", "floor_id": None, "icon": None, "labels": [], "name": "Garage",
     "picture": None},
]


def _device(ident, name, area_id, name_by_user=None, parent=None):
    # Home Assistant works out `next_name_part` (device_registry.py at 2026.11.2): "area" when the device has
    # a room, else "parent_device" for a child device, else none.
    next_name_part = "area" if area_id else "parent_device" if parent else None
    return {"area_id": area_id, "config_entries": ["entry"], "configuration_url": None, "disabled_by": None,
            "entry_type": None, "id": ident, "identifiers": [["demo", ident]], "labels": [],
            "manufacturer": "Made up", "model": None, "name_by_user": name_by_user, "name": name,
            "next_name_part": next_name_part, "parent_device_id": parent, "via_device_id": None}


DEVICES = [
    _device("dev_bedside", "Bedside", "bedroom"),
    _device("dev_kitchen_sat", "Kitchen satellite", "kitchen"),
    _device("dev_hub", "Hub", "hall"),
    _device("dev_hub_child", "Porch", None, parent="dev_hub"),
]

# Each entity: registry fields, exposure, state, attributes, and the names Home Assistant 2026.11.2 itself
# matches on (its `async_get_entity_aliases`, the null alias replaced by the computed full name), by hand.
ENTITIES = {
    "light.reading_light": {"original_name": "Reading light", "area_id": "living_room", "state": "off",
                                "attrs": {"supported_color_modes": ["brightness"], "brightness": None}},
    "light.bedside_lamp": {"original_name": "Lamp", "device_id": "dev_bedside",
                               "aliases": [None, "night light"], "state": "on",
                               "attrs": {"supported_color_modes": ["brightness"], "brightness": 128}},
    "light.window_lamp": {"original_name": "Window lamp", "area_id": "living_room", "exposed": False, "state": "on",
                              "attrs": {"supported_color_modes": ["onoff"]}},
    "light.kitchen_lamp": {"original_name": "Lamp", "area_id": "kitchen", "state": "off",
                               "attrs": {"supported_color_modes": ["onoff"]}},
    "light.lounge_lamp": {"name": "Lamp", "original_name": "Floor lamp", "area_id": "living_room", "state": "off",
                              "attrs": {"supported_color_modes": ["onoff"]}},
    "light.porch": {"original_name": "Light", "device_id": "dev_hub_child",
                        "state": "off", "attrs": {"supported_color_modes": ["onoff"]}},
    "switch.desk_fan": {"original_name": "Desk fan", "state": "on", "attrs": {}},
    "lock.front_door": {"original_name": "Front door", "area_id": "hall", "state": "locked", "attrs": {}},
    "lock.garage": {"original_name": "Garage", "area_id": "garage", "state": "locked", "attrs": {}},
    "cover.living_blinds": {"original_name": "Blinds", "area_id": "living_room", "device_class": "blind",
                                "state": "closed", "attrs": {"device_class": "blind"}},
    "cover.garage_door": {"original_name": "Garage door", "area_id": "garage", "device_class": "garage",
                              "state": "closed", "attrs": {"device_class": "garage"}},
    "todo.shopping_list": {"original_name": "Shopping list", "state": "0", "attrs": {}},
    "assist_satellite.kitchen": {"original_name": "Assist satellite", "device_id": "dev_kitchen_sat",
                                     "exposed": False, "state": "idle", "attrs": {}},
    # The app's own agent, as the Wyoming integration makes it, renamed by the owner (check 1 of the evidence:
    # platform "wyoming", original name the program's name).
    "conversation.hunch_home_2": {"name": "Kitchen helper", "original_name": "Hunch Home", "platform": "wyoming",
                                  "exposed": False, "state": "2026-11-20T13:00:20+00:00", "attrs": {}},
    # No registry entry: Home Assistant names it from its state's friendly name.
    "light.yaml_spot": {"registry": False, "state": "off", "attrs": {"friendly_name": "Spot light"}},
}
HA_NAMES = {
    "light.reading_light": ["Reading light"],
    "light.bedside_lamp": ["Bedside Lamp", "night light"],
    "light.window_lamp": ["Window lamp"],
    "light.kitchen_lamp": ["Lamp"],
    "light.lounge_lamp": ["Lamp"],
    "light.porch": ["Hub Porch Light"],
    "switch.desk_fan": ["Desk fan"],
    "lock.front_door": ["Front door"],
    "lock.garage": ["Garage"],
    "cover.living_blinds": ["Blinds"],
    "cover.garage_door": ["Garage door"],
    "todo.shopping_list": ["Shopping list"],
    "assist_satellite.kitchen": ["Kitchen satellite Assist satellite"],
    "light.yaml_spot": ["Spot light"],
    "conversation.hunch_home_2": ["Kitchen helper"],
}
AGENTS = [
    {"id": "conversation.home_assistant", "name": "Home Assistant", "supported_languages": ["en", "fr"]},
    {"id": "conversation.my_assistant", "name": "My assistant", "supported_languages": "*"},
    {"id": "conversation.hunch_home_2", "name": "Kitchen helper", "supported_languages": ["en"]},
    {"id": "conversation.stopped", "name": "Stopped agent", "supported_languages": ["en"]},
]

# The assistants (pipelines), as `assist_pipeline/pipeline/list` gave them in the test machine (20 Nov 2026).
# A fixed-phrase speech-to-text on the first; a French one.
PIPELINE_FIELDS = ("conversation_engine", "conversation_language", "language", "name", "stt_engine",
                   "stt_language", "tts_engine", "tts_language", "tts_voice", "wake_word_entity", "wake_word_id")
PIPELINE_OPTIONAL = ("prefer_local_intents", "acknowledge_media_id")


def _pipeline(ident, name, engine, language="en", stt=None, prefer_local=False):
    return {"conversation_engine": engine, "conversation_language": language, "language": language,
            "name": name, "stt_engine": stt, "stt_language": language if stt else None,
            "tts_engine": "tts.piper" if stt else None, "tts_language": language if stt else None,
            "tts_voice": "en_GB-alba-medium" if stt else None, "wake_word_entity": None, "wake_word_id": None,
            "prefer_local_intents": prefer_local, "id": ident}


PIPELINES = [
    _pipeline("01m7t4hzmm8j3jhfsjy9hdjjxj", "Home Assistant", "conversation.home_assistant",
              stt="stt.speech_to_phrase"),
    _pipeline("01m7wmgczn3p7q35eh515wtn4a", "Phone", "conversation.my_assistant", stt="stt.faster_whisper",
              prefer_local=True),
    _pipeline("01m7wmgczn3p7q35eh515wtn4b", "Maison", "conversation.home_assistant", language="fr"),
]
PREFERRED = "01m7t4hzmm8j3jhfsjy9hdjjxj"
# Speech-to-text entities, as the Wyoming integration names them (no room, not exposed: not in the home).
STT_STATES = {"stt.speech_to_phrase": "speech-to-phrase", "stt.faster_whisper": "faster-whisper"}
HUNCH_ENTITY = "conversation.hunch_home_2"

_ULID = itertools.count(1)


def ulid():
    return f"01M7STANDIN{next(_ULID):015d}"


def _friendly(entity_id, spec):
    if "attrs" in spec and "friendly_name" in spec["attrs"]:
        return spec["attrs"]["friendly_name"]
    return HA_NAMES[entity_id][0]


class StandIn:
    """The stand-in's state and its web application."""

    def __init__(self):
        self.entities = copy.deepcopy(ENTITIES)
        self.areas = copy.deepcopy(AREAS)
        self.devices = copy.deepcopy(DEVICES)
        self.names = copy.deepcopy(HA_NAMES)
        self.agents = copy.deepcopy(AGENTS)
        self.pipelines = copy.deepcopy(PIPELINES)
        self.preferred = PREFERRED
        self.states = {}
        for entity_id, spec in self.entities.items():
            self.states[entity_id] = self._state(entity_id, spec["state"], dict(spec.get("attrs", {})))
        for entity_id, name in STT_STATES.items():
            self.states[entity_id] = {"entity_id": entity_id, "state": "unknown",
                                      "attributes": {"friendly_name": name}, "context": {"id": ulid()}}
        # The Supervisor's view of the app: its sidebar entry and the microphone chosen in its Audio setting.
        self.app_info = {"ingress_panel": False, "audio_input": "hunch_virtual_mic", "slug": "local_hunch_home",
                         "state": "started"}
        # The sound system's inputs, as the Supervisor's /audio/info lists them.
        self.audio_inputs = [{"name": "hunch_virtual_mic", "index": 2, "description": "USB Audio Device",
                              "volume": 1.0, "mute": False, "default": True, "card": None, "applications": []}]
        self.supervisor_requests = []   # (method, path, body)
        self.delays = {}            # websocket command type -> seconds to wait before answering
        # Websocket command type -> what goes wrong, once: "silent" (carried out, never answered), "drop" (carried
        # out, then the connection drops before the answer) or "drop first" (the connection drops before it is
        # carried out). As an answer lost to a timeout or a dropped connection would be.
        self.swallow = {}
        self.failing = set()        # entity ids an intent reports as failed
        # Entity ids an intent lists under "success" though they do not change: as Home Assistant 2026.11.2
        # answered for an unavailable light in the test machine (20 Nov 2026, image check 11). A test sets their
        # state to "unavailable" or "unknown" itself.
        self.unavailable = set()
        self.started = True         # False: as Home Assistant while it starts, default exposures not yet stored
        self.requests = []          # every REST request: (method, path, body)
        self.ws_messages = []       # every websocket command sent to it
        self.sockets = []
        self.subscribers = []       # (ws, id, event type)
        self.conversations = {}     # conversation id -> {"agent": ..., "owner": ..., "texts": [...]}
        self.process_delay = 0.0
        self.intent_delay = 0.0
        self.refuse_auth = False
        # True: the websocket is answered 502, as the Supervisor's proxy answered while Home Assistant restarted
        # (the test machine, 20 Nov 2026, image check 12: WSServerHandshakeError at every try).
        self.unreachable = False
        self.fail_intents = False

    # Shapes.

    def _state(self, entity_id, state, attrs, context=None):
        attributes = {k: v for k, v in attrs.items() if v is not None}
        attributes.setdefault("friendly_name", _friendly(entity_id, self.entities[entity_id]))
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        return {"entity_id": entity_id, "state": state, "attributes": attributes, "last_changed": now,
                "last_reported": now, "last_updated": now,
                "context": context or {"id": ulid(), "parent_id": None, "user_id": None}}

    def registry_row(self, entity_id, spec):
        return {"area_id": spec.get("area_id"), "categories": {}, "config_entry_id": None,
                "config_subentry_id": None, "created_at": 1795101147.3, "device_id": spec.get("device_id"),
                "disabled_by": None, "entity_category": None, "entity_id": entity_id, "hidden_by": None,
                "icon": None, "id": f"id-{entity_id}", "labels": [], "modified_at": 1795101148.7,
                "name": spec.get("name"),
                # As Home Assistant works it out (entity_registry.py): "area", else "device", else none.
                "next_name_part": "area" if spec.get("area_id") else "device" if spec.get("device_id") else None,
                "options": {"conversation": {"should_expose": spec.get("exposed", True)}},
                "original_name": spec.get("original_name"), "platform": spec.get("platform", "demo"), "translation_key": None,
                "unique_id": f"u-{entity_id}"}

    def full_row(self, entity_id, spec):
        return {**self.registry_row(entity_id, spec), "aliases": list(spec.get("aliases", [None])),
                "capabilities": None, "device_class": None,
                "original_device_class": spec.get("device_class"), "original_icon": None}

    def exposed(self, entity_id):
        return self.entities[entity_id].get("exposed", True)

    def listed(self, entity_id):
        """Whether `homeassistant/expose_entity/list` lists it: only once a setting is stored for it, which
        Home Assistant does for every entity with a state when it has started, and for each new one at once
        (`conversation/default_agent.py` at 2026.11.2). Before that, an entity exposed by default is left out
        (`"default": True` in its spec)."""
        spec = self.entities[entity_id]
        if spec.get("default") and not self.started:
            return False
        return self.exposed(entity_id)

    def finish_starting(self):
        """Home Assistant has started: every default is stored, and `homeassistant_started` is fired."""
        self.started = True
        self.send_event("homeassistant_started", {})

    def remove_hunch(self):
        """As before the owner presses Submit on "Discovered": no Hunch agent in Home Assistant yet."""
        self.entities.pop(HUNCH_ENTITY, None)
        self.states.pop(HUNCH_ENTITY, None)
        self.names.pop(HUNCH_ENTITY, None)
        self.agents = [a for a in self.agents if a["id"] != HUNCH_ENTITY]

    def pipeline(self, ident):
        return next((p for p in self.pipelines if p["id"] == ident), None)

    def pipeline_updates(self):
        return [m for m in self.ws_messages if m.get("type") == "assist_pipeline/pipeline/update"]

    # Changing the home, as a test would.

    def set_state(self, entity_id, state, attrs=None, user_id=None, parent_id=None, context_id=None):
        """Change a state and send `state_changed`; returns the context used."""
        old = self.states[entity_id]
        new_attrs = {**old["attributes"], **(attrs or {})}
        context = {"id": context_id or ulid(), "parent_id": parent_id, "user_id": user_id}
        new = self._state(entity_id, state, new_attrs, context)
        self.states[entity_id] = new
        self.send_event("state_changed", {"entity_id": entity_id, "old_state": old, "new_state": new}, context)
        return context

    def send_event(self, event_type, data, context=None):
        event = {"event_type": event_type, "data": data, "origin": "LOCAL",
                 "time_fired": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                 "context": context or {"id": ulid(), "parent_id": None, "user_id": None}}
        for ws, ident, wanted in list(self.subscribers):
            if wanted in (event_type, None) and not ws.closed:
                asyncio.get_running_loop().create_task(ws.send_json({"id": ident, "type": "event",
                                                                     "event": event}))

    def rename(self, entity_id, aliases):
        self.entities[entity_id]["aliases"] = aliases
        self.send_event("entity_registry_updated", {"action": "update", "entity_id": entity_id,
                                                    "changes": {"aliases": aliases}})

    def expose(self, entity_id, exposed):
        self.entities[entity_id]["exposed"] = exposed
        self.send_event("entity_registry_updated", {"action": "update", "entity_id": entity_id,
                                                    "changes": {"options": {}}})

    async def drop_sockets(self):
        for ws in list(self.sockets):
            await ws.close()

    def intent_requests(self):
        return [body for method, path, body in self.requests if path == "/api/intent/handle"]

    def process_requests(self):
        return [body for method, path, body in self.requests if path == "/api/conversation/process"]

    # The web application.

    def app(self):
        app = web.Application()
        app.router.add_get("/api/websocket", self.websocket)
        app.router.add_post("/api/intent/handle", self.intent_handle)
        app.router.add_post("/api/conversation/process", self.conversation_process)
        app.router.add_get("/api/states", self.get_states)
        app.router.add_get("/supervisor/addons/self/info", self.addon_info)
        app.router.add_get("/supervisor/audio/info", self.audio_info)
        app.router.add_post("/supervisor/addons/self/options", self.addon_options)
        app.router.add_post("/supervisor/discovery", self.discovery)
        return app

    # The Supervisor, as the app reaches it (`http://supervisor/...`, here under `/supervisor`).

    async def addon_info(self, request):
        if not self._authorised(request):
            return web.json_response({"result": "error", "message": "Unauthorized"}, status=401)
        self.supervisor_requests.append(("GET", "/addons/self/info", None))
        return web.json_response({"result": "ok", "data": dict(self.app_info)})

    async def audio_info(self, request):
        """The sound system's inputs, in the shape the Supervisor gave in the test machine (20 Nov 2026), with a
        USB microphone's description. An app needs `hassio_api` for this one."""
        if not self._authorised(request):
            return web.json_response({"result": "error", "message": "Unauthorized"}, status=401)
        self.supervisor_requests.append(("GET", "/audio/info", None))
        return web.json_response({"result": "ok", "data": {"host": "172.30.32.4", "audio": {
            "card": [], "input": [dict(i) for i in self.audio_inputs], "output": [], "application": []}}})

    async def addon_options(self, request):
        if not self._authorised(request):
            return web.json_response({"result": "error", "message": "Unauthorized"}, status=401)
        body = await request.json()
        self.supervisor_requests.append(("POST", "/addons/self/options", body))
        if "ingress_panel" in body:
            self.app_info["ingress_panel"] = bool(body["ingress_panel"])
        return web.json_response({"result": "ok", "data": {}})

    async def discovery(self, request):
        if not self._authorised(request):
            return web.json_response({"result": "error", "message": "Unauthorized"}, status=401)
        self.supervisor_requests.append(("POST", "/discovery", await request.json()))
        return web.json_response({"result": "ok", "data": {"uuid": "17235168c0834c05ba7454279050dd3b"}})

    def _authorised(self, request):
        return request.headers.get("Authorization") == f"Bearer {TOKEN}"

    async def get_states(self, request):
        if not self._authorised(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        self.requests.append(("GET", request.path, None))
        return web.json_response(list(self.states.values()))

    async def websocket(self, request):
        if self.unreachable:
            return web.Response(status=502, text="502 Bad Gateway")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        await ws.send_json({"type": "auth_required", "ha_version": VERSION})
        auth = await ws.receive_json()
        if self.refuse_auth or auth.get("access_token") != TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token or password"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": VERSION})
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            command = json.loads(message.data)
            self.ws_messages.append(command)
            delay = self.delays.get(command.get("type"))
            if delay:
                await asyncio.sleep(delay)
            lost = self.swallow.pop(command.get("type"), None)
            if lost == "drop first":
                await ws.close()
                break
            reply = self.answer(ws, command)
            if lost == "silent":
                continue
            if lost == "drop":
                await ws.close()
                break
            await ws.send_json(reply)
        self.subscribers = [s for s in self.subscribers if s[0] is not ws]
        return ws

    def answer(self, ws, command):
        ident, kind = command.get("id"), command.get("type")

        def ok(result):
            return {"id": ident, "type": "result", "success": True, "result": result}

        if kind == "subscribe_events":
            self.subscribers.append((ws, ident, command.get("event_type")))
            return ok(None)
        if kind == "homeassistant/expose_entity/list":
            return ok({"exposed_entities": {eid: {"conversation": True} for eid in self.entities
                                            if self.listed(eid)}})
        if kind == "assist_pipeline/pipeline/list":
            return ok({"pipelines": copy.deepcopy(self.pipelines), "preferred_pipeline": self.preferred})
        if kind == "assist_pipeline/pipeline/get":
            found = self.pipeline(command.get("pipeline_id") or self.preferred)
            if found is None:
                return {"id": ident, "type": "result", "success": False, "error": {
                    "code": "not_found", "message": f"Unable to find pipeline_id {command.get('pipeline_id')}"}}
            return ok(copy.deepcopy(found))
        if kind == "assist_pipeline/pipeline/update":
            return self.update_pipeline(ident, command)
        if kind == "config/entity_registry/list":
            return ok([self.registry_row(eid, spec) for eid, spec in self.entities.items()
                       if spec.get("registry", True)])
        if kind == "config/entity_registry/get_entries":
            return ok({eid: (self.full_row(eid, self.entities[eid])
                             if eid in self.entities and self.entities[eid].get("registry", True) else None)
                       for eid in command.get("entity_ids", [])})
        if kind == "config/device_registry/list":
            return ok(self.devices)
        if kind == "config/area_registry/list":
            return ok(self.areas)
        if kind == "get_states":
            return ok(list(self.states.values()))
        if kind == "conversation/agent/list":
            return ok({"agents": self.agents})
        if kind == "get_config":
            return ok({"unit_system": {"temperature": "°C", "length": "km"}, "version": VERSION,
                       "location_name": "Home", "language": "en"})
        if kind == "auth/current_user":
            return ok({"id": APP_USER, "name": "Supervisor", "is_owner": False, "is_admin": True,
                       "credentials": [], "mfa_modules": []})
        return {"id": ident, "type": "result", "success": False,
                "error": {"code": "unknown_command", "message": "Unknown command."}}

    def update_pipeline(self, ident, command):
        """As Home Assistant 2026.11.2: every required field must be given, nothing unknown, and the pipeline
        becomes exactly what was sent (`PipelineStorageCollection._update_data`)."""
        fields = {k: v for k, v in command.items() if k not in {"id", "type", "pipeline_id"}}
        missing = [k for k in PIPELINE_FIELDS if k not in fields]
        unknown = [k for k in fields if k not in PIPELINE_FIELDS + PIPELINE_OPTIONAL]
        if missing or unknown:
            message = (f"required key not provided @ data['{missing[0]}']" if missing
                       else f"extra keys not allowed @ data['{unknown[0]}']")
            return {"id": ident, "type": "result", "success": False,
                    "error": {"code": "invalid_format", "message": message}}
        found = self.pipeline(command.get("pipeline_id"))
        if found is None:
            return {"id": ident, "type": "result", "success": False, "error": {
                "code": "not_found", "message": f"Unable to find pipeline_id {command.get('pipeline_id')}"}}
        new = {"prefer_local_intents": False, **fields, "id": found["id"]}
        new.pop("acknowledge_media_id", None)   # checked, but not kept on the pipeline (models.py)
        found.clear()
        found.update(new)
        return {"id": ident, "type": "result", "success": True, "result": copy.deepcopy(found)}

    # Intents, as Home Assistant's built-in handlers answer them.

    def _match(self, data, assistant):
        def value(slot):
            return data.get(slot)

        name, area = value("name"), value("area")
        domains = value("domain") or []
        classes = value("device_class") or []
        found = []
        for entity_id in self.entities:
            if domains and entity_id.split(".")[0] not in domains:
                continue
            spec = self.entities[entity_id]
            if classes and spec.get("device_class") not in classes:
                continue
            if name is not None and entity_id != name and name.strip().casefold() not in {
                    n.casefold() for n in self.names[entity_id]}:
                continue
            if area is not None:
                device = next((d for d in self.devices if d["id"] == spec.get("device_id")), None)
                where = spec.get("area_id") or (device or {}).get("area_id")
                if where != area:
                    continue
            found.append(entity_id)
        if assistant is not None:
            exposed = [entity_id for entity_id in found if self.exposed(entity_id)]
            if found and not exposed:
                return None, f"No matching entities are exposed to the assistant (given name '{name}')"
            found = exposed
        if not found:
            return None, f"No device or entity named {name}"
        return found, None

    async def intent_handle(self, request):
        if not self._authorised(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        body = await request.json()
        self.requests.append(("POST", request.path, body))
        if self.intent_delay:
            await asyncio.sleep(self.intent_delay)
        # The REST endpoint wraps each plain value as a slot itself (intent/__init__.py at 2026.11.2), so a
        # value sent already wrapped is not a name, a room or a domain, and the intent fails.
        if any(isinstance(v, dict) for v in body.get("data", {}).values()):
            found, error = None, "Received invalid slot info"
        else:
            found, error = self._match(body.get("data", {}), body.get("assistant"))
        if self.fail_intents:
            found, error = None, "Service call failed"
        if error:
            return web.json_response({"speech": {"plain": {"speech": error, "extra_data": None}}, "card": {},
                                      "language": "en", "response_type": "error",
                                      "data": {"code": "failed_to_handle"}})
        intent = body.get("name")
        data = dict(body.get("data", {}))
        context = {"id": ulid(), "parent_id": None, "user_id": APP_USER}
        failed = [e for e in found if e in self.failing]
        found = [e for e in found if e not in self.failing]
        if failed and not found:
            return web.json_response({"speech": {"plain": {"speech": "Failed to turn on the device",
                                                           "extra_data": None}}, "card": {}, "language": "en",
                                      "response_type": "error", "data": {"code": "failed_to_handle"}})
        for entity_id in found:
            if entity_id in self.unavailable:
                continue
            domain = entity_id.split(".")[0]
            on = intent == "HassTurnOn"
            if intent in {"HassTurnOn", "HassTurnOff"}:
                state = {"lock": "locked" if on else "unlocked",
                         "cover": "open" if on else "closed"}.get(domain, "on" if on else "off")
                attrs = {"brightness": 255 if on else None} if domain == "light" else {}
                self.set_state(entity_id, state, attrs, APP_USER, context_id=context["id"])
            elif intent == "HassLightSet" and "brightness" in data:
                self.set_state(entity_id, "on", {"brightness": round(data["brightness"] * 255 / 100)},
                               APP_USER, context_id=context["id"])
        await asyncio.sleep(0)
        return web.json_response({"speech": {}, "card": {}, "language": "en", "response_type": "action_done",
                                  "data": {"success": [{"name": self.names[e][0], "type": "entity", "id": e}
                                                       for e in found],
                                           "failed": [{"name": self.names[e][0], "type": "entity", "id": e}
                                                      for e in failed]}})

    async def conversation_process(self, request):
        if not self._authorised(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        body = await request.json()
        self.requests.append(("POST", request.path, body))
        if self.process_delay:
            await asyncio.sleep(self.process_delay)
        agent = body.get("agent_id") or "conversation.home_assistant"
        if agent not in {a["id"] for a in self.agents}:
            return web.json_response({"message": "Agent not found"}, status=400)
        given = body.get("conversation_id")
        if given is not None and (self.conversations.get(given) or {}).get("owner") != "app":
            # The evidence (check 6): a conversation owned by someone else is refused with "Unauthorized".
            return web.json_response(self._reply("Unauthorized", "error", ulid(), code="failed_to_handle"))
        conversation_id = given or ulid()
        talk = self.conversations.setdefault(conversation_id, {"agent": agent, "owner": "app", "texts": []})
        talk["texts"].append(body.get("text"))
        text = (body.get("text") or "").strip().lower()
        if agent == "conversation.home_assistant":
            if text == "turn off all the lights":
                return web.json_response(self._reply("Turned off all of the lights", "action_done",
                                                     conversation_id))
            return web.json_response(self._reply("Sorry, I couldn't understand that", "error", conversation_id,
                                                 code="no_intent_match"))
        if agent == "conversation.stopped":
            # The evidence (check 10): a stopped Wyoming agent answers an error, with no conversation id.
            return web.json_response(self._reply("Error communicating with service: [Errno -5] Name has no "
                                                 "usable address", "error", None, code="unknown"))
        # A language model agent that remembers its conversation.
        said = f"Turn {len(talk['texts'])} of our talk: {body.get('text')}"
        return web.json_response(self._reply(said, "query_answer", conversation_id))

    @staticmethod
    def _reply(speech, kind, conversation_id, code=None):
        data = {"code": code} if kind == "error" else {"success": [], "failed": []}
        return {"response": {"speech": {"plain": {"speech": speech, "extra_data": None}}, "card": {},
                             "language": "en", "response_type": kind, "data": data},
                "conversation_id": conversation_id, "continue_conversation": False}




# The stand-in model: what it answers, by sentence (any case). Anything else: "not for me".
ANSWERS = {
    "turn on the reading light": Answer("turn on", "Lights", similarity=0.95, version="1.1"),
    "turn off the reading light": Answer("turn off", "Lights", similarity=0.94, version="1.1"),
    "reading light on please": Answer("turn on", "Lights", similarity=0.91, version="1.1"),
    "lamp on": Answer("turn on", "Lights", similarity=0.9),
    "brighter in the bedroom": Answer("raise", "Lights", similarity=0.9),
    "unlock the front door": Answer("unlock", "Security", similarity=0.97),
    "lock the front door": Answer("lock", "Security", similarity=0.97),
    "unlock the garage": Answer("unlock", "Security", similarity=0.97),
    "open the garage door": Answer("open", "Security", similarity=0.96),
    "close the blinds": Answer("close", "Climate", similarity=0.96),
    "open the blinds": Answer("open", "Climate", similarity=0.96),
    "turn off the window lamp": Answer("turn off", "Lights", similarity=0.95),
    "switch off the desk fan": Answer("turn off", "Other device", similarity=0.93),
    "turn off all the lights": Answer("not for me", "none", declined=True),
    "turn on the lights in the living room": Answer("turn on", "Lights", similarity=0.93),
    # A few more for the demo.
    "it's a bit dark in here": Answer("turn on", "Lights", similarity=0.88, version="1.1"),
    "kill the lights in here": Answer("turn off", "Lights", similarity=0.9, version="1.1"),
    "put the bedside lamp on": Answer("turn on", "Lights", similarity=0.93, version="1.1"),
    "make the bedroom brighter": Answer("raise", "Lights", similarity=0.9, version="1.1"),
    "shut the blinds": Answer("close", "Climate", similarity=0.92, version="1.1"),
    "add milk to the shopping list": Answer("add", "Lists", similarity=0.94, version="1.1"),
}


class StandInModel:
    """Answers the sentences in `ANSWERS`, as the real model's `answer` does; "not for me" to the rest.

    It can be taught, as the real model's runtime is (`model.Loaded.teach` and `forget`), in a simple form: a
    taught sentence (case, spaces and end punctuation ignored) is answered as taught, ahead of `ANSWERS`, and
    nothing else changes. The real runtime also answers near wordings from a taught example; this does not."""

    ready, version = True, "1.1"
    actions = ACTIONS + ACTIONS_1_1
    kinds = KINDS
    can_decline = True

    def __init__(self, delay: float = 0.0, answers: dict | None = None):
        self.delay, self.asked = delay, []
        self.answers = ANSWERS if answers is None else answers
        self.taught: dict[int, tuple[str, str, str]] = {}   # its own id -> (sentence's key, action, kind)
        self.teach_calls = 0
        self._ids = itertools.count(1)

    @staticmethod
    def key(text):
        return " ".join(text.lower().split()).rstrip(".!?")

    async def answer(self, text):
        self.asked.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        key = self.key(text)
        for taught_key, action, kind in reversed(list(self.taught.values())):
            if taught_key == key:
                return Answer(action, kind, similarity=1.0, version=self.version)
        return self.answers.get(" ".join(text.lower().split()), Answer("not for me", "none"))

    async def teach(self, examples):
        self.teach_calls += 1
        ids = []
        for sentence, action, kind in examples:
            if (action, kind) != ("not for me", "none") and (action not in self.actions or kind not in self.kinds):
                ids.append(None)
                continue
            ident = next(self._ids)
            self.taught[ident] = (self.key(sentence), action, kind)
            ids.append(ident)
        return ids

    async def forget(self, ids):
        return sum(self.taught.pop(int(i), None) is not None for i in ids)


async def serve(standin: StandIn, host: str = "127.0.0.1", port: int = 0) -> tuple[web.AppRunner, int]:
    """Run the stand-in on this machine; returns its runner and port (`await runner.cleanup()` stops it)."""
    runner = web.AppRunner(standin.app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]
