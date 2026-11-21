"""Talking to Home Assistant: REST for actions, one websocket for everything else, reconnected by itself.

Two ways in (`access_from_env`):

- **As a Home Assistant app**: the Supervisor's proxy, `http://supervisor/core/api` and
  `ws://supervisor/core/websocket`, with the token the Supervisor gives every app (`SUPERVISOR_TOKEN`). Changes
  the app makes this way are recorded in Home Assistant under its system user "Supervisor" (seen in the test
  machine, 20 Nov 2026).
- **In Docker**: a plain address and a long-lived access token made by the owner (`HASS_URL`, `HASS_TOKEN`), so
  `http://homeassistant.local:8123/api` and `ws://homeassistant.local:8123/api/websocket`. Changes are recorded
  under the user who made the token.

The websocket is opened once and kept. The first connection is patient: it is tried again after 1, 2, 4, 8,
15 and then every 30 seconds, since the app may start long before Home Assistant does. Once it has been
connected, a lost connection is tried again after 1, 2 and then every 3 seconds however long it takes, since a
lost connection is nearly always Home Assistant restarting, and every try it refuses or drops while it starts
would otherwise make the app wait longer (the test machine, 20 Nov 2026, image check 12: the app reconnected
about 70 seconds after Home Assistant came back). An access token Home Assistant refuses is tried again every
30 seconds. Each time, event subscriptions are made again and each `on_connect` hook runs (the home is read
again then). While it is down, a call raises `Unavailable` at once rather than waiting. A lost connection is
one warning in the log; the tries after it are logged only at debug level, and the reconnection at info.

The token is never logged, never put in an error message and never in a repr. Requests and replies are not
logged either; only what failed, by kind.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

LOG = logging.getLogger("hunch_app.ha")

SUPERVISOR_REST = "http://supervisor/core/api"
SUPERVISOR_WS = "ws://supervisor/core/websocket"
SUPERVISOR = "http://supervisor"
TIMEOUT = 10.0       # seconds for an ordinary call
BACKOFF = (1, 2, 4, 8, 15, 30)   # seconds between tries until the first connection
AGAIN = (1, 2, 3)                # seconds between tries once it has been connected
REFUSED = 30                     # seconds before trying a refused access token again


def wait_before(tries: int, connected_before: bool) -> float:
    """Seconds to wait before the next try to open the websocket, after `tries` failed tries in a row."""
    steps = AGAIN if connected_before else BACKOFF
    return steps[min(tries, len(steps) - 1)]


class ConfigError(ValueError):
    """Neither way in is set up. The message says what to set, never a token's value."""


class Unavailable(ConnectionError):
    """Home Assistant cannot be reached just now."""


class CallFailed(RuntimeError):
    """Home Assistant answered a websocket call with an error. The message is its code and message."""


@dataclass(frozen=True)
class Access:
    rest: str        # base for REST, ending in /api (no slash after)
    ws: str          # the websocket's address
    token: str = field(repr=False)
    supervisor: bool = False
    supervisor_url: str = SUPERVISOR   # the Supervisor's own address (a stand-in's in tests and the demo)


def access_from_env(env: Mapping[str, str]) -> Access:
    """The Supervisor's proxy when the app runs under it, else `HASS_URL` and `HASS_TOKEN`."""
    token = env.get("SUPERVISOR_TOKEN", "").strip()
    if token:
        return Access(SUPERVISOR_REST, SUPERVISOR_WS, token, supervisor=True)
    url, token = env.get("HASS_URL", "").strip(), env.get("HASS_TOKEN", "").strip()
    if not url or not token:
        raise ConfigError("set HASS_URL (such as http://homeassistant.local:8123) and HASS_TOKEN (a long-lived "
                          "access token made in your Home Assistant profile), or run the app under Home "
                          "Assistant's Supervisor")
    parts = urlsplit(url.rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ConfigError("HASS_URL must be an http or https address with no user name or password in it")
    path = parts.path.removesuffix("/api")
    rest = urlunsplit((parts.scheme, parts.netloc, path + "/api", "", ""))
    ws = urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, path + "/api/websocket", "", ""))
    return Access(rest, ws, token)


