"""The app put together: Home Assistant, the home, the model, the turns, the store, the counts, the assistants'
set-up and the live stream, behind the calls the page's server makes (it is the page's `web.Backend`, with the
shapes that protocol's docstring gives) and the one call the Wyoming agent makes (`handle`).

Built with its parts given (`App(ha, store, model=...)`), so a test can give a stand-in model and a stand-in
Home Assistant; `__main__.py` builds the real ones.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from . import NAME, __version__, hold
from . import data as your_data
from .api import ASSIST, EVENT_HOME, PRIVACY_URL, SettingsError
from .assistant import Assistants
from .counts import SHARE_ENDPOINT, Counts, session_transport
from .ha import HomeAssistant, Unavailable
from .home import HomeView
from .listen import Listening, ProcessEar
from .logic import undo
from .model import NoModel
from .passon import DEFAULT_AGENT, PassOn
from .store import SETTINGS_FILE, Store, check_settings
from .teach import Teaching, offer
from .turn import Turn, Turns, iso
from .undone import Undone

LOG = logging.getLogger("hunch_app")

PLUGIN = "hunch_home"                   # the earlier plug-in's integration
EVENT_STATE = "state"                   # the live stream's "read the state again" (`web.Backend.events`)
QUEUE = 200                             # events a slow listener may fall behind by
STATE_EVERY = 5.0                       # seconds: the page's state reads the home again at most this often
STATE_WAIT = 3.0                        # seconds the page's state waits for that reading
EMPTY_HOME = {"devices": 0, "kinds": [], "rooms": [], "fix": []}


class Subscription:
    """One listener on the live stream (`api.Subscription`)."""

    _END = object()

    def __init__(self, owner: App) -> None:
        self._owner = owner
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE + 1)  # one more, for the end marker
        self.closed = False

    def put(self, event: dict) -> None:
        if self.closed:
            return
        if self._queue.qsize() >= QUEUE:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()  # the oldest goes; the app is never held up
        self._queue.put_nowait(event)

    async def get(self) -> dict:
        item = await self._queue.get()
        if item is self._END:
            raise StopAsyncIteration
        return item

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._owner._subscribers.discard(self)
            if self._queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._queue.get_nowait()  # the oldest goes, so the end marker always fits
            self._queue.put_nowait(self._END)

    def __aiter__(self) -> AsyncIterator[dict]:
        return self

    async def aclose(self) -> None:
        self.close()

    async def __anext__(self) -> dict:
        return await self.get()


class App:
    def __init__(self, ha: HomeAssistant, store: Store, model=None, transport=None,
                 share_endpoint: str = SHARE_ENDPOINT, undo_clock=None, ear=None) -> None:
        """`undo_clock`: the monotonic clock the "undone" watch measures its window by (a test's own). `ear`:
        what listening hears with (`listen.ProcessEar`, the speech model and its worker process, by default;
        a stand-in in the tests and the demo)."""
        self.ha = ha
        self.store = store
        self.model = model or NoModel()
        self._model_problem: str | None = None   # a plain reason when the model could not be loaded
        self.teaching = Teaching(store.folder)   # the owner's taught examples (`teach.py`)
        self.teaching.load()
        self._attach_first = not self.teaching.use(self.model)
        self.agents: list[dict] = []
        self._subscribers: set[Subscription] = set()
        self._tasks: list[asyncio.Task] = []
        self._saving: asyncio.Task | None = None
        self.home = HomeView(ha, on_change=self._home_changed)
        self.undone = Undone(self._settle, **({"clock": undo_clock} if undo_clock else {}))
        self.home.state_listeners.append(self.undone.changed)
        self.passon = PassOn(ha, own_agents=self.own_agents)
        self.turns = Turns(ha, self.home, store, self.passon, self.undone, lambda: self.model, self.agent_name,
                           self.emit)
        self.assistants = Assistants(ha, store, self.agent_name, self.own_agents, self.hunch_agent,
                                     lambda: self.home.states)
        self.assistants.changed = self.state_changed   # a set-up or undo whose answer was lost is settled
        self._agents_at = 0.0
        self._transport = transport
        self.share_endpoint = share_endpoint
        self.counts: Counts | None = None
        self.listening = Listening(self, ear if ear is not None else ProcessEar(store.folder / "speech"))
        ha.on_connect(self._connected)
        ha.on_drop(self.state_changed)   # the page's "connected" changes: it reads the state again

    # Starting and stopping.

    async def start(self) -> None:
        """Connect to Home Assistant and start the timers. The store is loaded by the caller first."""
        if self._attach_first:   # the model given is taught the kept examples before it answers anything
            self._attach_first = False
            given, self.model = self.model, NoModel()
            await self.teaching.attach(given)
            self.model = given
        await self.ha.start()
        transport = self._transport or session_transport(self.ha.session)
        self.counts = Counts(self.store, transport, self.share_endpoint)
        self.counts.changed = self.state_changed   # a deletion done or failed, counts sent: the page reads again
        self.counts.start()
        self.home.start_timer()
        self._tasks.append(asyncio.create_task(self._undo_clock(), name="hunch undone"))
        await self.listening.start()

    async def stop(self) -> None:
        await self.listening.stop()   # first: the microphone is closed before anything else
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self.counts is not None:
            await self.counts.stop()
        await self.home.stop()
        self.undone.stop()
        for subscription in list(self._subscribers):
            subscription.close()
        with contextlib.suppress(OSError):
            await self.store.save_record()
        close = getattr(self.model, "close", None)
        if callable(close):
            close()
        await self.ha.close()

    def set_model(self, model) -> None:
        """The model, once loaded (sentences before then are passed on). When there are taught examples it is put
        to use once they are taught to it, so it never answers without them."""
        self._attach_first = False
        self._model_problem = None
        if self.teaching.use(model):
            self.model = model
            self.state_changed()
            return
        self._tasks.append(asyncio.get_running_loop().create_task(self._use(model), name="hunch taught"))

    async def _use(self, model) -> None:
        await self.teaching.attach(model)
        self.model = model
        self.state_changed()   # ready, and teaching's notice may have changed

    @property
    def model_problem(self) -> str | None:
        return self._model_problem

    @model_problem.setter
    def model_problem(self, reason: str | None) -> None:
        if reason != self._model_problem:
            self._model_problem = reason
            self.state_changed()

    def state_changed(self) -> None:
        """Tell every page that something in `state()` changed that it did not ask for (the model ready, failed
        or replaced, Home Assistant reached or lost, an assistant settled): it reads the state again."""
        self.emit({"event": EVENT_STATE})

    async def _connected(self) -> None:
        try:
            user = await self.ha.call("auth/current_user")
            self.undone.app_user = user.get("id") if isinstance(user, dict) else None
        except Exception as error:  # noqa: BLE001 - without it, own changes are recognised by timing alone
            LOG.warning("could not learn the app's own Home Assistant user (%s)", type(error).__name__)
        await self._read_agents()
        self.state_changed()

    async def _read_agents(self) -> None:
        try:
            reply = await self.ha.call("conversation/agent/list")
        except (Unavailable, TimeoutError):
            return
        except Exception as error:  # noqa: BLE001
            LOG.warning("could not list the agents (%s)", type(error).__name__)
            return
        agents = (reply or {}).get("agents") if isinstance(reply, dict) else None
        self.agents = [{"id": a["id"], "name": a.get("name") or a["id"]} for a in agents or ()
                       if isinstance(a, dict) and isinstance(a.get("id"), str)]

    def _own_rows(self) -> list[str]:
        """The app's own agent ids from the entity registry: a conversation entity of the Wyoming integration
        whose original name is the app's Wyoming program name (the test machine, 20 Nov 2026: the discovery's id
        cannot be used, as the entry's unique id shows as null). Never an agent matched by its name alone: the
        earlier plug-in's agent is called "Hunch Home" too, and so can any agent the owner renames."""
        return sorted(entity_id for entity_id, row in self.home.snapshot.entity_rows.items()
                      if entity_id.startswith("conversation.") and row.get("platform") == "wyoming"
                      and row.get("original_name") == NAME)

    def own_agents(self) -> set[str]:
        """Hunch's own agent ids: never passed to, never listed, the one the set-up chooses and undo puts back."""
        return set(self._own_rows())

    def earlier_plugin(self) -> bool:
        """Whether the earlier Hunch Home plug-in is still installed: an entity of its integration ("hunch_home")
        is in the entity registry."""
        return any(row.get("platform") == PLUGIN for row in self.home.snapshot.entity_rows.values())

    def agent_name(self, agent_id: str | None) -> str:
        """An agent's name as Home Assistant lists it; never its id."""
        for agent in self.agents:
            if agent["id"] == agent_id:
                return agent["name"]
        if agent_id == DEFAULT_AGENT:
            return "Home Assistant"
        if agent_id in self.own_agents():
            return NAME
        return "the agent you chose"

    async def hunch_agent(self) -> str | None:
        """Hunch's own agent in Home Assistant, read afresh; None until the owner has pressed Submit on
        "Discovered". Found in the entity registry (a conversation entity of the Wyoming integration with the
        program's name) and, when Home Assistant lists its agents, only if it is listed."""
        await self.home.fresh(older_than=0.0, limit=STATE_WAIT)
        if self.ha.connected:
            await self._read_agents()
        found = self._own_rows()
        listed = {a["id"] for a in self.agents}
        if listed:
            found = [entity_id for entity_id in found if entity_id in listed]
        return found[0] if found else None

    async def _undo_clock(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            self.undone.due()

    def _settle(self, verdict: undo.Verdict) -> None:
        if self.store.set_undone(verdict.key, verdict.undone, verdict.by, verdict.entity_id):
            self._save_soon()

    def _save_soon(self) -> None:
        if self._saving is None or self._saving.done():
            self._saving = asyncio.get_running_loop().create_task(self._save())

    async def _save(self) -> None:
        try:
            await self.store.save_record()
        except OSError as error:
            LOG.warning("could not save the record (%s)", type(error).__name__)

    # The live stream.

    def emit(self, event: dict) -> None:
        for subscription in list(self._subscribers):
            subscription.put(event)

    def _home_changed(self, summary: dict) -> None:
        self.emit({"event": EVENT_HOME, "home": summary})

    def subscribe(self) -> Subscription:
        subscription = Subscription(self)
        self._subscribers.add(subscription)
        return subscription

    def events(self) -> Subscription:
        """The live stream for one page (`web.Backend.events`): listening from the moment this is called, so
        nothing sent after it is missed; `aclose()` (or `close()`) stops it."""
        return self.subscribe()

    # The Wyoming agent's one call.

    async def handle(self, text: str, language: str | None = None, conversation_id: str | None = None,
                     device_id: str | None = None, satellite_id: str | None = None) -> Turn:
        return await self.turns.run(text, ASSIST, language or "en", conversation_id, device_id, satellite_id)

    # The page's calls (`web.Backend`).

    async def _freshen(self) -> None:
        """For the page's state: the home, the agents and the app's own entry read again, at most every few
        seconds, so a device the owner has just exposed is seen at once."""
        if not self.ha.connected:
            return
        await self.home.fresh(older_than=STATE_EVERY, limit=STATE_WAIT)
        if time.monotonic() - self._agents_at >= STATE_EVERY:
            self._agents_at = time.monotonic()
            await self._read_agents()
        await self.assistants.read_self()

    def teachable(self) -> dict:
        """What can be taught with the model in use (`teach.offer`), and a plain note when the kept examples
        could not all be read or used (empty otherwise)."""
        return {**offer(self.model), "notice": self.teaching.notice}

    async def state(self) -> dict:
        await self._freshen()
        await self.listening.read_microphone()
        settings = dict(self.store.settings)
        own = self.own_agents()
        hunch = next((a for a in self.agents if a["id"] in own), None)
        return {
            "version": __version__,
            "model_version": getattr(self.model, "version", None),
            "model_ready": bool(getattr(self.model, "ready", False)),
            "model_problem": self.model_problem,
            "connected": self.ha.connected,
            "home": self.home.summary() if self.home.loaded else dict(EMPTY_HOME),
            "settings": settings,
            "assistants": await self.assistants.assistants() if self.ha.connected else [],
            "agents": [dict(a) for a in self.agents if a["id"] not in own],
            "hunch_agent": dict(hunch) if hunch else None,
            "teach": self.teachable(),
            "privacy_url": PRIVACY_URL,
            "notices": list(self.store.notices) + ([self.teaching.held] if self.teaching.held else []),
            "pass_to_chosen": self.store.pass_to_chosen,   # the owner chose "Pass on to" themselves
            "sidebar": self.assistants.sidebar,
            "listening": self.listening.status(),
            "earlier_plugin": self.earlier_plugin(),
            # False while the settings file is damaged and left where it is: a change is used, not saved
            "settings_saved": SETTINGS_FILE not in self.store.held,
        }

    async def record(self, limit: int = 50) -> list[dict]:
        if self.store.settings.get("keep_record", True) is not True:
            return []
        limit = max(1, min(1000, int(limit)))
        out = []
        for entry, extra in self.store.turns(limit):
            out.append({"id": entry.id, "time": iso(entry.at), "source": extra.get("source") or ASSIST,
                        "sentence": entry.sentence, "outcome": extra.get("outcome"), "label": extra.get("label"),
                        "action": entry.action, "kind": entry.kind, "score": entry.score,
                        "entities": list(extra.get("entities") or []), "ms": extra.get("ms"),
                        "reply": extra.get("reply") or "", "undone": entry.undone,
                        "undone_by": entry.undone_by,
                        "note": extra.get("note") or "",
                        "short": hold.short(str(extra.get("outcome")), str(extra.get("reply") or ""))})
        return out

    async def try_sentence(self, text: str, room: str | None = None) -> dict:
        return await self.turns.try_only(text, room)

    async def update_settings(self, changes: dict) -> dict:
        """Save some settings and use them from the next sentence on, with no restart. Returns all settings.
        Unknown names and wrong values raise `SettingsError` (plain words) and nothing is saved."""
        clean, errors = check_settings(changes)
        if errors:
            raise SettingsError(errors)
        agent = clean.get("pass_to")
        if agent is not None:
            if not self.home.loaded and self.ha.connected:
                await self.home.fresh(older_than=0.0, limit=STATE_WAIT)   # the registry says which agent is Hunch
            if agent in self.own_agents():
                raise SettingsError({"pass_to": "that is Hunch itself"})
            if self.agents and agent not in {a["id"] for a in self.agents}:
                raise SettingsError({"pass_to": "no such agent"})
        room = clean.get("mic_room")
        if room is not None and self.home.loaded and self.home.area_id_for(room) is None:
            raise SettingsError({"mic_room": "not a room"})
        if agent is not None and not self.store.pass_to_chosen:
            self.store.pass_to_chosen = True   # the set-up never changes it again
            await self.store.save_assistants()
        listen = clean.pop("listening", None)
        settings = await self.store.save_settings(clean)
        if "share_counts" in clean and self.counts is not None:
            await self.counts.settings_changed()
        if listen is not None:
            # Saved by listening itself: on once the speech model is here (fetching it first if need be).
            await self.listening.switch(listen)
            settings = dict(self.store.settings)
        return settings

    save_settings = update_settings

    async def data(self) -> dict:
        """The page's "Your data" section (`data.view`)."""
        return your_data.view(self.store, self.counts, now=self.store.clock(), endpoint=self.share_endpoint)

    async def download(self, kind: str) -> dict:
        """The whole record ("full") or the version made for a bug report ("report") (`data.download`)."""
        return your_data.download(self.store, kind, version=__version__,
                                  model_version=getattr(self.model, "version", None),
                                  model_ready=bool(getattr(self.model, "ready", False)),
                                  listening=self.listening.status(), now=self.store.clock())

    async def clear_record(self) -> None:
        self.undone.stop()
        self.store.clear()
        await self.store.save_record()

    async def setup_assistant(self, pipeline_id: str, sidebar: bool = False) -> dict:
        return await self.assistants.setup(pipeline_id, sidebar)

    async def undo_assistant(self, pipeline_id: str) -> dict:
        return await self.assistants.undo(pipeline_id)

    # Teaching (`teach.py`): only on the owner's tap on the page, never by the app itself.

    async def teach(self, sentence: str, action: str, kind: str) -> dict:
        return await self.teaching.teach(sentence, action, kind)

    async def taught(self) -> list[dict]:
        return self.teaching.listed()

    async def forget(self, taught_id: str) -> None:
        await self.teaching.forget(taught_id)
