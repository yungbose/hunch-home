"""The Wyoming server Home Assistant talks to, and the discovery message that offers it.

Home Assistant's built-in Wyoming integration asks what the server offers (`Describe`) and is told: one `handle`
program named "Hunch Home", with home control, for English. That makes it a conversation agent called
"Hunch Home" once the owner confirms the discovered service (the test machine, 20 Nov 2026, check 1).

For each `Transcript` (the sentence, its language, and a context with the conversation id and the device or
satellite it came from) the app runs one turn and answers `Handled` with the words to say, or `NotHandled` when
the agent it passed the sentence to could not help or could not be reached. An empty text is said as nothing
(check 11). The context comes back unchanged.

Under the Supervisor, the app posts a discovery message at start, `{"service": "wyoming", "config": {"uri":
"tcp://<its host name>:10500"}}`, so Home Assistant shows it under "Discovered". In Docker there is no
Supervisor: the owner adds the Wyoming integration by the machine's address and port 10500.

**Who may connect.** The Wyoming protocol has no sign-in: anything that reaches the port can send a sentence
that switches a device or unlocks a named lock. So the server keeps an allow-list of peer addresses
(`listening`): a connection from any other address is closed before a single event is read, with no answer,
and one warning names that address (only the address, once per address). The first accepted peer is logged
once at info level, so the real address can be checked against the list.

- As a Home Assistant app the server must listen on every address inside its container; only Home Assistant's
  own address on the Supervisor's internal network is allowed by default (`SUPERVISOR_HA`). Other apps on that
  network are not. `HUNCH_ALLOW_FROM` replaces the list.
- In Docker the listening address is `HUNCH_WYOMING_HOST`, `127.0.0.1` by default, where only loopback is
  allowed. Set to anything that is not loopback, it needs `HUNCH_ALLOW_FROM` (addresses or networks, comma
  separated), or the app refuses to start and says why.

The port must never be published to a network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field

from wyoming.asr import Transcript
from wyoming.event import Event
from wyoming.handle import Handled, NotHandled
from wyoming.info import Attribution, Describe, HandleModel, HandleProgram, Info
from wyoming.server import AsyncEventHandler

from . import NAME, __version__
from .api import DONE, NOTHING, PASSED

LOG = logging.getLogger("hunch_app.agent")

PORT = 10500
SUPERVISOR_HA = "172.30.32.1"   # Home Assistant on the Supervisor's internal network
LOOPBACK = ("127.0.0.0/8", "::1/128")


class ListenError(ValueError):
    """The listening set-up is unsafe or unreadable. The message says what to change."""


def _network(text: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    try:
        return ipaddress.ip_network(text.strip(), strict=False)
    except ValueError:
        raise ListenError(f"HUNCH_ALLOW_FROM holds {text.strip()!r}, which is not an address or a "
                          "network") from None


def _is_loopback(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


@dataclass
class Listening:
    """Where the Wyoming server listens and whom it answers."""

    host: str
    port: int
    allow: tuple = field(default_factory=tuple)   # ip_network objects
    _refused: set = field(default_factory=set, repr=False)
    _first: bool = field(default=True, repr=False)

    def allows(self, peer: str | None) -> bool:
        if not peer:
            return False
        try:
            address = ipaddress.ip_address(peer.split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        return any(address in network for network in self.allow)

    def admit(self, peer: str | None) -> bool:
        """Whether to answer this peer; logs a refusal once per address, and the first accepted peer once."""
        if not self.allows(peer):
            if peer not in self._refused:
                self._refused.add(peer)
                LOG.warning("refused a Wyoming connection from %s (not on the allow-list)", peer)
            return False
        if self._first:
            self._first = False
            LOG.info("first Wyoming connection accepted, from %s", peer)
        return True


def listening(env: Mapping[str, str], supervisor: bool) -> Listening:
    """The listening set-up from the environment; raises `ListenError` for an unsafe one."""
    port = int(env.get("HUNCH_PORT", str(PORT)) or PORT)
    given = [part for part in (env.get("HUNCH_ALLOW_FROM") or "").split(",") if part.strip()]
    allow = tuple(_network(part) for part in given)
    if supervisor:
        return Listening("0.0.0.0", port, allow or (_network(SUPERVISOR_HA),))
    host = (env.get("HUNCH_WYOMING_HOST") or "127.0.0.1").strip()
    if _is_loopback(host):
        return Listening(host, port, tuple(_network(n) for n in LOOPBACK) + allow)
    if not allow:
        raise ListenError(f"HUNCH_WYOMING_HOST is {host}, which other machines can reach, and HUNCH_ALLOW_FROM "
                          "is not set. The Wyoming port has no sign-in: set HUNCH_ALLOW_FROM to Home "
                          "Assistant's address (or its network), or listen on 127.0.0.1")
    return Listening(host, port, allow)
ATTRIBUTION = Attribution(name="xevall", url="https://xevall.com")
LANGUAGES = ["en"]


def info() -> Info:
    return Info(handle=[HandleProgram(
        name=NAME, description="Understands home commands in your own words", version=__version__,
        installed=True, attribution=ATTRIBUTION,
        models=[HandleModel(name="Hunch-1 Home", description="Hunch-1 Home", version=__version__, installed=True,
                            attribution=ATTRIBUTION, languages=LANGUAGES)],
        supports_home_control=True)])


class AgentHandler(AsyncEventHandler):
    """One Wyoming connection from Home Assistant."""

    def __init__(self, app, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.app = app

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(info().event())
            return True
        if not Transcript.is_type(event.type):
            return True
        transcript = Transcript.from_event(event)
        context = transcript.context if isinstance(transcript.context, dict) else {}
        try:
            turn = await self.app.handle(transcript.text, transcript.language, context.get("conversation_id"),
                                         context.get("device_id"), context.get("satellite_id"))
        except Exception:
            LOG.exception("a sentence could not be handled")
            await self.write_event(NotHandled(text="Sorry, Hunch Home had a problem.", context=context).event())
            return True
        handled = turn.outcome in (DONE, NOTHING) or (turn.outcome == PASSED and turn.handled)
        if handled:
            reply = Handled(text=turn.reply or "", context=context,
                            continue_conversation=True if turn.continue_conversation else None)
        else:
            reply = NotHandled(text=turn.reply or "", context=context)
        await self.write_event(reply.event())
        return True


async def serve(app, where: Listening, started: asyncio.Event | None = None) -> None:
    """Run the Wyoming server until cancelled. A peer not on the allow-list is closed before any event is
    read."""

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        address = peer[0] if isinstance(peer, tuple) and peer else None
        if not where.admit(address):
            writer.close()
            return
        handler = AgentHandler(app, reader, writer)
        try:
            await handler.run()
        finally:
            writer.close()

    server = await asyncio.start_server(connected, where.host, where.port)
    if where.port == 0:
        where.port = server.sockets[0].getsockname()[1]
    if started is not None:
        started.set()
    async with server:
        await server.serve_forever()


def discovery_message(hostname: str | None = None, port: int = PORT) -> dict:
    return {"service": "wyoming", "config": {"uri": f"tcp://{hostname or socket.gethostname()}:{port}"}}


async def announce(ha, port: int = PORT) -> bool:
    """Post the discovery message to the Supervisor; skipped (False) outside it."""
    if not ha.access.supervisor:
        return False
    try:
        status = await ha.supervisor_post("/discovery", discovery_message(port=port))
    except Exception as error:  # noqa: BLE001 - the owner can still add the integration by hand
        LOG.warning("could not offer the agent to Home Assistant (%s)", type(error).__name__)
        return False
    if status != 200:
        LOG.warning("Home Assistant's Supervisor did not take the discovery message (HTTP %s)", status)
        return False
    LOG.info("offered the agent to Home Assistant; confirm it under Settings, Devices and services")
    return True
