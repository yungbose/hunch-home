"""Asking the Hunch server, and reading its reply.

The server is the Hunch Home server (https://github.com/yungbose/hunch-home-server; the `hunch_home` runtime
underneath). Its endpoint `POST /api/home` takes `{"text": ...}` and returns the answer dict documented in the
runtime's API page (`hunch_home/API.md` in the model's release). The plug-in reads only these keys of it:

- `action` (str): what to do, one of the actions, or "not for me";
- `kind` (str): what kind of thing, one of the kinds, or "none";
- `declined` (bool): true when the match was below the server's cut-off;
- `similarity`, `label` and `ms`: for the plug-in's debug log line; `similarity` is also the score kept in the
  local record (`record.py`);
- `version` (optional): the model's version, such as "1.1", kept in the local record and counted in the daily
  summary. A value that is not a version number of digits and dots (`VERSION`) is ignored.

A server reachable from the home network asks for a shared secret: the plug-in sends `Authorization: Bearer
<secret>` when one is set in its settings, and none otherwise (a server running as a Home Assistant app, on the
Supervisor's private network, asks for none). A 401 from the server becomes the hand-back `REFUSED`; the glue
then tells the user that the secret is missing or wrong. The secret never goes into a log line, a hand-back's
detail or the repr of a `Request`.

Anything else in the reply is ignored, so the server may add keys freely. Anything that goes wrong (no server,
no reply within the timeout, an error status, a reply that is not the documented shape) becomes a `HandBack`:
the sentence then goes to Home Assistant's own agent as if the plug-in were not there.

The HTTP call itself is passed in (`transport`), so this module needs no network library and can be tested
against recorded replies.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from .table import ACTIONS, ACTIONS_1_1, KINDS

PATH = "/api/home"
NOT_FOR_ME = "not for me"
NO_KIND = "none"
# The server turned the request away (HTTP 401): the shared secret is missing or wrong.
REFUSED = "secret refused"

# Reasons a sentence is handed back that come from the server, not from the sentence. The glue logs these at
# warning level (one line each), and the rest at debug level.
SERVER_REASONS = frozenset({"server down", "server slow", "server error", "bad reply", REFUSED})

# A version number as the server may report the model's ("1.1") and as the plug-in has its own ("0.1.0"):
# digits and dots only, so it can never carry a word.
VERSION = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,4}){0,3}")
MAX_SECRET = 512


@dataclass(frozen=True)
class Answer:
    """Hunch's two answers for one sentence, and what the plug-in logs about them."""

    action: str
    kind: str
    declined: bool = False
    similarity: float | None = None
    label: str | None = None
    ms: float | None = None
    version: str | None = None


@dataclass(frozen=True)
class HandBack:
    """The sentence goes back to Home Assistant's own agent, and why."""

    reason: str
    detail: str = ""

    @property
    def from_server(self) -> bool:
        return self.reason in SERVER_REASONS


@dataclass(frozen=True)
class Request:
    url: str
    body: bytes
    # Left out of the repr: the headers may hold the shared secret.
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    method: str = "POST"  # the daily counts' deletion is the one DELETE (`share.deletion_for`)


Transport = Callable[[Request, float], Awaitable[tuple[int, bytes]]]


def clean_url(text: object) -> str | None:
    """The server's address as typed in the settings, tidied; None if it is not an http(s) address."""
    if not isinstance(text, str):
        return None
    url = text.strip().rstrip("/").removesuffix(PATH)
    if any(c.isspace() or ord(c) < 32 for c in url):
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment or "\\" in url
                or parts.port == 0):
            return None
    except ValueError:
        return None
    return urlunsplit(parts)


def clean_secret(text: object) -> str | None:
    """The shared secret as typed in the settings: "" for none, None when it cannot be sent.

    Spaces at either end are dropped. What is left must be letters, digits and symbols (printable ASCII with
    no space), at most `MAX_SECRET` characters, so it fits in an HTTP header as it stands."""
    if text is None:
        return ""
    if not isinstance(text, str):
        return None
    secret = text.strip()
    if len(secret) > MAX_SECRET or any(not "!" <= c <= "~" for c in secret):
        return None
    return secret


def request_for(base_url: str, sentence: str, secret: str = "") -> Request:
    """The request for one typed sentence. The server wants a JSON content type and no foreign Origin; it gets
    the shared secret as a bearer token when one is set, and no `Authorization` header when none is."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return Request(
        url=base_url.rstrip("/") + PATH,
        body=json.dumps({"text": sentence}, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def read_reply(status: int, body: bytes | str) -> Answer | HandBack:
    """The answer in a reply from `POST /api/home`, or why the sentence must be handed back."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        data = None
    if status == 401:
        return HandBack(REFUSED, "HTTP 401")
    if status != 200:
        return HandBack("server error", f"HTTP {status}")
    if not isinstance(data, dict):
        return HandBack("bad reply", "the reply is not a JSON object")
    action, kind = data.get("action"), data.get("kind")
    if not isinstance(action, str) or not isinstance(kind, str) or not action.strip():
        return HandBack("bad reply", "the reply lacks `action` or `kind`")
    declined = data.get("declined")
    if not isinstance(declined, bool):
        return HandBack("bad reply", "the reply lacks a boolean `declined`")
    if ((action, kind) != (NOT_FOR_ME, NO_KIND)
            and (action not in ACTIONS + ACTIONS_1_1 or kind not in KINDS)):
        return HandBack("bad reply", "the reply uses an unknown action or kind")
    label, version = data.get("label"), data.get("version")
    return Answer(
        action=action.strip(),
        kind=kind.strip(),
        declined=declined,
        similarity=_number(data.get("similarity")),
        label=label if isinstance(label, str) else None,
        ms=_number(data.get("ms")),
        version=version if isinstance(version, str) and VERSION.fullmatch(version) else None,
    )


async def ask(transport: Transport, base_url: str, sentence: str, timeout: float,
              secret: str = "") -> Answer | HandBack:
    """Send one sentence and read the reply; a server that is down or slower than `timeout` seconds hands back,
    and so does one that turns the shared secret away (`REFUSED`)."""
    request = request_for(base_url, sentence, secret)
    try:
        status, body = await asyncio.wait_for(transport(request, timeout), timeout)
    except TimeoutError:
        return HandBack("server slow", f"no reply within {timeout:g} s")
    except Exception as error:  # noqa: BLE001 - every failure to reach the server hands the sentence back
        return HandBack("server down", type(error).__name__)
    return read_reply(status, body)
