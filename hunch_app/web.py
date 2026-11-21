"""The page's server: the owner's interface to Hunch Home, behind Home Assistant's Ingress.

It serves three things, all on relative addresses because Home Assistant chooses the path the page sits under:
the static page (`page/`), a small JSON interface (`api/...`) and a live stream of events (`api/events`, as
server-sent events).

**It has no login of its own.** As an app, Home Assistant has already signed the person in, and the server
answers only Home Assistant's Ingress address. Outside Home Assistant (Docker, the demo) it must never be
published to a network. Every request passes these checks, in this order (`guard`):

1. Its `Host` must be on the list of names this page is known by (`page_listening`): `localhost`, `127.0.0.1`
   and `[::1]` with any port, the page's own address when that is a literal address, and the names the owner
   lists in `HUNCH_PAGE_HOSTS`. This stops a hostile site whose name is made to point at this machine (DNS
   rebinding), which would otherwise send a matching `Origin` and `Host`. Behind Ingress the accepted names are
   `HUNCH_INGRESS_HOSTS`, any by default, since only the Ingress proxy can connect at all.
2. Its peer address must be on the allow-list, when there is one (as an app: the Ingress proxy only).
3. An `Origin` from another site is refused.
4. A request that changes something (every POST and DELETE) must carry `X-Hunch-Page: 1` and a JSON content
   type, which a plain cross-site form cannot send.

Each refusal is a 403 with no detail.

The server knows nothing about Home Assistant, the model or the record. It is given a backend when it starts
(`make_app(backend)`) and calls the plain async methods of `Backend` below, one for each address of the page's
interface as pinned in the app's build plan (20 Nov 2026). `StandIn` is a believable backend for
development and tests: a small home, a past record and a live trickle of turns.

    uv run python -m hunch_app.web --stand-in                # http://127.0.0.1:8099/
    uv run python -m hunch_app.web --stand-in --first-run    # nothing set up yet

(run from the app's folder, the one that holds `hunch_app/`, or with that folder on `PYTHONPATH`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import itertools
import json
import logging
import random
import re
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from aiohttp import web

from . import hold
from .api import PRIVACY_URL

LOG = logging.getLogger("hunch_app.web")

PAGE_DIR = Path(__file__).resolve().parents[1] / "page"
# The Supervisor's Ingress proxy: as an app, the only address the page server answers.
INGRESS_REMOTE = "172.30.32.2"
DEFAULT_PORT = 8099
KEEPALIVE_S = 15.0
MAX_SENTENCE = 300          # as the app has it, for a tried or a taught sentence
MAX_RECORD = 1000


# ---------------------------------------------------------------------------------------------------------------
# The backend the page server calls: one method per address of the pinned interface.
# ---------------------------------------------------------------------------------------------------------------


@runtime_checkable
class Backend(Protocol):
    """What the page server needs from the rest of the app. Every method is a plain coroutine (`events` is an
    async iterator). Raise `ValueError` for a request the backend refuses (the page shows the message),
    `LookupError` for something that does not exist and `NotImplementedError` for a part not built yet (each
    message in plain words, shown as it is); anything else is reported as a fault without its detail.

    Shapes (JSON-ready dicts; the page reads only these fields):

    - `state()`: `version`, `model_version`, `model_ready`, `model_problem` (a plain sentence when the model
      could not be loaded, else None); `home` = {`kinds`: [{`kind` (a Home Assistant domain such as
      "light"), `device_class`? (for a cover or a media player, when it has one), `count`}], `rooms`: [{`name`,
      `count`}], `devices`, `fix`: [{`type`: "lock_named_like_room" | "no_room" | "same_name", `names`: [...],
      `count`? (for "no_room": 1, or how many devices one grouped entry stands for, when three or more have no
      room; its `names` are then all of theirs), `room`?, `rooms`?: [...]}]}; `settings` (as `update_settings`);
      `assistants`: [{`id`, `name`, `preferred`, `language`, `uses_hunch`, `agent`: {`id`, `name`},
      `previous_agent`: {`id`, `name`} | None, `answer_first`, `stt`: {`name`, `fixed_phrases`} | None,
      `can_set_up`, `setup_note` (why not, in plain words, or "")}]; `agents`: [{`id`, `name`}] (those Hunch
      can pass on to); `hunch_agent`: {`id`, `name`} | None (None until the owner has pressed Submit on
      "Discovered"); `teach`: {`actions`, `kinds`, `can_decline` (whether a
      sentence can be taught as "not a home command"), `notice` (one plain sentence for the owner, or None)}; `privacy_url`;
      `pass_to_chosen`: whether the owner chose "Pass on to" themselves (the set-up then leaves it as it is);
      `notices`: plain sentences about files of the app's own that could not be read at start and were kept
      aside (settings, record, the assistants it set up), or []; `sidebar`: whether the app's sidebar entry is on (None where there is no Supervisor, as in Docker);
      `earlier_plugin`: whether the earlier Hunch Home plug-in is still installed (an entity of its integration
      is in the registry), so the page can say once that it can be removed;
      `settings_saved`: false while the settings file is damaged and left where it is, so a changed setting is
      used until the app restarts but not saved (the page says so instead of "Saved");
      `listening`: {`available`, `microphone`: the id of the input chosen in the app's Audio setting (or the
      machine's default input) | None, `microphone_name`: its description | None, `chosen`, `status`: "off" |
      "needs model" | "fetching" | "starting" | "on" | "could not start", `problem` (plain words, or ""),
      `model`: {`ready`, `name`, `maker`, `source`, `download_mb`, `disk_mb`, `progress` | None}, `memory`:
      {`free_mb`, `needed_mb`, `warning`}} (the whole shape is in `api.py`'s docstring).
    - `events()`: dicts with `event` naming the kind: "turn" (`id`, `time` ISO 8601, `source` "assist" |
      "listening", `sentence`, `outcome` "done" | "passed" | "ignored" | "error" | "nothing" ("Hunch did
      nothing": a home command of a kind Hunch carries out that it did not carry out, answered by Hunch in one
      short line and passed to no agent: `hold.py`), `label`,
      `action`, `kind`,
      `score`, `entities` as names, `ms`, `reply` (what Hunch said), and `note`: a plain-words note, such as
      that an agent may still act, or for "nothing" the same short line as `reply`), and `short`: for
      "nothing", that line as a headline, without the "Sorry, " ("Couldn't work out which device you meant";
      `hold.SHORT`), else ""),
      "hearing", "quiet" (`speech` false when it was only a noise), "home", and "state" (no other field:
      something in `state()` changed that the page did not ask for, such as the model becoming ready or
      failing, Home Assistant lost or reached, or listening's status or a fetch's progress; the page reads
      `state()` again). A turn whose outcome is "ignored" has no `sentence` field at all.
    - `record(limit)`: past turns as in the "turn" event, newest first, each also with `undone` ("yes" when the
      owner or something else put it back within 30 seconds, as `logic/record.py` has it) and `undone_by` (the
      rules' words for what put it back, or "").
    - `try_sentence(text, room)`: {`outcome`, `label`, `action`, `kind`, `score`, `entities`, `ms`, `note`}:
      what would happen; nothing is carried out. `outcome` "nothing" when Hunch would do nothing (`hold.py`);
      `note` is then Hunch's own reply and `short` its headline, as the real turn's say, else "". `why`: one
      plain sentence for why it would do nothing ("Hunch would do nothing: it couldn't work out which device you
      meant."; `hold.WHY`) or pass it on (`why.py`), with no score, or "" when it would act.
    - `update_settings(changes)`: applies a partial dict of `SETTINGS` keys and returns all the settings.
    - `setup_assistant(pipeline_id, sidebar)`, `undo_assistant(pipeline_id)`: the assistant as in `state()`.
      With `sidebar` true the app also switches on its own sidebar entry.
    - `teach(sentence, action, kind)`: the taught example {`id`, `sentence`, `action`, `kind`, `time`, `active`}
      (`active` false when the model in use cannot take it; ids have no fixed shape). "Not a home command" is
      sent as action "not for me", kind "none".
    - `taught()`: the taught examples, newest first. `forget(taught_id)`: removes one. `clear_record()`.
    - `data()`: the page's "Your data" section (`data.py`). `kept`: {`on`, `turns` (how many the record holds),
      `most` (1,000), `days` (30)}; `sharing`: {`offered`, `chosen`, `day` (yesterday, UTC, "2026-11-19"),
      `counts` (the daily counts for that day as they would be sent, with the install number replaced by a
      plain "not shown" string), `message` (the same as the exact JSON text that would be sent, but for that one
      string), `sends` (false when the day has no sentences, so nothing would be sent), `last_sent_day` | None,
      `deletion`: {`status`: "none" | "asking" | "owed" | "done", `tried` (ISO 8601, the last failed try, while
      owed) | None, `done` (ISO 8601) | None}}; `install`: {`reference` (16 hexadecimal digits made from the
      install number, which is never shown), `set_aside` (a damaged record was set aside and a new number made),
      `earlier`: [{`file` (a set-aside record's name), `reference` (its install number's reference, when it can
      still be read there) | None}], newest first}.
    - `download(kind)`: "full" (the whole record, sentences and names included, for the owner) or "report" (made
      for a bug report: the newest 50 turns with no sentence, no name of a device, room or agent and each entity
      id cut to its kind; the versions; the settings as on or off; listening's state). Neither holds the install
      number. Served as `GET api/record/download?kind=`, a JSON file to save; only to the page itself, which
      sends `X-Hunch-Page: 1` with it.
    """

    async def state(self) -> dict[str, Any]: ...

    def events(self) -> AsyncIterator[dict[str, Any]]: ...

    async def record(self, limit: int) -> list[dict[str, Any]]: ...

    async def try_sentence(self, text: str, room: str | None) -> dict[str, Any]: ...

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]: ...

    async def setup_assistant(self, pipeline_id: str, sidebar: bool) -> dict[str, Any]: ...

    async def undo_assistant(self, pipeline_id: str) -> dict[str, Any]: ...

    async def teach(self, sentence: str, action: str, kind: str) -> dict[str, Any]: ...

    async def taught(self) -> list[dict[str, Any]]: ...

    async def forget(self, taught_id: str) -> None: ...

    async def clear_record(self) -> None: ...

    async def data(self) -> dict[str, Any]: ...

    async def download(self, kind: str) -> dict[str, Any]: ...


# The settings the page may change, and the type each takes (None allowed where listed).
SETTINGS: dict[str, tuple[type, ...]] = {
    "locks_and_doors": (bool,),
    "pass_to": (str,),
    "keep_record": (bool,),
    "share_counts": (bool,),
    "listening": (bool,),
    "mic_room": (str, type(None)),
}


# ---------------------------------------------------------------------------------------------------------------
# Checks on what the page sends
# ---------------------------------------------------------------------------------------------------------------


class Refused(Exception):
    """A request the server turns away before the backend sees it."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


async def _body(request: web.Request) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > 16_384:
        raise Refused(413, "That request is too large.")
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as err:
        raise Refused(400, "Hunch could not read that request.") from err
    if not isinstance(data, dict):
        raise Refused(400, "Hunch could not read that request.")
    return data


# What the page sends, as the owner would call it, for messages.
WORDS = {"text": "a sentence", "room": "the room", "pipeline_id": "which assistant", "sentence": "the sentence",
         "action": "what Hunch should do", "kind": "what kind of thing it is", "sidebar": "the sidebar choice",
         "locks_and_doors": "Locks and doors", "pass_to": "Pass on to", "keep_record": "Keep a record",
         "share_counts": "Share daily counts", "listening": "Listen with no wake word",
         "mic_room": "Microphone's room"}


def _text(data: dict[str, Any], key: str, *, limit: int = MAX_SENTENCE, required: bool = True) -> str | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Refused(400, f"Hunch needs {WORDS.get(key, 'more')} to do that.")
    value = value.strip()
    if len(value) > limit:
        raise Refused(400, f"That is too long. Keep {WORDS.get(key, 'it')} to {limit} characters or fewer.")
    return value


def _settings_changes(data: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key, value in data.items():
        if key not in SETTINGS:
            raise Refused(400, "There is no such setting.")
        kinds = SETTINGS[key]
        # bool is an int, and an int is never a setting here, so the exact type is checked
        if type(value) not in kinds:
            raise Refused(400, f"That is not a value {WORDS[key]} can take.")
        if isinstance(value, str):
            value = value.strip()
            if not value or len(value) > 200:
                raise Refused(400, f"That is not a value {WORDS[key]} can take.")
        changes[key] = value
    if not changes:
        raise Refused(400, "There was nothing to change.")
    return changes


# ---------------------------------------------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------------------------------------------

BACKEND = web.AppKey("backend", Backend)
ALLOWED_REMOTES = web.AppKey("allowed_remotes", frozenset)
ALLOWED_HOSTS = web.AppKey("allowed_hosts", object)   # frozenset of names, or None for any
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
PAGE_HEADER = "X-Hunch-Page"
PAGE = web.AppKey("page_dir", Path)
STREAMS = web.AppKey("streams", set)


def _same_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False  # "null", a file, an extension
    hosts = {request.headers.get("Host", "")}
    hosts.update(h.strip() for h in request.headers.get("X-Forwarded-Host", "").split(",") if h.strip())
    netloc = parts.netloc.lower()
    for host in hosts:
        host = host.lower()
        if not host:
            continue
        if host == netloc:
            return True
        # a default port may be written or left out on either side
        default = {"http": "80", "https": "443"}[parts.scheme]
        if host.removesuffix(":" + default) == netloc.removesuffix(":" + default):
            return True
    return False


def host_name(header: str | None) -> str | None:
    """The name in a `Host` header without its port, lower case (`[::1]` keeps its brackets); None when there
    is none or it cannot be read."""
    if not header or not header.strip() or any(c in header for c in " /\\@"):
        return None
    header = header.strip().lower()
    if header.startswith("["):
        end = header.find("]")
        rest = header[end + 1:]
        if end < 0 or (rest and not re.fullmatch(r":\d{1,5}", rest)):
            return None
        return header[:end + 1]
    name, _, port = header.partition(":")
    if port and not port.isdigit():
        return None
    return name or None


# The server's own answers when the backend cannot help, in the words the page shows as they are.
NOT_BUILT = "That part of Hunch is not built yet."
NO_HOME_ASSISTANT = "Hunch cannot reach Home Assistant just now. It keeps trying. Try again in a minute."
FAULT = "Something went wrong inside Hunch. Try again."


def _refused() -> web.HTTPForbidden:
    return web.HTTPForbidden(text="")


@web.middleware
async def guard(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
    hosts = request.app[ALLOWED_HOSTS]
    if hosts is not None and host_name(request.headers.get("Host")) not in hosts:
        LOG.warning("refused a request for a name this page is not known by")
        raise _refused()
    allowed = request.app[ALLOWED_REMOTES]
    if allowed and request.remote not in allowed:
        LOG.warning("refused a request from %s", request.remote)
        raise _refused()
    if not _same_origin(request):
        LOG.warning("refused a request with a foreign origin")
        raise _refused()
    if request.method not in ("GET", "HEAD") and (
            request.headers.get(PAGE_HEADER) != "1" or request.content_type != "application/json"):
        LOG.warning("refused a change that did not come from the page")
        raise _refused()
    try:
        response = await handler(request)
    except Refused as err:
        response = web.json_response({"error": err.message}, status=err.status)
    except ValueError as err:
        response = web.json_response({"error": str(err) or "That was refused."}, status=400)
    except LookupError as err:
        response = web.json_response({"error": str(err).strip("'\"") or "Not found."}, status=404)
    except NotImplementedError as err:
        response = web.json_response({"error": str(err) or NOT_BUILT}, status=501)
    except web.HTTPException:
        raise
    except (ConnectionError, TimeoutError):
        # the backend could not reach Home Assistant (the app's `Unavailable` is a ConnectionError): a calm answer
        LOG.warning("the backend could not reach Home Assistant on %s %s", request.method, request.path)
        response = web.json_response({"error": NO_HOME_ASSISTANT}, status=503)
    except Exception:  # the page gets a plain answer and the log keeps the detail
        LOG.exception("the backend failed on %s %s", request.method, request.path)
        response = web.json_response({"error": FAULT}, status=500)
    if not response.prepared:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
            "form-action 'self'; object-src 'none'",
        )
    return response


# ---------------------------------------------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------------------------------------------


def _json(data: Any, status: int = 200) -> web.Response:
    response = web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))
    response.headers["Cache-Control"] = "no-store"
    return response


async def get_state(request: web.Request) -> web.Response:
    return _json(await request.app[BACKEND].state())


async def get_record(request: web.Request) -> web.Response:
    raw = request.query.get("limit", "50")
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_RECORD:
        raise Refused(400, f"The limit is a whole number from 1 to {MAX_RECORD}.")
    return _json(await request.app[BACKEND].record(int(raw)))


async def post_try(request: web.Request) -> web.Response:
    data = await _body(request)
    text = _text(data, "text")
    room = _text(data, "room", limit=200, required=False)
    return _json(await request.app[BACKEND].try_sentence(text, room))


async def post_settings(request: web.Request) -> web.Response:
    changes = _settings_changes(await _body(request))
    return _json(await request.app[BACKEND].update_settings(changes))


async def post_setup(request: web.Request) -> web.Response:
    data = await _body(request)
    pipeline_id = _text(data, "pipeline_id", limit=200)
    sidebar = data.get("sidebar", False)
    if type(sidebar) is not bool:
        raise Refused(400, "That is not a value the sidebar choice can take.")
    return _json(await request.app[BACKEND].setup_assistant(pipeline_id, sidebar))


async def post_undo(request: web.Request) -> web.Response:
    pipeline_id = _text(await _body(request), "pipeline_id", limit=200)
    return _json(await request.app[BACKEND].undo_assistant(pipeline_id))


async def post_teach(request: web.Request) -> web.Response:
    data = await _body(request)
    sentence = _text(data, "sentence")
    action = _text(data, "action", limit=100)
    kind = _text(data, "kind", limit=100)
    return _json(await request.app[BACKEND].teach(sentence, action, kind))


async def get_taught(request: web.Request) -> web.Response:
    return _json(await request.app[BACKEND].taught())


async def delete_taught(request: web.Request) -> web.Response:
    taught_id = request.match_info["taught_id"]
    if not 0 < len(taught_id) <= 200:
        raise Refused(400, "Hunch could not tell which one you meant.")
    await request.app[BACKEND].forget(taught_id)
    return _json({"ok": True})


async def post_clear(request: web.Request) -> web.Response:
    await request.app[BACKEND].clear_record()
    return _json({"ok": True})


async def get_data(request: web.Request) -> web.Response:
    return _json(await request.app[BACKEND].data())


# The two downloads, and the word each file is named by.
DOWNLOADS = {"full": "record", "report": "report"}
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


async def get_download(request: web.Request) -> web.Response:
    """A file to save: the whole record, or the version made for a bug report. Only the page's own script asks
    for it (it sends `X-Hunch-Page: 1`, which a link from another site, or a page opened by itself, cannot), on
    top of the checks every request passes."""
    if request.headers.get(PAGE_HEADER) != "1":
        LOG.warning("refused a download that did not come from the page")
        raise _refused()
    kind = request.query.get("kind", "")
    if kind not in DOWNLOADS:
        raise Refused(400, "Hunch cannot make that download.")
    content = await request.app[BACKEND].download(kind)
    made = str(content.get("made") or "")[:10]
    day = made if _DAY.fullmatch(made) else _now().date().isoformat()
    body = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
    response = web.Response(body=body.encode("utf-8"), content_type="application/json", charset="utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="hunch-home-{DOWNLOADS[kind]}-{day}.json"'
    response.headers["Cache-Control"] = "no-store"
    return response


def _frame(event: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n").encode("utf-8")


async def get_events(request: web.Request) -> web.StreamResponse:
    """The live stream: one JSON object per message, its kind in `event`; a comment line keeps it open."""
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
    })
    # Listening starts before the first line is written, so a page that has read that line misses nothing. From
    # here on the subscription is always released, even if the response fails before the stream starts.
    source = request.app[BACKEND].events()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
    task: asyncio.Task[None] | None = None

    async def pump() -> None:
        try:
            async for event in source:
                if isinstance(event, dict) and isinstance(event.get("event"), str):
                    with contextlib.suppress(asyncio.QueueFull):  # a stalled reader loses events, never memory
                        queue.put_nowait(event)
        finally:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    try:
        await response.prepare(request)
        await response.write(b"retry: 2000\n\n")
        task = asyncio.create_task(pump())
        request.app[STREAMS].add(task)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
            except TimeoutError:
                await response.write(b": keepalive\n\n")
                continue
            if event is None:
                break
            await response.write(_frame(event))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            request.app[STREAMS].discard(task)
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()
    return response


# ---------------------------------------------------------------------------------------------------------------
# The page's files
# ---------------------------------------------------------------------------------------------------------------

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}
_SAFE_PATH = re.compile(r"[A-Za-z0-9_\-./]*")


