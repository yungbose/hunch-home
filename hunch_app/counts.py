"""The daily counts: off unless the owner turns them on; the same words and receiving end as plug-in 0.1.1.

The rules are the plug-in's (`logic/summary.py` makes the counts, with no words in them; `logic/share.py` sends
them and their deletion). This module runs them as the plug-in's `localrecord.py` did:

- Only while `share.offered(SHARE_ENDPOINT)`. Then a check every hour sends yesterday's counts (UTC) once, if
  the owner chose to share; the choice is read from the settings just before sending.
- Turning the choice off makes a deletion of what was sent owed (`share.settle`). An owed deletion is sent at
  once (a few seconds after start, or straight after the setting changes) and then every hour until the
  receiving end takes it. Nothing more is sent while one is owed.
- Sentences from listening mode are not counted: the counts are made only from turns marked as having come
  through Assist. A turn with no mark (its app-side details lost) is left out, never guessed to be from Assist.
- For the page (`data.py`): when a deletion last went through or last failed is kept in the store's `sharing`,
  `deleting` is true while one is being asked for, and `changed` (when set) is called after a pass that changed
  something, so the page reads it again.

The one thing that leaves the machine by the app's own doing is this, and only when the owner turns it on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from . import __version__
from .api import ASSIST, PRIVACY_URL
from .logic import share, summary
from .logic.client import Request, Transport
from .logic.record import day_of
from .store import Store

LOG = logging.getLogger("hunch_app.counts")

# The same receiving end as plug-in 0.1.1 (its const.py): xevall.com passes each message on.
SHARE_ENDPOINT = "https://www.xevall.com/api/hunch-home/counts"
DELETION_CONTACT = "privacy@xevall.com"
# The privacy notice's public address (PRIVACY.md in the app's public repository): the one constant, `api.PRIVACY_URL`,
# which the page's state also carries.
PRIVACY_NOTICE = PRIVACY_URL

CHECK_EVERY = 3600.0  # seconds
DELETE_SOON = 5.0     # seconds after start: an owed deletion is first tried then, not an hour later


def session_transport(session) -> Transport:
    """A transport for `share` over an aiohttp session: the reply's status only, never its body."""
    import aiohttp

    async def transport(request: Request, timeout: float) -> tuple[int, bytes]:
        async with session.request(request.method, request.url, data=request.body, headers=request.headers,
                                   timeout=aiohttp.ClientTimeout(total=timeout)) as reply:
            return reply.status, b""

    return transport


def summary_for(store: Store, day: str) -> dict:
    """One UTC day's counts, from the turns marked as having come through Assist (never one with no mark): what
    the sender sends, and what the page shows."""
    entries = [entry for entry in store.record.entries if store.extras.get(entry.id, {}).get("source") == ASSIST]
    return summary.summarise(entries, day, store.record.install_id, __version__)


class Counts:
    """The sender, joined to the store's record and settings."""

    def __init__(self, store: Store, transport: Transport, endpoint: str = SHARE_ENDPOINT,
                 clock: Callable[[], float] | None = None) -> None:
        self.store = store
        self.transport = transport
        self.endpoint = endpoint
        self.clock = clock or store.clock
        self._task: asyncio.Task | None = None
        self._busy = asyncio.Lock()
        self.deleting = False                          # a deletion is being asked for just now
        self.changed: Callable[[], None] | None = None  # told after a pass that changed something

    @property
    def chosen(self) -> bool:
        return share.offered(self.endpoint) and self.store.settings.get("share_counts") is True

    def counts(self, day: str) -> dict:
        """One UTC day's counts, from the turns that came through Assist."""
        return summary_for(self.store, day)

    def settle(self) -> bool:
        """Remember the choice as it is now; True when a withdrawal has just made a deletion owed."""
        record = self.store.record
        before = record.delete_owed
        chosen, owed = share.settle(self.endpoint, record.share_chosen, self.chosen, record.delete_owed)
        record.share_chosen, record.delete_owed = chosen, owed
        return owed and not before

    async def check(self) -> None:
        """One pass: settle the choice, send an owed deletion, then yesterday's counts if they are due."""
        async with self._busy:
            record = self.store.record
            changed = False
            if self.settle():
                changed = True
            if record.delete_owed:
                self.deleting = True
                try:
                    deleted = await share.delete(self.transport, self.endpoint, record.install_id)
                finally:
                    self.deleting = False
                if deleted:
                    record.delete_owed = False
                    self.store.sharing.update(deleted_at=self.clock(), delete_tried_at=None)
                else:
                    self.store.sharing["delete_tried_at"] = self.clock()
                    LOG.debug("the shared counts are not deleted yet; trying again in an hour")
                changed = True
            day = day_of(self.clock() - 86400)
            if share.due(self.endpoint, self.chosen, record.last_sent_day, day, record.delete_owed):
                if await share.send(self.transport, self.endpoint, self.chosen, self.counts(day)):
                    record.last_sent_day = day
                    changed = True
                else:
                    LOG.debug("the daily counts for %s were not sent", day)
            if changed:
                await self.store.save_record()
                if self.changed is not None:
                    self.changed()

    def start(self) -> None:
        """Begin the hourly check (nothing at all while sharing is not offered)."""
        if not share.offered(self.endpoint) or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="hunch daily counts")

    async def _run(self) -> None:
        first = DELETE_SOON if self.store.record.delete_owed else CHECK_EVERY
        await asyncio.sleep(first)
        while True:
            try:
                await self.check()
            except Exception:  # noqa: BLE001 - one failed pass must not end the hourly check
                LOG.warning("the daily counts check failed; trying again in an hour")
            await asyncio.sleep(CHECK_EVERY)

    async def settings_changed(self) -> None:
        """The owner changed a setting: a withdrawal sends the deletion now."""
        if share.offered(self.endpoint) and self.settle():
            await self.store.save_record()
            await self.check()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
