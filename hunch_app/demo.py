"""See the whole app without Home Assistant: `python -m hunch_app --demo`.

Runs the real app (its rules, its turns, its store, its one-button set-up, its Wyoming agent and its page) against
the stand-in Home Assistant and the stand-in model of the tests (`standin.py`), all on this machine:

    uv run python -m hunch_app --demo                 # from the app's folder; http://127.0.0.1:8099/
    uv run python -m hunch_app --demo --port 8100 --trickle 0

- The stand-in model answers the sentences in `standin.ANSWERS` (such as "turn on the reading light", "it's a
  bit dark in here", "shut the blinds"); anything else it declines, and the real rules pass it on.
- "Try a sentence" goes through the real rules against the stand-in home.
- Teaching works as in the app: the stand-in model learns a taught sentence exactly (not near wordings, as the
  real model does). "get some light in here" is passed on until it is taught as "turn on" and "Lights"; the
  next time it comes round it switches on the kitchen's lamp. Taught examples are kept in the data folder.
- Once an assistant is set up, a sentence is sent through the Wyoming port every `--trickle` seconds, as Home
  Assistant would send it from a kitchen satellite, so the live view and the record fill (0 switches it off).
- The stand-in Home Assistant plays the Supervisor too, so the sidebar box works.
- Listening with no wake word works with a stand-in microphone (`listen/standin.py`): switching it on asks to
  fetch the speech model, pretends to fetch it (nothing is downloaded), then "hears" a short script every few
  seconds, so the page shows hearing, deciding, done and ignored.
- Nothing leaves the machine: the daily counts go nowhere even if switched on. Settings and the record are kept
  in a fresh temporary folder unless `--data` names one.

Not the real thing in four ways: no model (a lookup of a few sentences), no Home Assistant (a stand-in with a
made-up home), no voice and no microphone.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import itertools
import logging
import signal
import tempfile
from pathlib import Path

from . import NAME, agent, standin, web
from .app import App
from .ha import Access, HomeAssistant
from .listen.standin import StandInEar
from .store import Store

LOG = logging.getLogger("hunch_app.demo")

# What the demo's Home Assistant hears, in turn, once an assistant uses Hunch.
SCRIPT = ("turn on the reading light", "it's a bit dark in here", "what's the weather doing tomorrow",
          "switch off the desk fan", "get some light in here", "shut the blinds", "turn off the reading light",
          "add milk to the shopping list", "turn off all the lights")
SATELLITE = "assist_satellite.kitchen"


async def _nowhere(request, timeout):
    """The daily counts' sender in the demo: nothing is sent."""
    return 200, b""


def demo_home() -> standin.StandIn:
    """The stand-in home, with Hunch's agent under its own name (as if Submit had been pressed)."""
    home = standin.StandIn()
    home.entities[standin.HUNCH_ENTITY]["name"] = None
    home.names[standin.HUNCH_ENTITY] = [NAME]
    for row in home.agents:
        if row["id"] == standin.HUNCH_ENTITY:
            row["name"] = NAME
    return home


async def _trickle(app: App, port: int, every: float) -> None:
    from wyoming.asr import Transcript
    from wyoming.client import AsyncTcpClient

    for n in itertools.count():
        await asyncio.sleep(every)
        assistants = await app.assistants.assistants()
        if not any(a["uses_hunch"] for a in assistants):
            continue
        sentence = SCRIPT[n % len(SCRIPT)]
        try:
            async with AsyncTcpClient("127.0.0.1", port) as client:
                await client.write_event(Transcript(sentence, context={
                    "conversation_id": f"demo-{n}", "satellite_id": SATELLITE}).event())
                await client.read_event()
        except OSError as error:
            LOG.warning("the demo could not send a sentence (%s)", type(error).__name__)


async def run(stop: asyncio.Event, *, port: int = web.DEFAULT_PORT, data: Path | None = None,
              trickle: float = 12.0, ready: asyncio.Event | None = None, where: dict | None = None) -> int:
    """Run the demo until `stop` is set. `where`, if given, is filled with the page's and Wyoming's ports."""
    home = demo_home()
    ha_runner, ha_port = await standin.serve(home)
    base = f"http://127.0.0.1:{ha_port}"
    access = Access(base + "/api", f"ws://127.0.0.1:{ha_port}/api/websocket", standin.TOKEN, supervisor=True,
                    supervisor_url=base + "/supervisor")
    store = Store(data or Path(tempfile.mkdtemp(prefix="hunch-demo-")))
    store.load()
    ear = StandInEar(ready=False, fetch_steps=24, step_seconds=0.12, play_every=7.0)
    app = App(HomeAssistant(access), store, model=standin.StandInModel(), transport=_nowhere, ear=ear)
    await app.start()
    await app.ha.wait_connected(5)
    for _ in range(250):   # the home is read just after connecting
        if app.home.loaded and app.agents:
            break
        await asyncio.sleep(0.02)
    wyoming_where = agent.listening({"HUNCH_PORT": "0"}, supervisor=False)
    started = asyncio.Event()
    wyoming = asyncio.create_task(agent.serve(app, wyoming_where, started), name="demo wyoming")
    await started.wait()
    page_where = web.PageWhere("127.0.0.1", port, False, None, web.LOOPBACK_HOSTS)
    page, page_port = await web.serve(app, page_where)
    trickling = (asyncio.create_task(_trickle(app, wyoming_where.port, trickle), name="demo trickle")
                 if trickle > 0 else None)
    if where is not None:
        where.update(page=page_port, wyoming=wyoming_where.port, data=str(store.folder))
    print(f"Hunch Home demo: http://127.0.0.1:{page_port}/  (stand-in home and model; Ctrl-C to stop)",
          flush=True)
    if ready is not None:
        ready.set()
    try:
        await stop.wait()
    finally:
        for task in (wyoming, trickling):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        await page.cleanup()
        await app.stop()
        await ha_runner.cleanup()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hunch_app --demo",
                                     description="The whole app on this machine, against a stand-in home.")
    parser.add_argument("--port", type=int, default=web.DEFAULT_PORT, help="the page's port (8099)")
    parser.add_argument("--data", type=Path, default=None, help="a folder for settings and the record")
    parser.add_argument("--trickle", type=float, default=12.0,
                        help="seconds between sentences sent as Home Assistant would (0: none)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    async def go() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        return await run(stop, port=args.port, data=args.data, trickle=args.trickle)

    return asyncio.run(go())
