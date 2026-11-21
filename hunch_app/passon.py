"""Passing a sentence Hunch declined to the agent the owner chose, through `POST /api/conversation/process`.

The agent is a setting (`pass_to`); the default is Home Assistant's own, `conversation.home_assistant`. Hunch's
own agent is never passed to (that would loop): asked to, this module passes to the default instead.

**Conversations.** The conversation id that came with the sentence belongs to the person's own session, and
Home Assistant answers "Unauthorized" when the app passes it on (seen in the test machine, 20 Nov 2026, check
6). So it is never sent. The app starts its own conversation with the agent, and keeps the id the agent gives
back against the incoming conversation's id: the next sentence of the same incoming conversation goes to the
agent under the app's id, so a follow-up ("and the other one") keeps its context. Those pairs are forgotten
after `KEEP` seconds of quiet (Home Assistant itself ends a conversation after five minutes), and at most
`MOST` are kept.

A pass-on has `LIMIT` seconds. Past that, or when Home Assistant cannot be reached or gives a reply that cannot
be read, the result says it failed, and the turn's outcome is `error` ("Could not reach ..."). The three ways it
fails look different in the test machine (20 Nov 2026), and each gets its own note for the record:

- an agent id Home Assistant does not know: an immediate HTTP 400 ("invalid agent ID");
- a stopped agent: HTTP 200 with `response_type: error`, speech beginning "Error communicating with service"
  and no conversation id, after about 4 seconds;
- a slow agent: it simply takes its time. Giving up does not stop it: it may still act after the app has
  stopped waiting. So a sentence is handed off once and never again, and the note says the agent may still act.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .ha import HomeAssistant, Unavailable

LOG = logging.getLogger("hunch_app.passon")

DEFAULT_AGENT = "conversation.home_assistant"
PATH = "/conversation/process"
LIMIT = 30.0     # seconds
KEEP = 600.0     # seconds of quiet before a conversation pair is forgotten
MOST = 500


@dataclass
class Passed:
    ok: bool                      # the agent was reached and its reply read
    agent_id: str
    speech: str = ""
    response_type: str = ""       # the agent's: "action_done", "query_answer" or "error"
    continue_conversation: bool = False
    note: str = ""                # why it failed, in plain words, for the record
    timed_out: bool = False       # no reply in time: the agent may still act on it

    @property
    def handled(self) -> bool:
        return self.ok and self.response_type != "error"


class PassOn:
    def __init__(self, ha: HomeAssistant, own_agents: Callable[[], set[str]] = set,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ha = ha
        self.own_agents = own_agents
        self.clock = clock
        self._ours: OrderedDict[tuple[str, str], tuple[str, float]] = OrderedDict()

    def target(self, agent_id: str | None) -> str:
        """The agent a sentence will go to: the chosen one, unless it is missing or Hunch's own."""
        if not agent_id or agent_id in self.own_agents():
            return DEFAULT_AGENT
        return agent_id

    def _forget_old(self) -> None:
        now = self.clock()
        for key in [key for key, (_, at) in self._ours.items() if now - at > KEEP]:
            del self._ours[key]
        while len(self._ours) > MOST:
            self._ours.popitem(last=False)

    async def pass_on(self, text: str, language: str, agent_id: str | None, conversation_id: str | None = None,
                      device_id: str | None = None, satellite_id: str | None = None,
                      limit: float = LIMIT) -> Passed:
        agent = self.target(agent_id)
        self._forget_old()
        key = (conversation_id, agent) if conversation_id else None
        body: dict = {"text": text, "language": language or "en", "agent_id": agent}
        if key is not None and key in self._ours:
            body["conversation_id"] = self._ours[key][0]
        if device_id:
            body["device_id"] = device_id
        if satellite_id:
            body["satellite_id"] = satellite_id
        try:
            status, reply = await self.ha.rest("POST", PATH, body, timeout=limit)
        except TimeoutError:
            LOG.info("a passed-on sentence had no reply within %g s", limit)
            return Passed(False, agent, note=f"No reply within {limit:g} seconds. The agent may still act on it.",
                          timed_out=True)
        except Unavailable as error:
            LOG.info("could not pass a sentence on (%s)", type(error).__name__)
            return Passed(False, agent, note="Hunch could not reach Home Assistant.")
        if status == 400:
            LOG.info("could not pass a sentence on: Home Assistant does not know the agent")
            return Passed(False, agent, note="Home Assistant does not know this agent. Choose another in "
                          "Settings, under Pass on to.")
        if status != 200 or not isinstance(reply, dict) or not isinstance(reply.get("response"), dict):
            LOG.info("could not pass a sentence on (Home Assistant answered %s)", status)
            return Passed(False, agent, note="Home Assistant gave an answer Hunch could not read.")
        response = reply["response"]
        plain = ((response.get("speech") or {}).get("plain") or {})
        speech = plain.get("speech") if isinstance(plain.get("speech"), str) else ""
        if response.get("response_type") == "error" and speech.startswith("Error communicating with service"):
            LOG.info("could not pass a sentence on: the agent is not running")
            return Passed(False, agent, note="That agent is not running.")
        theirs = reply.get("conversation_id")
        if key is not None and isinstance(theirs, str) and theirs:
            self._ours[key] = (theirs, self.clock())
            self._ours.move_to_end(key)
        return Passed(True, agent, speech, str(response.get("response_type") or ""),
                      reply.get("continue_conversation") is True)
