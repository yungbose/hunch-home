"""One sentence in, exactly one outcome out: the app's whole handling of a sentence, for Assist and listening.

| Outcome | Label | When |
|---|---|---|
| `done` | Hunch did it | A plan was carried out |
| `passed` | Passed to <agent's name> | Not a home command of a kind Hunch carries out (`hold.py`), or Hunch's time ran out or its model failed. Through Assist only. |
| `ignored` | Ignored | Listening mode, anything that is not carried out |
| `error` | Could not reach <agent's name> | The pass-on failed or timed out |
| `nothing` | Hunch did nothing | Through Assist: a home command of a kind Hunch carries out, that it did not carry out, with one short line saying why (`hold.py`): never passed on |

One departure from that table: when Home Assistant gives no answer in time to an intent call, the device may
still change, so the sentence is not passed on as well (one sentence, one hand-off): the outcome is `error`,
"Could not reach Home Assistant", with a note saying the device may still change.

For a sentence from Assist (`source="assist"`):

1. **Hunch's part**, within `HUNCH_LIMIT` seconds (`LISTENING_LIMIT` for a sentence heard in listening mode, since
   nobody waits on a reply there): the model's answer, the room the sentence came from and the
   rules' `decide`. Past the limit the sentence is handed on as "server slow" (the counts' word for it).
2. A plan is carried out (`act.py`, its own limit). If Home Assistant cannot carry it out, Hunch says so itself
   ("Hunch did nothing", "Sorry, that didn't work.") and the sentence is not passed on; a question (a weather or
   timer-status plan) is passed on instead, as any question is.
3. A hand-back that `hold.held` keeps (Hunch took it for a home command of a kind it carries out, and found no
   device or room, more than one, a later time, a number it cannot be sure of, a "don't" or an "if", or Locks
   and doors off) is answered by Hunch itself in one short line, "Hunch did nothing", and nothing moves. Any
   other hand-back goes to the chosen agent (`passon.py`, 30 seconds). Its reply is Hunch's reply.

For a sentence heard in listening mode (`source="listening"`) nothing is ever passed on: the outcome is `done`
or `ignored`. The rules are asked with Locks and doors off, whatever the setting, and on top of that any plan
that would unlock, or open or close anything that is not a window covering, or switch a device whose name or
the sentence says it is a way into the home, is refused (`listening_refuses`).

A sentence that arrives while the same sentence is being passed on is not passed on again (an agent that sent
it back to Hunch would otherwise loop); it ends as `error` at once.

Each turn is written to the record (unless the record is off, or it is an ignored sentence from listening), a
carried-out plan is watched in case it is undone (`undone.py`), and one `turn` event goes to every subscriber.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from . import act, hold, unsure, why
from .api import (
    ASSIST,
    DONE,
    ERROR,
    IGNORED,
    LABEL_DONE,
    LABEL_ERROR,
    LABEL_IGNORED,
    LABEL_NOTHING,
    LABEL_PASSED,
    LISTENING,
    NOTHING,
    PASSED,
)
from .home import HomeView
from .logic import speech, undo
from .logic.client import Answer, HandBack
from .logic.decide import ENTRY_WORDS, GENERIC_DOMAINS, Plan, decide
from .logic.record import entry_for, new_id
from .logic.table import WINDOW_COVERINGS
from .passon import PassOn
from .store import Store
from .undone import Undone

LOG = logging.getLogger("hunch_app.turn")

HUNCH_LIMIT = 2.0        # seconds for the model and the rules
LISTENING_LIMIT = 5.0    # the same, for a sentence heard in listening mode: nobody is waiting on a reply
FRESH = 1.0              # seconds: a home read this recently is not read again for a sentence
# The rules' reasons for not finding the one device a sentence means: none, none in that room, several (a device
# just exposed may be the one), or no room to look in. Each is decided once more after the home is read again.
NO_DEVICE = frozenset({"no device", "no match", "ambiguous", "no room"})
MAX_CHARACTERS = 300     # as the model's runtime has it: a command, not a document


@dataclass
class Turn:
    """One sentence and what became of it."""

    id: str
    at: float
    source: str
    sentence: str
    outcome: str
    label: str
    reply: str = ""
    handled: bool = False            # for Wyoming: Handled (True) or NotHandled
    answer: Answer | HandBack | None = None
    result: Plan | HandBack | None = None
    entities: list[str] = field(default_factory=list)
    ms: int = 0
    continue_conversation: bool = False
    failed_plan: Plan | None = None    # a plan Home Assistant could not carry out, noted in the record
    note: str = ""                     # for the record: why it failed, when that is not the rules' reason
    watch: tuple | None = field(default=None, repr=False)   # what to watch once the turn is written

    def event(self) -> dict:
        """The `turn` event (`web.Backend`), plus the reply and the note. An ignored turn carries no sentence
        at all (a ruling of 20 Nov 2026), nor anything else it heard."""
        answer = self.answer if isinstance(self.answer, Answer) else None
        event = {"event": "turn", "id": self.id, "time": iso(self.at), "source": self.source,
                 "sentence": self.sentence, "outcome": self.outcome, "label": self.label,
                 "action": answer.action if answer else None, "kind": answer.kind if answer else None,
                 "score": answer.similarity if answer else None, "entities": list(self.entities), "ms": self.ms,
                 "reply": self.reply, "note": self.note, "short": hold.short(self.outcome, self.reply)}
        if self.outcome == IGNORED:
            for heard in ("sentence", "action", "kind", "score", "reply", "note"):
                del event[heard]
            event["entities"] = []
        return event


def iso(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(sentence: object) -> str:
    """A sentence with its spaces tidied. Raises `ValueError` for an empty or overlong one."""
    if not isinstance(sentence, str) or not sentence.strip():
        raise ValueError("Type a sentence first.")
    text = " ".join(sentence.split())
    if len(text) > MAX_CHARACTERS:
        raise ValueError(f"That is too long for a command. Keep it under {MAX_CHARACTERS} characters.")
    return text


def _tokens(text: str) -> set[str]:
    return set(re.split(r"[\W_]+", text.casefold())) - {""}


def listening_refuses(plan: Plan, things: dict, sentence: str) -> str | None:
    """Why listening mode refuses a plan, or None. Whatever the Locks and doors setting."""
    if plan.action == "unlock":
        return "listening never unlocks"
    intents = {intent for intent, _ in plan.ha_calls()}
    for entity_id in plan.entity_ids:
        thing = things.get(entity_id)
        domain = entity_id.split(".", 1)[0]
        if domain == "lock" and intents != {"HassTurnOn"}:
            return "listening never unlocks"
        if domain == "cover" and (thing is None or thing.device_class not in WINDOW_COVERINGS):
            return "listening opens and closes window coverings only"
        if plan.action in {"open", "close"} and domain != "cover":
            return "listening opens and closes window coverings only"
        names = " ".join((entity_id, *(thing.names if thing is not None else ())))
        if domain in GENERIC_DOMAINS and ENTRY_WORDS & (_tokens(names) | _tokens(sentence)):
            return "listening never works a way into the home"
    return None


def acted_part(plan: Plan, acted: list[str]) -> Plan:
    """The part of a plan that acted: its devices and calls narrowed to the entity ids Home Assistant changed. A
    plan that named no devices (a room's lights, found by Home Assistant) takes the ones that changed."""
    keep = set(acted)
    return replace(plan, entity_ids=tuple(e for e in (plan.entity_ids or tuple(acted)) if e in keep),
                   calls=tuple(call for call in plan.calls if str(call[1].get("name")) in keep))


class Turns:
    """Runs turns. Everything it needs is given; nothing here reads the environment."""

    def __init__(self, ha, home: HomeView, store: Store, passon: PassOn, undone: Undone,
                 model: Callable[[], object], agent_name: Callable[[str], str],
                 emit: Callable[[dict], None], clock: Callable[[], float] = time.time) -> None:
        self.ha = ha
        self.home = home
        self.store = store
        self.passon = passon
        self.undone = undone
        self.model = model
        self.agent_name = agent_name
        self.emit = emit
        self.clock = clock
        self.hunch_limit = HUNCH_LIMIT
        self.listening_limit = LISTENING_LIMIT
        self.act_limit = act.LIMIT
        self.passon_limit = 30.0
        self._passing: dict[str, int] = {}

    # Hunch's part.

    async def think(self, sentence: str, area_id: str | None, locks_and_doors: bool, limit: float | None = None
                    ) -> tuple[Answer | HandBack, Plan | HandBack]:
        """The model's answer and the rules' decision, within Hunch's time limit (`limit`, or `hunch_limit`).
        When the rules find no device, the home is read again (unless it was just read) and the sentence is
        decided once more, so a device the owner has just exposed is found at once."""
        limit = self.hunch_limit if limit is None else limit
        started = time.monotonic()
        try:
            answer = await asyncio.wait_for(self.model().answer(sentence), limit)
        except TimeoutError:
            answer = HandBack("server slow", f"no answer within {limit:g} s")
        result = decide(sentence, answer, self.home.home(), area_id, locks_and_doors=locks_and_doors)
        if isinstance(result, HandBack) and result.reason in NO_DEVICE:
            left = limit - (time.monotonic() - started)
            if left > 0.05 and await self.home.fresh(older_than=FRESH, limit=left):
                result = decide(sentence, answer, self.home.home(), area_id, locks_and_doors=locks_and_doors)
        # Words against the answer, a number the rules would misread, or a later time they do not see: never
        # acted on (`unsure.py`). The names of what the plan acts on are set aside first, where they occur once.
        return answer, unsure.review(sentence, answer, result, self.home.home())

    # A turn.

    async def run(self, sentence: str, source: str = ASSIST, language: str = "en",
                  conversation_id: str | None = None, device_id: str | None = None,
                  satellite_id: str | None = None, area_id: str | None = None,
                  may_act: Callable[[], bool] | None = None) -> Turn:
        """Handle one sentence and return its one outcome. Never raises for a sentence's sake. `may_act`, for a
        sentence heard in listening mode, is asked immediately before anything is sent to Home Assistant: false
        (listening was switched off meanwhile) and the sentence is ignored."""
        started = time.monotonic()
        turn_id, at = new_id(), self.clock()
        try:
            text = clean(sentence)
        except ValueError:
            text = (sentence or "").strip() if isinstance(sentence, str) else ""
            turn = Turn(turn_id, at, source, text[:MAX_CHARACTERS], IGNORED if source == LISTENING else ERROR,
                        LABEL_IGNORED if source == LISTENING else "Nothing to do")
            return turn
        if source == ASSIST and text.casefold() in self._passing:
            turn = Turn(turn_id, at, source, text, ERROR, LABEL_ERROR.format(agent=self.agent_name(
                self.passon.target(self.store.settings.get("pass_to")))),
                note="This sentence came back while Hunch was passing it on, so it was not passed on again.")
            LOG.warning("a sentence came back while it was being passed on; not passing it on again")
            return self._finish(turn, started)
        if area_id is None:
            area_id = self.home.room_for(device_id, satellite_id)
        if source == LISTENING and area_id is None:
            area_id = self.home.area_id_for(self.store.settings.get("mic_room"))
        locks = self.store.settings.get("locks_and_doors", True) is True and source != LISTENING
        answer, result = await self.think(text, area_id, locks,
                                          self.listening_limit if source == LISTENING else None)
        things = {thing.entity_id: thing for thing in self.home.home().things}

        if isinstance(result, Plan) and source == LISTENING:
            refused = listening_refuses(result, things, text)
            if refused is not None:
                result = HandBack("not carried out", refused)

        if isinstance(result, Plan) and source == LISTENING and may_act is not None and not may_act():
            result = HandBack("not carried out", "listening was switched off")

        if isinstance(result, Plan):
            turn = await self._carry_out(turn_id, at, source, text, answer, result, things, language,
                                         device_id, satellite_id, area_id, conversation_id)
            return self._finish(turn, started)

        if source == LISTENING:
            turn = Turn(turn_id, at, source, text, IGNORED, LABEL_IGNORED, answer=answer, result=result)
            return self._finish(turn, started)
        kept = hold.held(answer, result, text, locks)
        if kept is not None:
            # A home command of a kind Hunch carries out: Hunch says in one short line why it did nothing.
            turn = self._nothing(turn_id, at, text, answer, result, kept)
            return self._finish(turn, started)
        turn = await self._pass_on(turn_id, at, text, answer, result, None, language, conversation_id,
                                   device_id, satellite_id)
        if isinstance(result, HandBack) and result.detail in unsure.NOTES and not turn.note:
            turn.note = result.detail
        return self._finish(turn, started)

    async def _carry_out(self, turn_id, at, source, text, answer, plan, things, language, device_id,
                         satellite_id, area_id, conversation_id) -> Turn:
        before = {entity_id: undo.seen_from(entity_id, str(state.get("state")), state.get("attributes") or {})
                  for entity_id in plan.entity_ids if (state := self.home.states.get(entity_id))}
        self.undone.before(turn_id, plan)
        done = await act.carry_out(self.ha, plan, language, device_id, satellite_id, area_id,
                                   self.home.states, self.act_limit)
        # A device Home Assistant lists as switched while it is not available did not change (`act.checked`).
        done = act.checked(done, plan, self.home.states, self.home.name_of)
        if done.ok:
            self.undone.after(turn_id)
            note = ""
            if done.partial:
                # Some devices acted: this sentence is never handed on (`act.py`). Only those that changed are
                # the turn's, and the note says which did not respond and which are not available.
                names = done.names or [self.home.name_of(entity_id) for entity_id in done.entity_ids]
                missed = done.missed or [self.home.name_of(entity_id) for entity_id in plan.entity_ids
                                         if entity_id not in done.entity_ids
                                         and entity_id not in done.unavailable_ids]
                notes = []
                if missed or not done.unavailable:
                    who = speech.join(missed) if missed else "Some devices"
                    notes.append(f"{who} did not answer in time and may still change." if done.late
                                 else f"{who} did not respond.")
                if done.unavailable:
                    notes.append(act.not_available(done.unavailable))
                note = " ".join(notes)
                # The turn, the record and the "undone" watch hold only what acted: a device that failed or was
                # not available never changed, so a later report from it cannot undo this turn.
                plan = acted_part(plan, done.entity_ids)
                LOG.info("a plan was carried out in part; not passing the sentence on")
            else:
                names = done.names or [self.home.name_of(entity_id) for entity_id in plan.entity_ids]
            reply = "" if source == LISTENING else " ".join(part for part in (done.speech, note) if part)
            turn = Turn(turn_id, at, source, text, DONE, LABEL_DONE, reply, True, answer, plan, names, note=note)
            if self.store.keeps(source, DONE):
                states = {entity_id: str(state.get("state")) for entity_id in plan.entity_ids
                          if (state := self.home.states.get(entity_id))}
                turn.watch = (plan, before, states)  # started once the turn is written (`_finish`)
            return turn
        self.undone.not_carried_out(turn_id)
        why = HandBack("not carried out", plan.intent)
        if source == LISTENING:
            return Turn(turn_id, at, source, text, IGNORED, LABEL_IGNORED, answer=answer, result=why,
                        note=done.problem)
        if done.uncertain:
            # Home Assistant may still carry it out: handing the sentence to another agent as well could act
            # twice. One sentence, one hand-off.
            LOG.info("Home Assistant did not answer an intent in time; not passing the sentence on")
            turn = Turn(turn_id, at, source, text, ERROR, LABEL_ERROR.format(agent="Home Assistant"),
                        "Sorry, Home Assistant did not answer in time.", False, answer, why, note=done.problem)
            turn.failed_plan = plan
            return turn
        if getattr(answer, "action", None) == "ask":
            # A question is never Hunch's to hold (`hold.held`): the other agent is the fallback, as before.
            LOG.info("Home Assistant did not answer a question Hunch asked it; passing the sentence on")
            turn = await self._pass_on(turn_id, at, text, answer, why, plan, language, conversation_id,
                                       device_id, satellite_id)
            if done.unavailable and not turn.note:
                turn.note = done.problem
            return turn
        LOG.info("Home Assistant did not carry out a plan; Hunch says so and passes nothing on")
        turn = self._nothing(turn_id, at, text, answer, why, hold.FAILED)
        if done.unavailable and not done.missed:   # the devices were not there: the reply names them
            turn.reply = turn.note = hold.unavailable(done.unavailable)
        turn.failed_plan = plan
        return turn

    @staticmethod
    def _nothing(turn_id, at, text, answer, why: HandBack, kept: str) -> Turn:
        """A "Hunch did nothing" turn: its reply, and the note on the page, are the one short line for `kept`."""
        reply = hold.REPLIES[kept]
        return Turn(turn_id, at, ASSIST, text, NOTHING, LABEL_NOTHING, reply, True, answer, why, note=reply)

    async def _pass_on(self, turn_id, at, text, answer, why: HandBack, plan, language, conversation_id,
                       device_id, satellite_id) -> Turn:
        agent = self.passon.target(self.store.settings.get("pass_to"))
        name = self.agent_name(agent)
        key = text.casefold()
        self._passing[key] = self._passing.get(key, 0) + 1
        try:
            passed = await self.passon.pass_on(text, language, agent, conversation_id, device_id, satellite_id,
                                               self.passon_limit)
        finally:
            self._passing[key] -= 1
            if not self._passing[key]:
                del self._passing[key]
        if not passed.ok:
            note = (f"Hunch stopped waiting for {name} after {self.passon_limit:g} seconds. {name} may still "
                    "act on it." if passed.timed_out else passed.note)
            turn = Turn(turn_id, at, ASSIST, text, ERROR, LABEL_ERROR.format(agent=name),
                        f"Sorry, I could not reach {name}.", False, answer, why, note=note)
        else:
            turn = Turn(turn_id, at, ASSIST, text, PASSED, LABEL_PASSED.format(agent=name), passed.speech,
                        passed.handled, answer, why, continue_conversation=passed.continue_conversation)
        turn.failed_plan = plan
        return turn

    def _finish(self, turn: Turn, started: float) -> Turn:
        turn.ms = round((time.monotonic() - started) * 1000)
        plan = turn.failed_plan
        error = plan is not None
        entry = entry_for(turn.sentence, turn.answer, turn.result, turn.at, error=error, plan=plan,
                          entry_id=turn.id)
        extra = {"source": turn.source, "outcome": turn.outcome, "label": turn.label, "entities": turn.entities,
                 "ms": turn.ms, "reply": turn.reply, "note": turn.note}
        if self.store.add(entry, extra):
            if turn.watch is not None:
                self.undone.watch(turn.id, *turn.watch)
            asyncio.get_running_loop().create_task(self._save())
        try:
            self.emit(turn.event())
        except Exception:
            LOG.exception("sending a turn event failed")
        return turn

    async def _save(self) -> None:
        try:
            await self.store.save_record()
        except OSError as error:
            LOG.warning("could not save the record (%s)", type(error).__name__)

    # Trying a sentence without doing it.

    async def try_only(self, sentence: str, room: str | None = None) -> dict:
        started = time.monotonic()
        text = clean(sentence)
        area_id = self.home.area_id_for(room)
        locks = self.store.settings.get("locks_and_doors", True) is True
        answer, result = await self.think(text, area_id, locks)
        agent = self.passon.target(self.store.settings.get("pass_to"))
        is_plan = isinstance(result, Plan)
        kept = None if is_plan else hold.held(answer, result, text, locks)
        answered = answer if isinstance(answer, Answer) else None
        return {
            "sentence": text,
            "outcome": DONE if is_plan else NOTHING if kept else PASSED,
            "label": LABEL_DONE if is_plan else LABEL_NOTHING if kept else LABEL_PASSED.format(
                agent=self.agent_name(agent)),
            "action": answered.action if answered else None,
            "kind": answered.kind if answered else None,
            "score": answered.similarity if answered else None,
            "entities": [self.home.name_of(entity_id) for entity_id in result.entity_ids] if is_plan else [],
            "intents": list(dict.fromkeys(intent for intent, _ in result.ha_calls())) if is_plan else [],
            "note": hold.REPLIES[kept] if kept else (
                result.detail if isinstance(result, HandBack) and result.detail in unsure.NOTES else ""),
            "short": hold.SHORT[kept] if kept else "",   # the page's headline for "Hunch would do nothing"
            # why it would do nothing (`hold.WHY`) or pass it on (`why.py`), in one plain sentence; "" when it
            # would act
            "why": "" if is_plan else hold.WHY[kept] if kept else why.plain(result, locks, self.agent_name(agent),
                                                                             answer),
            "ms": round((time.monotonic() - started) * 1000),
        }