EventHandler = Callable[[dict], None]
Hook = Callable[[], Awaitable[None]]


class HomeAssistant:
    """One REST session and one kept websocket."""

    def __init__(self, access: Access, session=None, sleep=asyncio.sleep) -> None:
        """`sleep` waits between tries to open the websocket (a test's own, to see the waits)."""
        self.access = access
        self._sleep = sleep
        self._session = session
        self._own_session = session is None
        self._ws = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[str, EventHandler] = {}   # event type -> handler
        self._sub_ids: dict[int, str] = {}                   # subscription id -> event type
        self._hooks: list[Hook] = []
        self._drop_hooks: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None
        self._connected = asyncio.Event()
        self._connected_before = False
        self._closing = False
        self.version: str | None = None   # Home Assistant's version, as its websocket says

    @property
    def session(self):
        """The web session (made at `start`), shared with the daily counts' sender."""
        return self._session

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access.token}", "Content-Type": "application/json"}

    # Starting and stopping.

    async def start(self) -> None:
        """Open the session and start keeping the websocket open (returns at once)."""
        if self._session is None:
            import aiohttp

            self._session = aiohttp.ClientSession()
        if self._task is None:
            self._task = asyncio.create_task(self._keep_open(), name="hunch websocket")

    async def wait_connected(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def close(self) -> None:
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._ws is not None:
            await self._ws.close()
        if self._own_session and self._session is not None:
            await self._session.close()

    def on_connect(self, hook: Hook) -> None:
        """Run `hook` after every (re)connection, once subscriptions are made again."""
        self._hooks.append(hook)

    def on_drop(self, hook: Callable[[], None]) -> None:
        """Call `hook()` each time a connection that was up is lost."""
        self._drop_hooks.append(hook)

    def on_event(self, event_type: str, handler: EventHandler) -> None:
        """Call `handler(event)` for every event of this type, across reconnections. Set before `start`, or the
        subscription is made at the next connection."""
        self._subscriptions[event_type] = handler

    # The websocket.

    async def _keep_open(self) -> None:
        tries = 0
        said = False   # this outage has had its one warning
        while not self._closing:
            wait, why = None, "it closed"
            try:
                await self._connect_and_read()
            except asyncio.CancelledError:
                raise
            except PermissionError:
                LOG.warning("Home Assistant refused the access token; trying again in %d s", REFUSED)
                wait, why = REFUSED, ""
            except Exception as error:  # noqa: BLE001 - every failure means: wait, then open it again
                why = type(error).__name__
            finally:
                was_connected = self._connected.is_set()
                self._connected.clear()
                self._fail_pending()
                self._ws = None
            if self._closing:
                return
            if was_connected:   # however the connection ended, a new outage starts from the first wait
                tries, said = 0, False
                for hook in self._drop_hooks:
                    try:
                        hook()
                    except Exception:
                        LOG.exception("a step after losing the connection failed")
            if why and not said:
                said = True
                LOG.warning("%s Home Assistant's websocket (%s); trying again every few seconds",
                            "lost" if self._connected_before else "cannot reach", why)
            elif why:
                LOG.debug("still cannot reach Home Assistant's websocket (%s)", why)
            await self._sleep(wait if wait is not None else wait_before(tries, self._connected_before))
            tries += 1

    def _fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(Unavailable("the websocket closed"))
        self._pending.clear()
        self._sub_ids.clear()

    async def _connect_and_read(self) -> None:
        import aiohttp

        async with self._session.ws_connect(self.access.ws, heartbeat=30, max_msg_size=0) as ws:
            first = await ws.receive_json(timeout=TIMEOUT)
            if first.get("type") != "auth_required":
                raise ConnectionError("not Home Assistant's websocket")
            await ws.send_json({"type": "auth", "access_token": self.access.token})
            auth = await ws.receive_json(timeout=TIMEOUT)
            if auth.get("type") != "auth_ok":
                raise PermissionError("auth refused")
            self.version = auth.get("ha_version")
            self._ws = ws
            reader = asyncio.create_task(self._read(ws))
            try:
                for event_type in list(self._subscriptions):
                    await self._subscribe(event_type)
                self._connected.set()
                self._connected_before = True
                LOG.info("connected to Home Assistant %s", self.version or "")
                for hook in self._hooks:
                    try:
                        await hook()
                    except Exception as error:  # noqa: BLE001 - one hook failing must not drop the connection
                        LOG.warning("a start-up step after connecting failed (%s)", type(error).__name__)
                await reader
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, aiohttp.ClientError):
                    await reader

    async def _read(self, ws) -> None:
        import aiohttp

        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE}:
                    break
                continue
            data = message.json()
            for item in data if isinstance(data, list) else [data]:
                self._dispatch(item)

    def _dispatch(self, item: dict) -> None:
        kind, ident = item.get("type"), item.get("id")
        if kind == "result":
            future = self._pending.pop(ident, None)
            if future is not None and not future.done():
                if item.get("success"):
                    future.set_result(item.get("result"))
                else:
                    error = item.get("error") or {}
                    future.set_exception(CallFailed(f"{error.get('code')}: {error.get('message')}"))
        elif kind == "event":
            event_type = self._sub_ids.get(ident)
            handler = self._subscriptions.get(event_type) if event_type else None
            if handler is not None:
                try:
                    handler(item.get("event") or {})
                except Exception:
                    LOG.exception("an event handler failed")

    async def _subscribe(self, event_type: str) -> None:
        ident = next(self._ids)
        self._sub_ids[ident] = event_type
        await self._send(ident, {"type": "subscribe_events", "event_type": event_type})

    async def _send(self, ident: int, message: dict, timeout: float = TIMEOUT) -> object:
        if self._ws is None:
            raise Unavailable("not connected")
        future = asyncio.get_running_loop().create_future()
        self._pending[ident] = future
        try:
            await self._ws.send_json({"id": ident, **message})
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(ident, None)

    async def call(self, message_type: str, timeout: float = TIMEOUT, **fields) -> object:
        """One websocket command and its result. Raises `Unavailable` while not connected and `CallFailed`
        when Home Assistant answers with an error."""
        if not self.connected and self._ws is None:
            raise Unavailable("not connected")
        return await self._send(next(self._ids), {"type": message_type, **fields}, timeout)

    # REST.

    async def rest(self, method: str, path: str, body: object | None = None,
                   timeout: float = TIMEOUT) -> tuple[int, object]:
        """One REST call under `/api`: its status and its JSON (or text when it is not JSON). Raises
        `Unavailable` when Home Assistant cannot be reached and `TimeoutError` past `timeout`."""
        import aiohttp

        if self._session is None:
            raise Unavailable("not started")
        url = self.access.rest + path
        try:
            async with self._session.request(method, url, json=body, headers=self._headers(),
                                             timeout=aiohttp.ClientTimeout(total=timeout)) as reply:
                text = await reply.text()
                try:
                    import json

                    return reply.status, json.loads(text)
                except ValueError:
                    return reply.status, text
        except aiohttp.ClientError as error:
            raise Unavailable(type(error).__name__) from None

    async def supervisor(self, method: str, path: str, body: object | None = None,
                         timeout: float = TIMEOUT) -> tuple[int, object]:
        """A call to the Supervisor itself (`http://supervisor<path>`, such as `/discovery` or
        `/addons/self/options`), only under the Supervisor: its status and its JSON (or None)."""
        import aiohttp

        if not self.access.supervisor or self._session is None:
            raise Unavailable("not under the Supervisor")
        try:
            async with self._session.request(method, self.access.supervisor_url + path, json=body,
                                             headers=self._headers(),
                                             timeout=aiohttp.ClientTimeout(total=timeout)) as reply:
                try:
                    data = await reply.json(content_type=None)
                except ValueError:
                    data = None
                return reply.status, data
        except aiohttp.ClientError as error:
            raise Unavailable(type(error).__name__) from None

    async def supervisor_post(self, path: str, body: object, timeout: float = TIMEOUT) -> int:
        """A POST to the Supervisor; its status."""
        status, _ = await self.supervisor("POST", path, body, timeout)
        return status
