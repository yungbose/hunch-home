"""Hunch's answer, and a hand-back, as the rules use them; and reading an answer dict.

Copied from the plug-in's `hunch_logic/client.py` (0.1.1). The app runs the model in its own process
(`hunch_app/model.py`), so the request to a separate server and its shared secret are gone. What stays:

- `Answer` and `HandBack`, which every other module of the rules uses;
- `read_reply`, which checks an answer dict (the runtime's answer, as the server used to return it) and gives
  an `Answer` or the `HandBack` "bad reply"; the app passes the model's dict through it unchanged;
- `Request`, `Transport` and `clean_url`, which the daily counts (`share.py`) use to send and delete.

The reason words (`SERVER_REASONS`, `REFUSED`) are kept as they were, because the daily counts' fixed words
include them (`summary.KNOWN_REASONS`) and the receiving end knows them. In the app "server slow" means Hunch's
own time ran out and "server error" means the model failed or is not loaded.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from .table import ACTIONS, ACTIONS_1_1, KINDS

# Kept so an address typed with the old endpoint's path is still tidied the same way.
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


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def read_reply(status: int, body: bytes | str) -> Answer | HandBack:
    """The answer in a reply (status and JSON body), or why the sentence must be handed back."""
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
