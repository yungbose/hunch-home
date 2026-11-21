"""Try a freshly built Hunch Home image before it is pushed: the `Image` workflow runs this with the image's own
Python, from the image itself, in two containers on the runner's own network.

    python check_image.py standin        # a stand-in Home Assistant on 127.0.0.1:8123, until stopped
    python check_image.py ask VERSION    # ask the app (already started against it) and check what it did

GitHub's machines cannot run Home Assistant OS and its Supervisor, so the app is started the way people run it
in Docker (an address and an access token) against the stand-in Home Assistant that ships inside the app for its
demo (`hunch_app/standin.py`): replies shaped as a real Home Assistant 2026.11.2 gave them, and a made-up home
with a reading light, a lamp hidden from Assist and locks. The model is the real one, inside the image.

`ask` fails (exit 1) unless all of this holds:

1. The page answers on 127.0.0.1 with the version asked for, and the model is loaded.
2. "could you pop the reading light on", sent through the Wyoming port as Home Assistant sends it, switches the
   reading light on, and the page's record says "Hunch did it" (turn on, Lights).
3. "i used to lock the door every night" moves nothing: the model answers "not for me" and the sentence is
   passed on to Home Assistant's own agent.
4. A sentence about the lamp hidden from Assist leaves it as it was.
5. Every intent the app sent to Home Assistant carried `"assistant": "conversation"` (Home Assistant's exposure
   check).
6. The page refuses a request that names another host, and a change without its own header.

The workflow then checks that the app's log holds none of these sentences.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

HA = "http://127.0.0.1:8123"
PAGE = "http://127.0.0.1:8099"
WYOMING_PORT = 10500
CHECK_PATH = "/check/requests"   # the stand-in's own list of what it was sent, for step 5

LIGHT = "light.reading_light"
HIDDEN = "light.window_lamp"
LOCKS = ("lock.front_door", "lock.garage")


async def serve_standin() -> None:
    from aiohttp import web
    from hunch_app import standin

    home = standin.StandIn()
    app = home.app()

    async def requests(_request):
        return web.json_response([{"method": m, "path": p, "body": b} for m, p, b in home.requests])

    app.router.add_get(CHECK_PATH, requests)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8123).start()
    print("stand-in Home Assistant on 127.0.0.1:8123", flush=True)
    await asyncio.Event().wait()


def token() -> str:
    from hunch_app import standin

    return standin.TOKEN


def get(url: str, headers: dict | None = None) -> tuple[int, object]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, json.loads(reply.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, None


def post(url: str, body: dict, headers: dict) -> int:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status
    except urllib.error.HTTPError as error:
        return error.code


def states() -> dict[str, str]:
    status, rows = get(f"{HA}/api/states", {"Authorization": f"Bearer {token()}"})
    assert status == 200, f"the stand-in's states: HTTP {status}"
    return {row["entity_id"]: row["state"] for row in rows}


def page(path: str) -> tuple[int, object]:
    return get(f"{PAGE}/{path}", {"Host": "127.0.0.1:8099"})


async def say(text: str) -> tuple[str, str]:
    from wyoming.asr import Transcript
    from wyoming.client import AsyncTcpClient

    async with AsyncTcpClient("127.0.0.1", WYOMING_PORT) as client:
        await client.write_event(Transcript(text=text, language="en", context={"conversation_id": "check"}).event())
        event = await asyncio.wait_for(client.read_event(), 40)
    return event.type, (event.data or {}).get("text", "")


def newest_turn(sentence: str) -> dict:
    for _ in range(20):
        status, turns = page("api/record?limit=5")
        assert status == 200, f"the page's record: HTTP {status}"
        for turn in turns:
            if turn.get("sentence") == sentence:
                return turn
        time.sleep(0.5)
    raise AssertionError(f"no turn in the record for {sentence!r}")


def ask(version: str) -> None:
    status, state = 0, {}
    for _ in range(90):
        try:   # the port is closed until the app has loaded its model and dropped to its own user
            status, state = page("api/state")
        except OSError:
            status, state = 0, {}
        if status == 200 and state.get("model_ready") and state.get("connected"):
            break
        time.sleep(2)
    else:
        raise AssertionError(f"the page never said the model was ready and Home Assistant connected: {status}")
    assert state["version"] == version, f"the app says it is {state['version']}, not {version}"
    print(f"1. page: version {state['version']}, model {state['model_version']}, connected")

    before = states()
    assert before[LIGHT] == "off", before[LIGHT]
    kind, reply = asyncio.run(say("could you pop the reading light on"))
    time.sleep(1)
    turn = newest_turn("could you pop the reading light on")
    print(f"2. {kind}: {reply!r}; {turn['label']} ({turn['action']} / {turn['kind']}, {turn['ms']} ms)")
    assert states()[LIGHT] == "on", "the reading light did not come on"
    assert (turn["outcome"], turn["action"], turn["kind"]) == ("done", "turn on", "Lights"), turn

    before = states()
    kind, reply = asyncio.run(say("i used to lock the door every night"))
    time.sleep(1)
    turn = newest_turn("i used to lock the door every night")
    print(f"3. {kind}: {reply!r}; {turn['label']} ({turn['action']})")
    assert states() == before, "a look-alike sentence moved something"
    assert (turn["outcome"], turn["action"]) == ("passed", "not for me"), turn
    assert all(before[lock] == "locked" for lock in LOCKS)

    before = states()
    kind, reply = asyncio.run(say("could you switch the window lamp off"))
    time.sleep(1)
    print(f"4. {kind}: {reply!r}; the hidden lamp is {states()[HIDDEN]}")
    assert states()[HIDDEN] == before[HIDDEN], "a lamp hidden from Assist changed"

    status, sent = get(f"{HA}{CHECK_PATH}")
    intents = [r["body"] for r in sent if r["path"] == "/api/intent/handle"]
    assert intents, "the app sent no intent at all"
    missing = [body for body in intents if body.get("assistant") != "conversation"]
    assert not missing, f"intents without Home Assistant's exposure check: {missing}"
    print(f"5. {len(intents)} intent requests, every one with the exposure check")

    foreign, _ = get(f"{PAGE}/api/state", {"Host": "evil.example:8099"})
    unmarked = post(f"{PAGE}/api/try", {"text": "turn on the lamp"}, {"Host": "127.0.0.1:8099"})
    print(f"6. another host: HTTP {foreign}; a change without the page's header: HTTP {unmarked}")
    assert 400 <= foreign < 500 and unmarked == 403, (foreign, unmarked)
    print("every check passed")


def main(argv: list[str]) -> int:
    if argv[1:2] == ["standin"]:
        asyncio.run(serve_standin())
        return 0
    if argv[1:2] == ["ask"] and len(argv) == 3:
        ask(argv[2])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
