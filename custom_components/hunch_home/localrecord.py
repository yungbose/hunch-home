"""The local record and the opt-in sender, joined to Home Assistant (glue only; the rules are in `hunch_logic`).

- Storage: Home Assistant's `Store` helper (`homeassistant.helpers.storage`), one private JSON file per config
  entry under `.storage` (`hunch_home.<entry id>.record`, mode 0600, written atomically). Saves are delayed and
  batched (`SAVE_DELAY`), and the data is copied in the event loop and serialised and written in a worker
  thread (`serialize_in_event_loop=False`), so a thousand entries never block the loop. The helper writes any
  pending save when Home Assistant stops; unloading the entry saves at once; removing the entry deletes the
  file.
- Undone: before a plan is carried out, the state of each entity it names is read; after, the entry is watched
  with `async_track_state_change_event` for `undo.WINDOW` seconds, each change passed to `undo.Tracker` with
  its context. Timers (`async_call_later`) settle the verdicts.
- Sharing: only when `share.offered(SHARE_ENDPOINT)`. Then an hourly check sends yesterday's counts once, if
  the user chose to share; the choice is read from the options just before sending. Turning the choice off
  reloads the entry (an options change); on loading, and on each hourly check, `share.settle` compares the
  choice with the one the record last saw, and a withdrawal makes a deletion owed. An owed deletion is sent a
  few seconds after loading and then every hour until the server takes it. Deleting the integration's entry
  also asks once for the deletion when sharing was on or a deletion was owed, before the record's file (and
  with it the install identifier) is deleted.

Checked against Home Assistant 2026.11.2's source (helpers/storage.py, helpers/event.py, core.py's Context).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from functools import partial

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store

from .const import CONF_RECORD, CONF_SHARE_COUNTS, DEFAULT_RECORD, DOMAIN, PLUGIN_VERSION, SHARE_ENDPOINT
from .hunch_logic import record, share, summary, undo
from .hunch_logic.client import Answer, HandBack, Request
from .hunch_logic.decide import Plan

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
SAVE_DELAY = 10  # seconds: one write for a burst of sentences
SHARE_CHECK = timedelta(hours=1)
DELETE_SOON = 5  # seconds after loading: an owed deletion is first tried then, not an hour later


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict]:
    return Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.record", private=True, atomic_writes=True,
                 serialize_in_event_loop=False)


class LocalRecord:
    """One config entry's record, its undo watches and its sender. Kept as the entry's `runtime_data`."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.record = record.Record()
        self.tracker = undo.Tracker()
        self._store = _store(hass, entry.entry_id)
        self._listeners: dict[str, CALLBACK_TYPE] = {}
        self._timer: CALLBACK_TYPE | None = None
        self._share_timer: CALLBACK_TYPE | None = None
        self._delete_timer: CALLBACK_TYPE | None = None

    @property
    def on(self) -> bool:
        return self.entry.options.get(CONF_RECORD, DEFAULT_RECORD) is not False

    @property
    def sharing_chosen(self) -> bool:
        return share.offered(SHARE_ENDPOINT) and self.entry.options.get(CONF_SHARE_COUNTS) is True

    async def async_load(self) -> None:
        self.record = record.Record.from_json(await self._store.async_load(), time.time())
        self._save()

    @callback
    def _save(self) -> None:
        data = self.record.to_json()  # copied here, in the loop; serialised in a worker thread
        self._store.async_delay_save(lambda: data, SAVE_DELAY)

    # What happened to each sentence.

    @callback
    def note_home_assistant_first(self, sentence: str) -> None:
        """Home Assistant's own exact sentences answered; Hunch was not asked."""
        if self.on:
            self.record.add(record.entry_for(sentence, None, None, time.time()), time.time())
            self._save()

    @callback
    def note_hand_back(self, sentence: str, answer: Answer | HandBack, why: HandBack,
                       plan: Plan | None = None, error: bool = False) -> None:
        """The sentence went back to Home Assistant's own agent; `error` when Home Assistant could not carry
        out `plan`."""
        if self.on:
            self.record.add(record.entry_for(sentence, answer, why, time.time(), error=error, plan=plan),
                            time.time())
            self._save()

    @callback
    def before(self, plan: Plan, context: Context) -> dict[str, undo.Seen]:
        """Read the entities' states just before a plan is carried out, and tell the watches of earlier
        entries that this command now speaks for those entities."""
        if not self.on:
            return {}
        self.tracker.turn(context.id, plan.entity_ids)
        seen = {}
        for entity_id in plan.entity_ids:
            if (state := self.hass.states.get(entity_id)) is not None:
                seen[entity_id] = undo.seen_from(entity_id, state.state, state.attributes)
        return seen

    @callback
    def not_carried_out(self, context: Context) -> None:
        """A plan announced by `before` was not carried out after all."""
        self.tracker.release(context.id)

    @callback
    def note_carried_out(self, sentence: str, answer: Answer, plan: Plan, context: Context,
                         before: dict[str, undo.Seen]) -> None:
        """The plan was carried out: keep it, and watch whether it is undone."""
        if not self.on:
            return
        entry = self.record.add(record.entry_for(sentence, answer, plan, time.time()), time.time())
        watches = undo.watches_for(plan, before)
        # The states as the plan left them (its service calls are made with blocking=True).
        after = {watch.entity_id: state.state for watch in watches
                 if (state := self.hass.states.get(watch.entity_id)) is not None}
        verdict = self.tracker.start(entry.id, context.id, watches, time.monotonic(), after)
        if verdict is not None:
            self._settle(verdict)
            return
        self._listeners[entry.id] = async_track_state_change_event(
            self.hass, [watch.entity_id for watch in watches], partial(self._async_changed, entry.id))
        self._schedule()
        self._save()

    @callback
    def _async_changed(self, key: str, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None:
            return
        entity_id = event.data["entity_id"]
        self.tracker.change(key, entity_id, undo.seen_from(entity_id, new_state.state, new_state.attributes),
                            event.context.id, event.context.parent_id, event.context.user_id, time.monotonic())
        self._schedule()

    @callback
    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None
        if (when := self.tracker.next_due()) is not None:
            self._timer = async_call_later(self.hass, max(0.0, when - time.monotonic()) + 0.05, self._async_due)

    @callback
    def _async_due(self, _now: object) -> None:
        self._timer = None
        for verdict in self.tracker.due(time.monotonic()):
            self._settle(verdict)
        self._schedule()

    @callback
    def _settle(self, verdict: undo.Verdict) -> None:
        if (unsubscribe := self._listeners.pop(verdict.key, None)) is not None:
            unsubscribe()
        self.record.set_undone(verdict.key, verdict.undone, verdict.by, verdict.entity_id)
        self._save()

    # The user's own view and the two actions.

    @callback
    def show(self) -> dict:
        """The whole record, sentences included, for the `hunch_home.show_record` action."""
        return {"on": self.on, "entries": [record.full_view(entry) for entry in self.record.entries]}

    async def async_clear(self) -> None:
        """Delete every entry now, and stop watching."""
        self._stop_watching()
        self.record.clear()
        await self._store.async_save(self.record.to_json())

    @callback
    def _stop_watching(self) -> None:
        for verdict in self.tracker.stop():
            self._settle(verdict)
        if self._timer is not None:
            self._timer()
            self._timer = None

    # Sharing (nothing at all while SHARE_ENDPOINT is empty; nothing sent until the owner chooses it).

    def counts(self, day: str) -> dict:
        return summary.summarise(self.record.entries, day, self.record.install_id, PLUGIN_VERSION)

    def sharing_view(self) -> dict:
        """For diagnostics: whether sharing is offered and chosen, whether a deletion of what was sent is still
        owed, and what would be sent for yesterday, with the endpoint empty or not. The install identifier is
        left out (it is what deletes this home's counts); its public reference stands in for it."""
        day = record.day_of(time.time() - 86400)
        view = {"offered": share.offered(SHARE_ENDPOINT), "chosen": self.sharing_chosen,
                "last_sent_day": self.record.last_sent_day, "deletion_owed": self.record.delete_owed,
                "what_would_be_sent": self.counts(day)}
        return record.shown_sharing(view, self.record.install_id)

    @callback
    def start_sharing(self) -> None:
        if not share.offered(SHARE_ENDPOINT):
            return
        self._settle_choice()
        self._share_timer = async_track_time_interval(self.hass, self._async_share, SHARE_CHECK,
                                                      name=f"{DOMAIN} daily counts", cancel_on_shutdown=True)
        if self.record.delete_owed:
            self._delete_timer = async_call_later(self.hass, DELETE_SOON, self._async_delete_soon)

    @callback
    def _settle_choice(self) -> None:
        """Remember the choice as it is now; a withdrawal makes a deletion owed (`share.settle`)."""
        chosen, owed = share.settle(SHARE_ENDPOINT, self.record.share_chosen, self.sharing_chosen,
                                    self.record.delete_owed)
        if (chosen, owed) != (self.record.share_chosen, self.record.delete_owed):
            self.record.share_chosen, self.record.delete_owed = chosen, owed
            self._save()

    async def _async_delete_soon(self, _now: object) -> None:
        self._delete_timer = None
        await self._async_share(None)

    async def _async_share(self, _now: object) -> None:
        self._settle_choice()
        if self.record.delete_owed:
            if await share.delete(self._async_post, SHARE_ENDPOINT, self.record.install_id):
                self.record.delete_owed = False
                self._save()
            else:
                _LOGGER.debug("Hunch: the shared counts are not deleted yet; trying again in an hour")
        day = record.day_of(time.time() - 86400)
        if not share.due(SHARE_ENDPOINT, self.sharing_chosen, self.record.last_sent_day, day,
                         self.record.delete_owed):
            return
        if await share.send(self._async_post, SHARE_ENDPOINT, self.sharing_chosen, self.counts(day)):
            self.record.last_sent_day = day
            self._save()
        else:
            _LOGGER.debug("Hunch: the daily counts for %s were not sent", day)

    async def _async_post(self, request: Request, timeout: float) -> tuple[int, bytes]:
        return await _async_request(self.hass, request, timeout)

    async def async_unload(self) -> None:
        for timer in (self._share_timer, self._delete_timer):
            if timer is not None:
                timer()
        self._share_timer = self._delete_timer = None
        self._stop_watching()
        await self._store.async_save(self.record.to_json())

    @staticmethod
    async def async_remove(hass: HomeAssistant, entry_id: str) -> None:
        """Delete the record's file when the integration's entry is deleted. If sharing was on, or a deletion
        was still owed, ask the server once to delete what was sent first: the install identifier goes with
        the file, so nothing could ask later."""
        store = _store(hass, entry_id)
        if share.offered(SHARE_ENDPOINT):
            kept = record.Record.from_json(await store.async_load(), time.time())
            asked = kept.share_chosen or kept.delete_owed
            if asked and not await share.delete(partial(_async_request, hass), SHARE_ENDPOINT, kept.install_id):
                _LOGGER.debug("Hunch: the shared counts could not be deleted as the entry was removed")
        await store.async_remove()


async def _async_request(hass: HomeAssistant, request: Request, timeout: float) -> tuple[int, bytes]:
    """Send one sharing request (the daily counts, or their deletion) with Home Assistant's own web session."""
    session = async_get_clientsession(hass)
    async with session.request(request.method, request.url, data=request.body, headers=request.headers,
                               timeout=aiohttp.ClientTimeout(total=timeout)) as reply:
        return reply.status, b""
