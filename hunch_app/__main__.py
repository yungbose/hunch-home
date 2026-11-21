"""Start Hunch Home: `python -m hunch_app`, or `python -m hunch_app --demo` to click through the whole app on
this machine against a stand-in Home Assistant and a stand-in model (`demo.py`). `python -m hunch_app health`
is the image's health check: exit 0 when the app has said within the last 45 seconds that its Wyoming server is
serving, by touching a file (`health.py`); it opens no connection. (The image's build checks the model with
`python -m hunch_app.model prepare`, which needs no packages.)

| Variable | Default | Meaning |
|---|---|---|
| `SUPERVISOR_TOKEN` | set by the Supervisor | Under Home Assistant's Supervisor: reach Home Assistant through its proxy. |
| `HASS_URL`, `HASS_TOKEN` | none | In Docker: Home Assistant's address and a long-lived access token. |
| `HUNCH_DATA` | `/data` | The folder for settings and the record. |
| `HUNCH_MODEL_DIR` | `/model` | The typed build's folder, with its `manifest.json`. |
| `HUNCH_THREADS` | `2` | Processor threads for the model. |
| `HUNCH_FAST` | on for aarch64, else off | `1` or `0`: run the 4-bit weights the faster way (`fast.py`). |
| `HUNCH_PORT` | `10500` | The Wyoming port. Never publish it to a network: Wyoming has no sign-in. |
| `HUNCH_WYOMING_HOST` | `127.0.0.1` | Docker only: the address the Wyoming server listens on (under the Supervisor it listens on all addresses inside the container). |
| `HUNCH_ALLOW_FROM` | `172.30.32.1` under the Supervisor; loopback in Docker | The peer addresses or networks the Wyoming server answers, comma separated. In Docker, required when `HUNCH_WYOMING_HOST` is not loopback. |
| `HUNCH_PAGE_PORT` | `8099` | The page's port (Ingress uses it as an app). |
| `HUNCH_PAGE_HOST` | `127.0.0.1` | Docker only: the address the page listens on (under the Supervisor it listens inside the container and answers only Ingress). |
| `HUNCH_PAGE_ALLOW_FROM` | none | Docker only: the addresses that may open the page, comma separated; required when `HUNCH_PAGE_HOST` is not loopback, since the page has no sign-in of its own. |
| `HUNCH_PAGE_HOSTS` | none | Docker only: more names the page may be opened by (besides `localhost`, `127.0.0.1`, `[::1]` and its own address), comma separated. Any other name in a request's `Host` is refused. |
| `HUNCH_INGRESS_HOSTS` | any | As an app: the names a request through Ingress may carry in `Host` (only Ingress can connect at all). |
| `HUNCH_ALLOW_ROOT` | off | `1`: stay root when started as root (logs a warning). Otherwise the app refuses to serve as root. |
| `HUNCH_HEALTH_FILE` | `/tmp/hunch-home.alive` | The file the app touches while it serves, for the health check. |
| `HUNCH_LOG` | `info` | `debug` adds detail; nothing at `info` holds a token, a sentence or a device's name. |

**Which user.** Started as root (Home Assistant's Supervisor starts every app as root and gives it a `/data`
only root may write to; a plain `docker run` of the image is root too), the app gives its data folder to the
ordinary user the image made (`hunch`, 10001), becomes that user with no supplementary groups and checks that
it took (every user and group id non-zero, and root cannot be had back), before it opens any port. If any of
that fails it serves nothing and exits with one line saying why. Running as root is only by choice:
`HUNCH_ALLOW_ROOT=1`, which logs a warning. Started as another user (`--user 10001:10001`, as
`compose.example.yaml` does) it stays that user.

The order: become the ordinary user, read the settings and the record, connect to Home Assistant (in the background, retrying), start the
Wyoming server and the page's server (the app is the page's backend), offer the agent to Home Assistant (under
the Supervisor), then load the model in a thread of its own. Sentences that arrive before the model is ready are
passed on. One line says when it is ready.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import signal
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

from . import NAME, __version__, agent, web
from . import health as heartbeat
from .app import App
from .ha import ConfigError, HomeAssistant, access_from_env
from .listen import ProcessEar
from .model import COULD_NOT_LOAD, ModelError, load
from .store import Store

LOG = logging.getLogger("hunch_app")
STOP_LIMIT = 3.0  # seconds
APP_USER = "hunch"   # made by the image, uid 10001


def _flag(value: str | None, default: bool) -> bool:
    value = (value or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def in_own_thread(work, *args) -> asyncio.Future:
    """Run `work(*args)` in a daemon thread of its own and give its result as a future.

    Not `asyncio.to_thread`: the default executor's threads are waited for when the event loop closes, so a
    slow model load would hold the process past the Supervisor's stop limit. A daemon thread is left behind
    when the process ends."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(outcome, value) -> None:
        if not future.done():
            (future.set_result if outcome else future.set_exception)(value)

    def run() -> None:
        try:
            outcome, value = True, work(*args)
        except BaseException as error:  # noqa: BLE001 - handed to the waiting coroutine
            outcome, value = False, error
        with contextlib.suppress(RuntimeError):  # the loop has closed: nobody is waiting any more
            loop.call_soon_threadsafe(settle, outcome, value)

    threading.Thread(target=run, name="hunch model loader", daemon=True).start()
    return future


