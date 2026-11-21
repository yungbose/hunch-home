"""The one-button set-up and its undo: Hunch becomes an assistant's agent, and can be taken off again exactly.

An assistant is what Home Assistant calls a pipeline (Settings, Voice assistants). Read and written over the
websocket, as Home Assistant 2026.11.2 has them (`assist_pipeline/pipeline.py`):

- `assist_pipeline/pipeline/list` gives `{"pipelines": [...], "preferred_pipeline": id}`;
- `assist_pipeline/pipeline/get` `{pipeline_id}` gives one, or the error "not_found";
- `assist_pipeline/pipeline/update` `{pipeline_id, ...}` replaces the whole pipeline with what is sent: every
  field must be given and nothing unknown is taken. So the set-up reads the whole pipeline, changes two fields
  and writes all of it back; nothing else about the assistant changes.

**Set-up**, for one assistant:

1. Refused, in plain words, when the assistant does not exist, already uses Hunch, is not in English (Hunch
   understands English only), or when Hunch's own agent is not in Home Assistant yet (the owner has not pressed
   Submit on "Discovered").
2. What it has is remembered in the app's store (`assistants.json`): its agent (`conversation_engine`) and
   whether "prefer handling commands locally" (`prefer_local_intents`) was on. Remembered before anything is
   written, and forgotten again only if Home Assistant plainly refuses the change (see "An answer that is lost"
   below).
3. Written back whole, with `conversation_engine` set to Hunch's agent and `prefer_local_intents` set to true.
4. Unless the owner has chosen the agent to pass on to themselves, the app's "Pass on to" becomes the agent the
   assistant had before.
5. With `sidebar`, the app switches on its own sidebar entry: `POST /addons/self/options {"ingress_panel":
   true}` through the Supervisor (the app needs `hassio_api`). In Docker there is no Supervisor, so this step is
   skipped silently and the page's `sidebar` is null.

**Undo**: the remembered agent and "prefer handling commands locally" are written back (the rest of the
assistant as it is now), and the memory is forgotten. If the assistant no longer uses Hunch (someone changed it
in Home Assistant since), nothing is changed and the owner is told so.

**An answer that is lost.** Home Assistant may carry a change out and its answer never arrive: the answer is
late (`write_limit`), or the connection drops. Then the app cannot tell whether the change was made, so it never
forgets the way back on that alone. The remembered settings are kept, marked `pending` ("setup" or "undo", kept
on disk, so a restart does not lose it either), and the assistant is read again at once. If it cannot be read,
it is read again each time the app connects to Home Assistant and each time the page reads its state, and
settled from what is really there:

- a set-up: the assistant uses Hunch, so the set-up worked (the rest of it is done then: "Pass on to", the
  sidebar); or it does not, so the memory is forgotten;
- an undo: the assistant no longer uses Hunch, so it is back and the memory is forgotten; or it still does, so
  the undo did not happen and the memory stays, for the owner to press "Stop using Hunch" again.

Meanwhile the owner reads `SETUP_NOT_KNOWN` or `UNDO_NOT_KNOWN`, and the page shows each assistant as Home
Assistant reports it; once it is settled, the page is told to read the state again.

The speech-to-text each assistant uses is reported with whether it is the fixed-phrase kind (Speech-to-Phrase,
which only knows set phrases, so a sentence in the owner's own words never reaches Hunch). It is recognised by
its name or entity id ("speech-to-phrase").
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable

from .ha import CallFailed, HomeAssistant, Unavailable
from .store import NotSaved, Store

LOG = logging.getLogger("hunch_app.assistant")

LIST = "assist_pipeline/pipeline/list"
GET = "assist_pipeline/pipeline/get"
UPDATE = "assist_pipeline/pipeline/update"
ENGINE, LOCAL = "conversation_engine", "prefer_local_intents"
FIXED_PHRASES = re.compile(r"speech[\s_-]*to[\s_-]*phrase", re.IGNORECASE)
SELF_EVERY = 5.0   # seconds between readings of the app's own entry in the Supervisor

# What the owner reads.
NO_SUCH = "That assistant is not in Home Assistant any more. Reload the page to see the ones there are now."
ALREADY = "This assistant already uses Hunch."
NOT_ENGLISH = "Hunch understands English only, so it can be added only to an assistant whose language is English."
SUBMIT_FIRST = ("Home Assistant has not added Hunch's agent yet. Open Settings, then Devices and services, find "
                "Hunch Home under Discovered and press Submit. Then try again.")
UNREACHABLE = "Hunch cannot reach Home Assistant just now. It keeps trying. Try again in a minute."
NOT_TAKEN = "Home Assistant did not take the change, so the assistant is as it was."
CANNOT_SAVE = ("Hunch cannot save what this assistant had before, so it has not changed it. Without that, Stop "
               "using Hunch could not put it back. See the note at the top of this page, or check that the machine "
               "has free space, then press the button again.")
SETUP_NOT_KNOWN = ("Home Assistant did not say whether it made the change, so Hunch cannot tell yet whether this "
                   "assistant uses it. Hunch checks again as soon as it can reach Home Assistant, and this page "
                   "shows the answer by itself.")
UNDO_NOT_KNOWN = ("Home Assistant did not say whether it made the change, so Hunch cannot tell yet whether this "
                  "assistant is back as it was. Hunch keeps what it needs to put it back, checks again as soon as "
                  "it can reach Home Assistant, and this page shows the answer by itself.")
WRITE_LIMIT = 10.0   # seconds to wait for Home Assistant's answer to a change
NOTHING_REMEMBERED = ("Hunch did not set this assistant up, so it has nothing to put back. You can choose its "
                      "agent in Home Assistant, under Settings, Voice assistants.")
CHANGED_SINCE = ("This assistant no longer uses Hunch: it was changed in Home Assistant after Hunch set it up. "
                 "Hunch has left it as it is.")


class NotConfirmed(Exception):
    """A change was sent and its answer was lost (late, or the connection dropped): it may have been made."""


def is_english(pipeline: dict) -> bool:
    return str(pipeline.get("language") or "").casefold().replace("_", "-").split("-")[0] == "en"


class Assistants:
    """The assistants, the set-up and its undo. Everything it needs is given."""

    def __init__(self, ha: HomeAssistant, store: Store, agent_name: Callable[[str], str],
                 own_agents: Callable[[], set[str]], hunch_agent: Callable[[], Awaitable[str | None]],
                 states: Callable[[], dict], clock: Callable[[], float] = time.monotonic) -> None:
        self.ha = ha
        self.store = store
        self.agent_name = agent_name
        self.own_agents = own_agents
        self.hunch_agent = hunch_agent      # reads the home and the agents again, then finds Hunch's own
        self.states = states
        self.clock = clock
        self.sidebar: bool | None = None    # the app's sidebar entry; None outside the Supervisor
        self.microphone: str | None = None  # the input chosen in the app's Audio setting
        self._self_at = -SELF_EVERY
        self._lock = asyncio.Lock()
        self.write_limit = WRITE_LIMIT
        self.changed: Callable[[], None] | None = None   # called when a lost answer is settled (the page)
        ha.on_connect(self.settle)

    # Reading.

    async def _call(self, message_type: str, **fields) -> object:
        try:
            return await self.ha.call(message_type, **fields)
        except (Unavailable, TimeoutError):
            raise ValueError(UNREACHABLE) from None

    async def _get(self, pipeline_id: str) -> dict:
        try:
            found = await self._call(GET, pipeline_id=pipeline_id)
        except CallFailed as error:
            if str(error).startswith("not_found"):
                raise LookupError(NO_SUCH) from None
            raise ValueError(UNREACHABLE) from None
        if not isinstance(found, dict) or found.get("id") != pipeline_id:
            raise LookupError(NO_SUCH)
        return found

    async def _write(self, pipeline: dict, changes: dict) -> dict:
        """Write the pipeline back whole, with `changes` over it. `ValueError` when it was certainly not made
        (refused, or Home Assistant not connected), `NotConfirmed` when its answer was lost."""
        fields = {key: value for key, value in pipeline.items() if key != "id"}
        fields.update(changes)
        if not self.ha.connected:
            raise ValueError(UNREACHABLE)   # nothing was sent
        try:
            written = await self.ha.call(UPDATE, timeout=self.write_limit, pipeline_id=pipeline["id"], **fields)
        except CallFailed as error:
            LOG.warning("Home Assistant refused a change to an assistant (%s)", str(error).split(":", 1)[0])
            raise ValueError(NOT_TAKEN) from None
        except Exception as error:  # noqa: BLE001 - sent, and its answer lost: it may have been made
            LOG.warning("Home Assistant's answer to a change to an assistant was lost (%s)", type(error).__name__)
            raise NotConfirmed from None
        return written if isinstance(written, dict) else {**pipeline, **changes}

    async def pipelines(self) -> tuple[list[dict], str | None]:
        reply = await self.ha.call(LIST)
        if not isinstance(reply, dict):
            return [], None
        found = [p for p in reply.get("pipelines") or () if isinstance(p, dict) and isinstance(p.get("id"), str)]
        return found, reply.get("preferred_pipeline")

    def _stt(self, engine: object) -> dict | None:
        if not isinstance(engine, str) or not engine:
            return None
        state = self.states().get(engine) or {}
        friendly = (state.get("attributes") or {}).get("friendly_name")
        name = friendly if isinstance(friendly, str) and friendly.strip() else (
            "Home Assistant Cloud" if engine == "cloud" else engine.split(".", 1)[-1].replace("_", " "))
        return {"name": name, "fixed_phrases": bool(FIXED_PHRASES.search(engine) or FIXED_PHRASES.search(name))}

    def describe(self, pipeline: dict, preferred: str | None = None) -> dict:
        """One assistant as the page shows it (`web.Backend`), plus its language and whether it can be set up."""
        engine = str(pipeline.get(ENGINE) or "")
        uses = engine in self.own_agents()
        before = self.store.assistants.get(pipeline["id"]) if uses else None
        english = is_english(pipeline)
        previous = before.get(ENGINE) if before else None
        return {
            "id": pipeline["id"],
            "name": str(pipeline.get("name") or "Assistant"),
            "preferred": pipeline["id"] == preferred,
            "language": str(pipeline.get("language") or ""),
            "uses_hunch": uses,
            "agent": {"id": engine, "name": self.agent_name(engine)},
            "previous_agent": {"id": previous, "name": self.agent_name(previous)} if previous else None,
            "answer_first": pipeline.get(LOCAL) is True,
            "stt": self._stt(pipeline.get("stt_engine")),
            "can_set_up": not uses and english,
            "setup_note": "" if english or uses else NOT_ENGLISH,
        }

    async def assistants(self) -> list[dict]:
        """Every assistant as the page shows it; [] while Home Assistant cannot be reached. A change whose
        answer was lost is settled first."""
        await self.settle()
        try:
            found, preferred = await self.pipelines()
        except (Unavailable, TimeoutError, CallFailed) as error:
            LOG.info("could not list the assistants (%s)", type(error).__name__)
            return []
        return [self.describe(p, preferred) for p in found]

    async def read_self(self, force: bool = False) -> None:
        """The app's sidebar entry and chosen microphone, from the Supervisor (at most every few seconds)."""
        if not self.ha.access.supervisor:
            self.sidebar = self.microphone = None
            return
        if not force and self.clock() - self._self_at < SELF_EVERY:
            return
        self._self_at = self.clock()
        try:
            status, reply = await self.ha.supervisor("GET", "/addons/self/info")
        except (Unavailable, TimeoutError) as error:
            LOG.info("could not read the app's own entry (%s)", type(error).__name__)
            return
        data = reply.get("data") if isinstance(reply, dict) else None
        if status == 200 and isinstance(data, dict):
            self.sidebar = data.get("ingress_panel") is True
            chosen = data.get("audio_input")
            self.microphone = chosen if isinstance(chosen, str) and chosen else None

    # Changing.

    async def setup(self, pipeline_id: str, sidebar: bool = False) -> dict:
        async with self._lock:
            hunch = await self.hunch_agent()
            pipeline = await self._get(pipeline_id)
            if str(pipeline.get(ENGINE) or "") in self.own_agents():
                raise ValueError(ALREADY)
            if not is_english(pipeline):
                raise ValueError(NOT_ENGLISH)
            if hunch is None:
                raise ValueError(SUBMIT_FIRST)
            before = {ENGINE: pipeline.get(ENGINE), LOCAL: pipeline.get(LOCAL) is True,
                      "name": pipeline.get("name"), "hunch_agent": hunch,
                      "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            try:
                await self.store.remember_assistant(pipeline_id, before)
            except NotSaved:
                LOG.warning("could not save what an assistant had before; it was not changed")
                raise ValueError(CANNOT_SAVE) from None
            try:
                written = await self._write(pipeline, {ENGINE: hunch, LOCAL: True})
            except NotConfirmed:
                await self.store.remember_assistant(pipeline_id, {**before, "pending": "setup", "sidebar": sidebar})
                if await self._settle_one(pipeline_id) != "uses":
                    raise ValueError(SETUP_NOT_KNOWN if pipeline_id in self.store.assistants else NOT_TAKEN) \
                        from None
                written = await self._get(pipeline_id)
            except Exception:
                await self.store.forget_assistant(pipeline_id)   # plainly refused, or never sent
                raise
            else:
                await self._set_up(before, sidebar)
            preferred = (await self.pipelines())[1] if self.ha.connected else None
            return self.describe(written, preferred)

    async def _set_up(self, before: dict, sidebar: bool) -> None:
        """The rest of a set-up, once the assistant is known to use Hunch."""
        LOG.info("an assistant now uses Hunch")
        previous = before[ENGINE]
        if (not self.store.pass_to_chosen and isinstance(previous, str) and previous
                and previous not in self.own_agents()):
            await self.store.save_settings({"pass_to": previous})
        if sidebar:
            await self._sidebar_on()

    # A change whose answer was lost.

    async def settle(self) -> None:
        """Settle every set-up or undo whose answer was lost, from the assistant as Home Assistant has it now.
        Quietly does nothing while Home Assistant cannot be reached, or while a change is being made."""
        pending = [key for key, value in self.store.assistants.items() if value.get("pending")]
        if not pending or not self.ha.connected or self._lock.locked():
            return
        async with self._lock:
            settled = [await self._settle_one(key) for key in pending]
        if any(settled) and self.changed is not None:
            self.changed()

    async def _settle_one(self, pipeline_id: str) -> str | None:
        """Settle one: "uses" when the assistant uses Hunch, "not" when it does not (its memory is then
        forgotten), None when it cannot be read now (nothing changes)."""
        before = self.store.assistants.get(pipeline_id)
        if before is None or not before.get("pending"):
            return None
        try:
            pipeline = await self._get(pipeline_id)
        except LookupError:   # the assistant is gone
            await self.store.forget_assistant(pipeline_id)
            return "not"
        except ValueError:
            return None
        uses = str(pipeline.get(ENGINE) or "") in self.own_agents()
        kept = {key: value for key, value in before.items() if key not in {"pending", "sidebar"}}
        if before["pending"] == "setup" and uses:
            LOG.info("a set-up whose answer was lost did take")
            await self.store.remember_assistant(pipeline_id, kept)
            await self._set_up(kept, before.get("sidebar") is True)
        elif before["pending"] == "undo" and uses:
            LOG.info("an undo whose answer was lost did not take; the way back is kept")
            await self.store.remember_assistant(pipeline_id, kept)
        else:
            LOG.info("a %s whose answer was lost is settled: the assistant does not use Hunch", before["pending"])
            await self.store.forget_assistant(pipeline_id)
        return "uses" if uses else "not"

    async def _sidebar_on(self) -> None:
        if not self.ha.access.supervisor:
            return  # Docker: no Supervisor, no sidebar entry of the app's own
        try:
            status = await self.ha.supervisor_post("/addons/self/options", {"ingress_panel": True})
        except (Unavailable, TimeoutError) as error:
            LOG.warning("could not switch on the sidebar entry (%s)", type(error).__name__)
            return
        if status == 200:
            self.sidebar = True
        else:
            LOG.warning("the Supervisor did not switch on the sidebar entry (HTTP %s)", status)

    async def undo(self, pipeline_id: str) -> dict:
        async with self._lock:
            before = self.store.assistants.get(pipeline_id)
            pipeline = await self._get(pipeline_id)
            if before is None:
                raise ValueError(NOTHING_REMEMBERED)
            if str(pipeline.get(ENGINE) or "") not in self.own_agents():
                raise ValueError(CHANGED_SINCE)
            try:
                written = await self._write(pipeline, {ENGINE: before[ENGINE], LOCAL: before.get(LOCAL) is True})
            except NotConfirmed:
                # Kept until the assistant is seen back as it was.
                await self.store.remember_assistant(pipeline_id, {**before, "pending": "undo"})
                if await self._settle_one(pipeline_id) != "not":
                    raise ValueError(UNDO_NOT_KNOWN if self.store.assistants.get(pipeline_id, {}).get("pending")
                                     else NOT_TAKEN) from None
                written = await self._get(pipeline_id)
            else:
                await self.store.forget_assistant(pipeline_id)
            LOG.info("an assistant was put back as it was")
            preferred = (await self.pipelines())[1] if self.ha.connected else None
            return self.describe(written, preferred)