async def get_file(request: web.Request) -> web.StreamResponse:
    name = request.match_info.get("name", "") or "index.html"
    if not _SAFE_PATH.fullmatch(name) or ".." in name.split("/") or name.startswith("/"):
        raise web.HTTPNotFound()
    root = request.app[PAGE]
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file() or path.suffix not in TYPES or path.name.startswith("."):
        raise web.HTTPNotFound()
    response = web.FileResponse(path, headers={"Content-Type": TYPES[path.suffix], "Cache-Control": "no-cache"})
    return response


# ---------------------------------------------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------------------------------------------


def make_app(backend: Backend, *, page_dir: Path = PAGE_DIR,
             allowed_remotes: frozenset[str] | set[str] | None = None,
             allowed_hosts: frozenset[str] | set[str] | None = LOOPBACK_HOSTS) -> web.Application:
    """The page server for `backend`. `allowed_hosts`: the names a request's `Host` may carry (None: any, only
    behind Ingress). `allowed_remotes`: the only client addresses answered (as an app, just the Ingress proxy,
    `ingress_remotes()`); None or empty answers any, for development on this machine."""
    app = web.Application(middlewares=[guard], client_max_size=64 * 1024)
    app[BACKEND] = backend
    app[ALLOWED_REMOTES] = frozenset(allowed_remotes or ())
    app[ALLOWED_HOSTS] = None if allowed_hosts is None else frozenset(h.lower() for h in allowed_hosts)
    app[PAGE] = page_dir.resolve()
    app[STREAMS] = set()

    async def close_streams(app: web.Application) -> None:
        for task in list(app[STREAMS]):
            task.cancel()

    app.on_shutdown.append(close_streams)
    app.add_routes([
        web.get("/api/state", get_state),
        web.get("/api/events", get_events),
        web.get("/api/record", get_record),
        web.post("/api/try", post_try),
        web.post("/api/settings", post_settings),
        web.post("/api/assistant/setup", post_setup),
        web.post("/api/assistant/undo", post_undo),
        web.post("/api/teach", post_teach),
        web.get("/api/taught", get_taught),
        web.delete("/api/taught/{taught_id}", delete_taught),
        web.post("/api/record/clear", post_clear),
        web.get("/api/data", get_data),
        web.get("/api/record/download", get_download),
        web.get("/{name:.*}", get_file),
    ])
    return app