async def load_model(app: App, env: Mapping[str, str]) -> None:
    folder = Path(env.get("HUNCH_MODEL_DIR", "/model"))
    threads = int(env.get("HUNCH_THREADS", "2") or 2)
    use_fast = _flag(env.get("HUNCH_FAST"), platform.machine().lower() in {"aarch64", "arm64"})
    try:
        model = await in_own_thread(load, folder, threads, use_fast)
    except ModelError as error:
        LOG.error("the model could not be loaded: %s. Every sentence is passed on until it is.", error)
        app.model_problem = COULD_NOT_LOAD
        return
    except ImportError as error:
        LOG.error("the model's runtime is missing (%s). Every sentence is passed on.", error.name)
        app.model_problem = COULD_NOT_LOAD
        return
    except Exception as error:  # noqa: BLE001 - such as fast.py's ValueError: one plain line, never a traceback
        LOG.error("the model could not be loaded (%s: %s). Every sentence is passed on; restarting the app may "
                  "help.", type(error).__name__, " ".join(str(error).split()))
        app.model_problem = COULD_NOT_LOAD
        return
    app.set_model(model)
    LOG.info("model %s loaded%s", model.version, " (faster 4-bit route)" if use_fast else "")


async def run(env: Mapping[str, str], stop: asyncio.Event, ready: asyncio.Event | None = None,
              model=None) -> int:
    """Run until `stop` is set. `model` stands in for loading one (tests)."""
    try:
        access = access_from_env(env)
        where = agent.listening(env, access.supervisor)
        page_where = web.page_listening(env, access.supervisor)
    except (ConfigError, agent.ListenError, ValueError) as error:
        LOG.error("not starting: %s", error)
        return 2
    store = Store(env.get("HUNCH_DATA", "/data"))
    store.load()
    ha = HomeAssistant(access)
    ear = ProcessEar(store.folder / "speech", threads=int(env.get("HUNCH_THREADS", "2") or 2), env=env)
    app = App(ha, store, model=model, ear=ear)
    await app.start()
    listening = asyncio.Event()
    server = asyncio.create_task(agent.serve(app, where, listening), name="hunch wyoming")
    await asyncio.wait({server, asyncio.create_task(listening.wait())}, return_when=asyncio.FIRST_COMPLETED)
    if server.done():
        LOG.error("the Wyoming server could not start (%s)", type(server.exception()).__name__)
        await app.stop()
        return 1
    port = where.port
    beat = asyncio.create_task(heartbeat.keep_alive(heartbeat.beat_file(env), lambda: not server.done()),
                               name="hunch health")
    page = None
    try:
        page, page_port = await web.serve(app, page_where)
        LOG.info("page at http://%s:%d/%s", page_where.host, page_port, " (through Home Assistant's Ingress only)"
                 if access.supervisor else "")
    except OSError as error:   # voice keeps working without the page
        LOG.error("the page could not start (%s); the agent works without it", type(error).__name__)
    await agent.announce(ha, port)
    loader = asyncio.create_task(load_model(app, env)) if model is None else None
    LOG.info("%s %s ready: Wyoming agent on %s port %d, answering %s; Home Assistant %s", NAME, __version__,
             where.host, port, ", ".join(str(n) for n in where.allow),
             "through the Supervisor" if access.supervisor else "with an access token")
    if ready is not None:
        ready.set()
    await stop.wait()
    LOG.info("stopping")
    for task in (server, loader, beat):
        if task is not None:
            task.cancel()
    await asyncio.gather(*(t for t in (server, loader, beat) if t is not None), return_exceptions=True)
    if page is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(page.cleanup(), 1.0)
    # The Supervisor marks an app that does not stop within a few seconds as failed: never wait long.
    try:
        await asyncio.wait_for(app.stop(), STOP_LIMIT)
    except TimeoutError:
        LOG.warning("stopping took longer than %g s; leaving the rest", STOP_LIMIT)
    LOG.info("stopped")
    return 0


