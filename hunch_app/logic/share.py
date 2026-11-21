"""Sharing the daily counts: only if the owner chose to, and only while there is somewhere to send them.

The address is `const.SHARE_ENDPOINT`, set from plug-in 0.1.0: xevall.com passes each message on to the
receiving end, and the privacy notice is at `const.PRIVACY_NOTICE`. While the
endpoint is empty, `offered` is false, the choice to share appears nowhere, the glue starts no timer, and
neither `send` nor `delete` ever calls the transport, whatever the stored options say. Setting that one
constant to an https address turns the feature on; a test refuses that while the deletion contact
(`const.DELETION_CONTACT`) is not an email address or the notice's address is empty.

When offered, sharing is still off unless the user turns it on in the integration's options, separately from
set-up. The choice is read again just before each send, so withdrawing it stops sending at once. A summary is
sent only for a finished day (UTC) that has at least one sentence, once, and only if `summary.check` passes.

Withdrawing also deletes what was sent. The record keeps the choice it last saw (`Record.share_chosen`); when
the choice goes from on to off, `settle` marks a deletion as owed (`Record.delete_owed`), the glue sends one
`DELETE` for the install identifier at once, and if that fails it tries again every hour until the server
takes it. Nothing more is sent for this install while a deletion is owed (`due`), so counts sent after the user
turns sharing back on can never be caught by an old deletion arriving late.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from .client import Request, Transport, clean_url
from .summary import check

TIMEOUT = 10.0  # seconds
_HEX = re.compile(r"[0-9a-f]{32}")


def offered(endpoint: str) -> bool:
    """Whether sharing exists at all: an https address is set."""
    return isinstance(endpoint, str) and endpoint.startswith("https://") and clean_url(endpoint) is not None


def due(endpoint: str, chosen: object, last_sent_day: str | None, day: str, delete_owed: bool = False) -> bool:
    """Whether the counts for `day` (a finished UTC day) should go now. Never while a deletion is owed."""
    return (offered(endpoint) and chosen is True and not delete_owed and last_sent_day != day
            and (last_sent_day or "") < day)


def settle(endpoint: str, was_chosen: bool, chosen: object, delete_owed: bool) -> tuple[bool, bool]:
    """The choice to remember and whether a deletion is owed, after seeing the choice `chosen`.

    Turning sharing off (it was on, now it is not) makes a deletion owed; it stays owed until the server takes
    it, even if sharing is turned on again meanwhile. While sharing is not offered nothing changes."""
    if not offered(endpoint):
        return was_chosen, delete_owed
    now = chosen is True
    return now, delete_owed or (was_chosen and not now)


# Sent with both requests, in place of the web session's own User-Agent, which would name the Home Assistant
# and Python versions: the request says what it is and nothing about the machine.
HEADERS = {"Content-Type": "application/json", "User-Agent": "HunchHome"}


def request_for(endpoint: str, summary: Mapping[str, object]) -> Request:
    return Request(url=endpoint, body=json.dumps(summary, sort_keys=False).encode("utf-8"), headers=dict(HEADERS))


def deletion_for(endpoint: str, install_id: str) -> Request:
    """The request that deletes every day's counts this install has sent: the identifier and nothing else."""
    return Request(url=endpoint, body=json.dumps({"install_id": install_id}).encode("utf-8"),
                   headers=dict(HEADERS), method="DELETE")


async def send(transport: Transport, endpoint: str, chosen: object, summary: Mapping[str, object]) -> bool:
    """Send one day's counts; True only when the server took them. Nothing is sent without an address, without
    the user's choice, for a day with no sentences, or for anything `summary.check` does not pass."""
    if not offered(endpoint) or chosen is not True or not check(summary) or not summary.get("sentences"):
        return False
    try:
        status, _ = await transport(request_for(endpoint, summary), TIMEOUT)
    except Exception:  # noqa: BLE001 - a failed send is tried again the next hour, and logged by the glue
        return False
    return 200 <= status < 300


async def delete(transport: Transport, endpoint: str, install_id: str) -> bool:
    """Ask the server to delete everything this install sent; True only when the server took the request
    (it answers the same whether or not it held anything). Nothing is sent without an address or for an
    identifier that is not 32 hexadecimal digits."""
    if not offered(endpoint) or not isinstance(install_id, str) or not _HEX.fullmatch(install_id):
        return False
    try:
        status, _ = await transport(deletion_for(endpoint, install_id), TIMEOUT)
    except Exception:  # noqa: BLE001 - an owed deletion is tried again the next hour, and logged by the glue
        return False
    return 200 <= status < 300