def ingress_remotes() -> frozenset[str]:
    """As an app: answer Home Assistant's Ingress proxy and nothing else."""
    return frozenset({INGRESS_REMOTE})


def _loopback(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class PageWhere:
    """Where the page's server listens, whom it answers and the names it is known by."""

    host: str
    port: int
    ingress: bool
    allowed_remotes: frozenset[str] | None   # None: any (bound to this machine only)
    allowed_hosts: frozenset[str] | None     # None: any (behind Ingress only)


def _names(text: str | None) -> frozenset[str]:
    out = set()
    for part in (text or "").split(","):
        name = host_name(part.strip())
        if name:
            out.add(name)
    return frozenset(out)


def page_listening(env: dict[str, str] | Any, supervisor: bool) -> PageWhere:
    """Where the page's server listens and whom it answers. Raises `ValueError`, saying why, for a set-up that
    would publish the page with no list of who may open it.

    The page has no sign-in of its own. As an app it listens inside the container and answers only the Ingress
    proxy, behind Home Assistant's own sign-in; the `Host` names it accepts there are `HUNCH_INGRESS_HOSTS`, any
    by default (what Ingress sends is to be confirmed in the test machine). In Docker it listens on
    `HUNCH_PAGE_HOST`, `127.0.0.1` by default; set to anything other machines can reach, it needs
    `HUNCH_PAGE_ALLOW_FROM` (addresses, comma separated), or the app refuses to start. It is known by
    `localhost`, `127.0.0.1`, `[::1]`, its own address when that is a literal one, and `HUNCH_PAGE_HOSTS`."""
    port = int(env.get("HUNCH_PAGE_PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
    if supervisor:
        hosts = _names(env.get("HUNCH_INGRESS_HOSTS"))
        return PageWhere("0.0.0.0", port, True, ingress_remotes(), hosts or None)
    host = (env.get("HUNCH_PAGE_HOST") or "127.0.0.1").strip()
    allow = frozenset(part.strip() for part in (env.get("HUNCH_PAGE_ALLOW_FROM") or "").split(",") if part.strip())
    for address in allow:
        try:
            ipaddress.ip_address(address)
        except ValueError:
            raise ValueError(f"HUNCH_PAGE_ALLOW_FROM holds {address!r}, which is not an address") from None
    hosts = set(LOOPBACK_HOSTS) | _names(env.get("HUNCH_PAGE_HOSTS"))
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        if not literal.is_unspecified:
            hosts.add(f"[{literal}]" if literal.version == 6 else str(literal))
    except ValueError:
        pass
    if _loopback(host):
        return PageWhere(host, port, False, (allow | {"127.0.0.1", "::1"}) if allow else None, frozenset(hosts))
    if not allow:
        raise ValueError(f"HUNCH_PAGE_HOST is {host}, which other machines can reach, and HUNCH_PAGE_ALLOW_FROM is "
                         "not set. The page has no sign-in of its own: set HUNCH_PAGE_ALLOW_FROM to the addresses "
                         "that may open it, or listen on 127.0.0.1")
    return PageWhere(host, port, False, allow, frozenset(hosts))


async def serve(backend: Backend, where: PageWhere, *, page_dir: Path = PAGE_DIR) -> tuple[web.AppRunner, int]:
    """Start the page server; returns its runner (`await runner.cleanup()` stops it) and the port it took."""
    app = make_app(backend, page_dir=page_dir, allowed_remotes=where.allowed_remotes,
                   allowed_hosts=where.allowed_hosts)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, where.host, where.port)
    await site.start()
    taken = site._server.sockets[0].getsockname()[1] if site._server and site._server.sockets else where.port
    return runner, taken


# ---------------------------------------------------------------------------------------------------------------
# A believable stand-in, for development and tests
# ---------------------------------------------------------------------------------------------------------------

HA_AGENT = {"id": "conversation.home_assistant", "name": "Home Assistant"}
HUNCH_AGENT = {"id": "conversation.hunch_home", "name": "Hunch Home"}
OLLAMA = {"id": "conversation.ollama", "name": "Ollama"}
TEACH_ACTIONS = ("turn on", "turn off", "set level", "raise", "lower", "set colour", "set temperature", "lock",
                 "unlock", "open", "close", "next", "ask", "add", "remove", "previous", "announce")
DECLINED = "not for me"   # the model's own words for "not a home command": sent by the page, never shown
TEACH_KINDS = ("Lights", "Climate", "Security", "Speakers & TVs", "Water", "Scenes", "Timers and alarms",
               "Weather", "Lists", "Other device")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


# (sentence, outcome, action, kind, entities, score, ms): what the stand-in home hears over a day
SCRIPT: tuple[tuple[str, str, str, str, tuple[str, ...], float | None, int], ...] = (
    ("it's a bit dark in here", "done", "turn on", "Lights", ("Lamp",), 0.91, 38),
    ("kill the kitchen lights", "done", "turn off", "Lights", ("Kitchen spots", "Kitchen pendant"), 0.94, 41),
    ("what's the weather doing tomorrow", "passed", "ask", "Weather", (), 0.88, 36),
    ("put the kettle on", "done", "turn on", "Other device", ("Kettle",), 0.86, 44),
    ("make it cosy in the living room", "done", "set level", "Lights", ("Lamp", "Reading light"), 0.79, 52),
    ("remind me to call mum at six", "passed", "add", "Timers and alarms", (), 0.81, 40),
    ("bedside lamp off", "done", "turn off", "Lights", ("Bedside lamp",), 0.97, 33),
    ("who sang that song about the rain", "passed", "", "", (), 0.42, 35),
    ("close the blinds", "done", "close", "Other device", ("Living room blinds",), 0.93, 39),
    ("is the porch locked", "passed", "ask", "Security", (), 0.77, 37),
    ("lights a bit brighter please", "done", "raise", "Lights", ("Lamp",), 0.84, 46),
    ("turn on the lamp", "nothing", "turn on", "Lights", (), 0.9, 35),   # more than one lamp fits
    ("turn the fan on", "error", "turn on", "Other device", (), 0.71, 2010),
)

LISTENING_SCRIPT = (
    ("kitchen lights on", "done", "turn on", "Lights", ("Kitchen spots", "Kitchen pendant"), 0.95, 41),
    ("did you see the match last night", "ignored", "", "", (), 0.31, 30),
    ("turn the reading light off", "done", "turn off", "Lights", ("Reading light",), 0.92, 37),
)


class StandIn:
    """A small home with a past record and a live trickle of turns. Not Home Assistant, not the model: a few
    keyword rules stand in for Hunch so the page can be built and seen."""

    def __init__(self, *, first_run: bool = False, trickle_s: float = 11.0, seed: int | None = None):
        self.version = "0.2.0"
        self.model_version = "Hunch-1.1 Home"
        self.trickle_s = trickle_s
        self.random = random.Random(seed)
        self.rooms = ["Kitchen", "Living room", "Bedroom", "Porch"]
        # (name, domain, room)
        self.devices = [
            ("Kitchen spots", "light", "Kitchen"), ("Kitchen pendant", "light", "Kitchen"),
            ("Lamp", "light", "Living room"), ("Reading light", "light", "Living room"),
            ("Lamp", "light", "Bedroom"), ("Kettle", "switch", "Kitchen"), ("Desk fan", "switch", None),
            ("Porch", "lock", "Porch"), ("Living room blinds", "cover", "Living room"),
        ]
        self.settings: dict[str, Any] = {"locks_and_doors": True, "pass_to": HA_AGENT["id"], "keep_record": True,
                                         "share_counts": False, "listening": False, "mic_room": None}
        self.assistants: list[dict[str, Any]] = [
            {"id": "01jhome", "name": "Home", "preferred": True, "language": "en", "uses_hunch": False,
             "agent": dict(HA_AGENT), "previous_agent": None, "answer_first": True,
             "stt": {"name": "Speech-to-Phrase", "fixed_phrases": True}, "can_set_up": True, "setup_note": ""},
            {"id": "01jphone", "name": "Phone", "preferred": False, "language": "en", "uses_hunch": False,
             "agent": dict(OLLAMA), "previous_agent": None, "answer_first": False, "stt": None,
             "can_set_up": True, "setup_note": ""},
            {"id": "01jmaison", "name": "Maison", "preferred": False, "language": "fr", "uses_hunch": False,
             "agent": dict(HA_AGENT), "previous_agent": None, "answer_first": False, "stt": None,
             "can_set_up": False, "setup_note": "Hunch understands English only, so it can be added only to an "
                                                "assistant whose language is English."},
        ]
        self.sidebar = False
        # The daily counts as the page's "Your data" shows them: nothing sent yet, no deletion asked for.
        self.install_reference = "3f9c2a7be41d4c0a"
        self.last_sent_day: str | None = None
        self.deletion: dict[str, Any] = {"status": "none", "tried": None, "done": None}
        self.delete_s = 0.9   # how long the pretend deletion takes (a test sets 0)
        # Listening as the app reports it (`listen.Listening.status`): the speech model is "fetched" (nothing is
        # downloaded) the first time it is switched on, with progress, then it starts.
        self._listen = {"status": "needs model", "ready": False, "progress": None}
        self._listen_task: asyncio.Task[None] | None = None
        self.listen_step = 0.12   # seconds between the pretend fetch's steps (a test sets 0)
        self._saved: dict[str, dict[str, Any]] = {}
        if not first_run:
            self._setup("01jhome")
        self._ids = itertools.count(1)
        self._record: deque[dict[str, Any]] = deque(maxlen=MAX_RECORD)
        self._taught: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        start = _now() - timedelta(hours=20)
        if not first_run:
            for i, line in enumerate(SCRIPT[:-1]):
                self._remember(self._turn(line, start + timedelta(minutes=97 * i)))
        self._taught.append({"id": "k1", "sentence": "it's a bit dark in here", "action": "turn on",
                             "kind": "Lights", "time": _iso(start - timedelta(days=2)), "active": True})
        if not first_run:
            self._taught.append({"id": "9f2c41d07a6be835", "sentence": "play the radio in the kitchen",
                                 "action": "play", "kind": "Speakers & TVs",
                                 "time": _iso(start - timedelta(days=5)), "active": False})

    # -- helpers --
    def _agent_name(self, agent_id: str | None = None) -> str:
        agent_id = agent_id or self.settings["pass_to"]
        for agent in (HA_AGENT, OLLAMA):
            if agent["id"] == agent_id:
                return agent["name"]
        return "your assistant"

    def _label(self, outcome: str) -> str:
        return {"done": "Hunch did it", "passed": f"Passed to {self._agent_name()}", "ignored": "Ignored",
                "error": f"Could not reach {self._agent_name()}", "nothing": "Hunch did nothing"}[outcome]

    def _turn(self, line: tuple, when: datetime | None = None, source: str = "assist") -> dict[str, Any]:
        sentence, outcome, action, kind, entities, score, ms = line
        note = hold.REPLIES[hold.DEVICE] if outcome == "nothing" else ""
        if outcome == "error":
            note = (f"Hunch stopped waiting for {self._agent_name()} after 30 seconds. {self._agent_name()} may "
                    "still act on it.")
        turn = {"event": "turn", "id": f"t{next(self._ids)}", "time": _iso(when or _now()), "source": source,
                "sentence": sentence, "outcome": outcome, "label": self._label(outcome), "action": action,
                "kind": kind, "score": score, "entities": list(entities), "ms": ms, "note": note,
                "reply": note if outcome == "nothing" else "", "short": hold.short(outcome, note)}
        if outcome == "ignored":   # nothing it heard reaches the page
            turn = {k: v for k, v in turn.items() if k not in {"sentence", "action", "kind", "score", "note"}}
            turn["entities"] = []
        return turn

    def _remember(self, turn: dict[str, Any]) -> None:
        if self.settings["keep_record"] and turn["outcome"] != "ignored":
            self._record.appendleft({k: v for k, v in turn.items() if k != "event"})

    def _publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def _trickle(self) -> None:
        i = 0
        while True:
            await asyncio.sleep(self.trickle_s * (0.7 + 0.6 * self.random.random()))
            if not self._subscribers:
                continue
            if self.settings["listening"]:
                line = LISTENING_SCRIPT[i % len(LISTENING_SCRIPT)]
                self._publish({"event": "hearing"})
                await asyncio.sleep(1.6)
                self._publish({"event": "quiet"})
                await asyncio.sleep(0.5)
                turn = self._turn(line, source="listening")
            else:
                if not any(a["uses_hunch"] for a in self.assistants):
                    continue
                turn = self._turn(SCRIPT[i % len(SCRIPT)])
            i += 1
            self._remember(turn)
            self._publish(turn)

    def _setup(self, pipeline_id: str, sidebar: bool = False) -> dict[str, Any]:
        assistant = self._assistant(pipeline_id)
        if assistant["uses_hunch"]:
            raise ValueError("This assistant already uses Hunch.")
        if not assistant["can_set_up"]:
            raise ValueError(assistant["setup_note"])
        self.sidebar = self.sidebar or sidebar
        self._saved[pipeline_id] = {"agent": dict(assistant["agent"]), "answer_first": assistant["answer_first"]}
        assistant["previous_agent"] = dict(assistant["agent"])
        assistant["agent"] = dict(HUNCH_AGENT)
        assistant["answer_first"] = True
        assistant["uses_hunch"] = True
        assistant["can_set_up"] = False
        self.settings["pass_to"] = assistant["previous_agent"]["id"]
        return assistant

    def _assistant(self, pipeline_id: str) -> dict[str, Any]:
        for assistant in self.assistants:
            if assistant["id"] == pipeline_id:
                return assistant
        raise LookupError("There is no such assistant.")

    def _home(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        rooms: dict[str, int] = {}
        for _name, domain, room in self.devices:
            kinds[domain] = kinds.get(domain, 0) + 1
            if room:
                rooms[room] = rooms.get(room, 0) + 1
        names: dict[str, list[str]] = {}
        for name, _domain, room in self.devices:
            names.setdefault(name, []).append(room or "")
        fix: list[dict[str, Any]] = []
        for name, domain, room in self.devices:
            if domain in ("lock", "cover") and room and name.casefold() == room.casefold():
                fix.append({"type": "lock_named_like_room", "names": [name], "room": room})
        fix += [{"type": "no_room", "names": [name]} for name, _d, room in self.devices if not room]
        fix += [{"type": "same_name", "names": [name], "rooms": [r for r in where if r]}
                for name, where in names.items() if len(where) > 1]
        return {"kinds": [{"kind": k, "count": n} for k, n in kinds.items()],
                "rooms": [{"name": r, "count": rooms.get(r, 0)} for r in self.rooms], "devices": len(self.devices),
                "fix": fix}

    # -- the Backend methods --
    async def state(self) -> dict[str, Any]:
        mic = "USB audio device"
        model = {"ready": self._listen["ready"], "name": "Parakeet TDT 0.6B v2", "maker": "NVIDIA",
                 "licence": "CC BY 4.0", "source": "GitHub (the sherpa-onnx project's releases)",
                 "download_mb": 482, "disk_mb": 661, "needs_disk_mb": 1194, "progress": self._listen["progress"]}
        return {"version": self.version, "model_version": self.model_version, "home": self._home(),
                "settings": dict(self.settings), "assistants": [dict(a) for a in self.assistants],
                "agents": [dict(HA_AGENT), dict(OLLAMA)], "hunch_agent": dict(HUNCH_AGENT),
                "teach": {"actions": list(TEACH_ACTIONS), "kinds": list(TEACH_KINDS), "can_decline": True,
                          "notice": None},
                "privacy_url": PRIVACY_URL, "sidebar": self.sidebar, "earlier_plugin": False,
                "settings_saved": True,
                "listening": {"available": True, "microphone": "alsa_input.usb-stand-in", "microphone_name": mic,
                              "chosen": True, "status": self._listen["status"], "problem": "", "model": model,
                              "memory": {"free_mb": 2400, "needed_mb": 900, "warning": ""}}}

    async def _switch_on(self) -> None:
        """Pretend to fetch the speech model (about five seconds, nothing downloaded), then start."""
        if not self._listen["ready"]:
            self._listen["status"] = "fetching"
            for stage, total in (("fetching", 482), ("unpacking", 661)):
                for step in range(21):
                    self._listen["progress"] = {"stage": stage, "done_mb": total * step // 20, "total_mb": total,
                                                "fraction": step / 20}
                    self._publish({"event": "state"})
                    await asyncio.sleep(self.listen_step)
            self._listen.update(ready=True, progress=None)
        self._listen["status"] = "starting"
        self._publish({"event": "state"})
        await asyncio.sleep(self.listen_step * 8)
        self.settings["listening"] = True
        self._listen["status"] = "on"
        self._publish({"event": "state"})

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._trickle())
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def record(self, limit: int) -> list[dict[str, Any]]:
        return list(itertools.islice(self._record, limit))

    async def try_sentence(self, text: str, room: str | None) -> dict[str, Any]:
        await asyncio.sleep(0.35)
        low = text.casefold()
        for example in self._taught:
            if example["sentence"].casefold() == low and example.get("active", True):
                if example["action"] == DECLINED:   # taught as not a home command: passed on
                    return self._try_answer("", "", low, room, 0.99)
                return self._try_answer(example["action"], example["kind"], low, room, 0.99)
        action = next((a for w, a in (("unlock", "unlock"), ("lock", "lock"), ("off", "turn off"),
                                      ("on", "turn on"), ("dark", "turn on"), ("bright", "raise"),
                                      ("dim", "lower"), ("open", "open"), ("close", "close"))
                       if re.search(rf"\b{w}\b", low)), "")
        kind = ("Lights" if re.search(r"\b(light|lights|lamp|lamps|dark|bright|dim)\b", low) else
                "Security" if re.search(r"\b(lock|unlock|door)\b", low) else
                "Other device" if re.search(r"\b(kettle|fan|blind|blinds)\b", low) else "")
        return self._try_answer(action, kind, low, room, 0.9 if action and kind else 0.38)

    def _try_answer(self, action: str, kind: str, low: str, room: str | None, score: float) -> dict[str, Any]:
        entities: list[str] = []
        if action and kind:
            for name, domain, where in self.devices:
                if name.casefold() in low or (where and where.casefold() in low and kind == "Lights"
                                              and domain == "light"):
                    entities.append(name if not where or where.casefold() in name.casefold() else
                                    f"{name} ({where})")
            if not entities and kind == "Lights":
                where = room or "Living room"
                entities = [n for n, d, r in self.devices if d == "light" and r == where]
        kept = hold.DEVICE   # a home command with no device found: Hunch says so, as the app does (`hold.py`)
        if kind == "Security" and action in ("unlock", "open") and not self.settings["locks_and_doors"]:
            entities, kept = [], hold.LOCKS
        outcome = "done" if entities else "nothing" if action and kind else "passed"
        tried = {"outcome": outcome, "label": self._label(outcome), "action": action, "kind": kind,
                 "score": round(score, 2), "entities": entities, "ms": self.random.randint(30, 60),
                 "note": "", "short": "", "why": ""}
        if outcome == "nothing":
            tried.update(note=hold.REPLIES[kept], short=hold.SHORT[kept], why=hold.WHY[kept])
        elif outcome == "passed":
            tried["why"] = "Hunch did not take it for a home command."
        return tried

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        if "pass_to" in changes and changes["pass_to"] not in (HA_AGENT["id"], OLLAMA["id"]):
            raise ValueError("That agent is not in this home.")
        if changes.get("mic_room") not in (None, *self.rooms):
            raise ValueError("That room is not in this home.")
        listening = changes.pop("listening", None)
        withdrawn = changes.get("share_counts") is False and self.settings["share_counts"] is True
        self.settings.update(changes)
        if withdrawn:   # as the app does: the deletion is asked for at once, and the answer waits for it
            self.deletion = {"status": "asking", "tried": None, "done": None}
            await asyncio.sleep(self.delete_s)
            self.deletion = {"status": "done", "tried": None, "done": _iso(_now())}
        if listening is True and self._listen["status"] not in ("fetching", "starting", "on"):
            self._listen_task = asyncio.create_task(self._switch_on())
        elif listening is False:
            if self._listen_task is not None:
                self._listen_task.cancel()
            self.settings["listening"] = False
            self._listen.update(status="off" if self._listen["ready"] else "needs model", progress=None)
            self._publish({"event": "quiet", "speech": False})
            self._publish({"event": "state"})
        return dict(self.settings)

    async def setup_assistant(self, pipeline_id: str, sidebar: bool = False) -> dict[str, Any]:
        await asyncio.sleep(0.4)
        return dict(self._setup(pipeline_id, sidebar))

    async def undo_assistant(self, pipeline_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        assistant = self._assistant(pipeline_id)
        saved = self._saved.pop(pipeline_id, None)
        if saved is not None:
            assistant.update(agent=saved["agent"], answer_first=saved["answer_first"], uses_hunch=False,
                             previous_agent=None, can_set_up=True)
        return dict(assistant)

    async def teach(self, sentence: str, action: str, kind: str) -> dict[str, Any]:
        declined = action == DECLINED and kind == "none"
        if not declined and (action not in TEACH_ACTIONS or kind not in TEACH_KINDS):
            raise ValueError("Hunch cannot be taught that. Choose from the lists.")
        example = {"id": f"{self.random.getrandbits(64):016x}", "sentence": sentence, "action": action,
                   "kind": kind, "time": _iso(_now()), "active": True}
        self._taught.insert(0, example)
        return example

    async def taught(self) -> list[dict[str, Any]]:
        return [dict(t) for t in self._taught]

    async def forget(self, taught_id: str) -> None:
        for i, example in enumerate(self._taught):
            if example["id"] == taught_id:
                del self._taught[i]
                return
        raise LookupError("That example is already gone.")

    async def clear_record(self) -> None:
        self._record.clear()

    def _counts(self, day: str) -> dict[str, Any]:
        """Yesterday's counts made from the stand-in's record, in the shape the app sends (`logic/summary.py`)."""
        turns = [t for t in self._record if t["time"][:10] == day and t.get("source") != "listening"]
        outcome = {"done": "carried out", "passed": "handed back", "error": "error"}
        answered = [t for t in turns if t.get("action")]

        def tally(words: list[str]) -> dict[str, int]:
            out: dict[str, int] = {}
            for word in words:
                out[word] = out.get(word, 0) + 1
            return out

        def band(score: float | None) -> str:
            if score is None:
                return "none"
            if score < 0.5:
                return "below 0.5"
            low = min(9, int(score * 10))
            return "0.9 and above" if low >= 9 else f"0.{low} to 0.{low + 1}"

        return {"schema": 1, "day": day, "install_id": "not shown: your install number",
                "plugin_version": self.version, "model_versions": {"1.1": len(answered)} if answered else {},
                "sentences": len(turns),
                "by_action_and_kind": dict(sorted(tally([f"{t['action']} / {t['kind']}" for t in answered]).items())),
                "by_outcome": tally([outcome.get(t["outcome"], "other") for t in turns]),
                "handed_back_by_reason": tally(["no match" if t.get("action") else "not for me"
                                                for t in turns if t["outcome"] == "passed"]),
                "errors": sum(1 for t in turns if t["outcome"] == "error"),
                "undone": tally(["no" if t["outcome"] == "done" else "not applicable" for t in turns]),
                "scores": tally([band(t.get("score")) for t in answered])}

    async def data(self) -> dict[str, Any]:
        day = (_now() - timedelta(days=1)).date().isoformat()
        counts = self._counts(day)
        return {"kept": {"on": self.settings["keep_record"], "turns": len(self._record), "most": MAX_RECORD,
                         "days": 30},
                "sharing": {"offered": True, "chosen": self.settings["share_counts"], "day": day, "counts": counts,
                            "message": json.dumps(counts), "sends": counts["sentences"] > 0,
                            "last_sent_day": self.last_sent_day, "deletion": dict(self.deletion)},
                "install": {"reference": self.install_reference, "set_aside": False, "earlier": []}}

    async def download(self, kind: str) -> dict[str, Any]:
        made = _iso(_now())
        versions = {"app": self.version, "model": self.model_version}
        if kind == "full":
            return {"about": "Everything Hunch Home keeps in its record on this machine, sentences included.",
                    "made": made, "versions": versions,
                    "record": {"on": "on" if self.settings["keep_record"] else "off", "kept": len(self._record),
                               "turns": [dict(t) for t in self._record]},
                    "install_reference": self.install_reference}
        if kind == "report":
            newest = [{"time": t["time"], "sentence": "**REDACTED**", "action": t.get("action"),
                       "kind": t.get("kind"), "score": t.get("score"), "source": t.get("source"),
                       "outcome": t["outcome"], "ms": t.get("ms")} for t in itertools.islice(self._record, 50)]
            return {"about": "Made to be pasted into a bug report.", "made": made, "versions": versions,
                    "settings": {k: ("on" if v else "off") for k, v in self.settings.items()
                                 if isinstance(v, bool)},
                    "record": {"kept": len(self._record), "newest": newest},
                    "sharing": {"install_reference": self.install_reference}}
        raise ValueError("Hunch cannot make that download.")


# ---------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the Hunch Home page on this machine.")
    parser.add_argument("--stand-in", action="store_true", help="use the stand-in home (the only way to run it "
                        "alone; as an app, the app starts this server with its own backend)")
    parser.add_argument("--first-run", action="store_true", help="the stand-in starts with nothing set up")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--trickle", type=float, default=11.0, help="seconds between the stand-in's turns")
    args = parser.parse_args(argv)
    if not args.stand_in:
        parser.error("run with --stand-in; the app itself starts this server with its own backend")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    backend = StandIn(first_run=args.first_run, trickle_s=args.trickle)
    print(f"Hunch Home page with the stand-in home: http://{args.host}:{args.port}/  (Ctrl-C to stop)")
    web.run_app(make_app(backend), host=args.host, port=args.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