class NotDropped(RuntimeError):
    """Started as root and could not become the ordinary user. The message says why, in one line."""


def become_app_user(data: Path, env: Mapping[str, str] | None = None, user: str = APP_USER, osmod=os) -> str:
    """Started as root: give `data` (and what is in it) to the image's ordinary user, become that user with no
    supplementary groups, and check it took. Returns "already" (not started as root), "became <user>", or
    "root by choice" (`HUNCH_ALLOW_ROOT=1`). Raises `NotDropped` when it started as root without that choice and
    the change did not complete or did not hold: then the app serves nothing."""
    env = {} if env is None else env
    if not hasattr(osmod, "geteuid") or osmod.geteuid() != 0:
        return "already"
    if (env.get("HUNCH_ALLOW_ROOT") or "").strip() == "1":
        return "root by choice"
    import pwd

    try:
        entry = pwd.getpwnam(user)
        uid, gid = entry.pw_uid, entry.pw_gid
        if uid == 0 or gid == 0:
            raise NotDropped(f"the user {user!r} is root")
        data.mkdir(parents=True, exist_ok=True)
        for folder, _folders, files in osmod.walk(data):
            osmod.chown(folder, uid, gid)
            for name in files:
                osmod.chown(os.path.join(folder, name), uid, gid, follow_symlinks=False)
        osmod.setgroups([])
        osmod.setgid(gid)
        osmod.setuid(uid)
    except KeyError:
        raise NotDropped(f"there is no user {user!r} to become (this is not the app's image)") from None
    except OSError as error:
        raise NotDropped(f"could not become the user {user!r} ({error.strerror or type(error).__name__})") from None
    ids = (osmod.getuid(), osmod.geteuid(), osmod.getgid(), osmod.getegid())
    if 0 in ids or ids != (uid, uid, gid, gid):
        raise NotDropped("the change of user did not hold")
    if osmod.getgroups() not in ([], [gid]):
        raise NotDropped("supplementary groups are still set")
    try:
        osmod.setuid(0)
    except OSError:
        return f"became {user}"
    raise NotDropped("it could become root again")


def health(env: Mapping[str, str] | None = None) -> int:
    """0 when the app has touched its heartbeat file lately (`health.py`), else 1. Opens no connection, so the
    Wyoming port's allow-list never sees the check."""
    env = os.environ if env is None else env
    return 0 if heartbeat.healthy(heartbeat.beat_file(env)) else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--demo":
        from . import demo

        return demo.main(args[1:])
    if args == ["health"]:
        return health()
    if args:
        print(__doc__.split("|", 1)[0].rstrip(), file=sys.stderr)
        return 2
    env = dict(os.environ)
    level = logging.DEBUG if env.get("HUNCH_LOG", "").lower() == "debug" else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    # aiohttp's access and client logs can hold addresses; keep them quiet at the default level.
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    try:
        user = become_app_user(Path(env.get("HUNCH_DATA", "/data")), env)
    except NotDropped as error:
        LOG.error("not starting: started as root and %s. Start it as an ordinary user (--user 10001:10001), or "
                  "set HUNCH_ALLOW_ROOT=1 to run as root on purpose", error)
        return 3
    if user == "root by choice":
        LOG.warning("running as root, because HUNCH_ALLOW_ROOT=1 is set")
    else:
        LOG.info("running as user %d, not root", os.geteuid())

    async def go() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        return await run(env, stop)

    return asyncio.run(go())


if __name__ == "__main__":
    sys.exit(main())
