"""Was it undone within 30 seconds? The rules' tracker (`logic/undo.py`) fed from the websocket's state changes.

The rules tell the plan's own changes from everyone else's by Home Assistant's context: the plug-in fired its
intents under the sentence's context, so every change they made carried that context's id, and an automation
they set off carried it as its parent. The app cannot do that. An intent sent through `POST /api/intent/handle`
runs under a new context Home Assistant makes for the request, and the reply does not say which (seen in the
test machine, 20 Nov 2026, check 9). What a change does carry is the user who made it: the app's own user (the
"Supervisor" system user under the Supervisor; the token's owner in Docker), where an automation carries none
and a person their own.

So the app recognises its own changes this way, the most exact it can be from what Home Assistant gives:

1. Each turn gets a context id of its own, made up by the app (`hunch:<turn id>`), which the tracker uses.
2. While an intent call of a turn is in flight, and for `GRACE` seconds after its reply, a change to one of the
   devices that turn acts on, made by the app's own user, is the turn's own. Its real context id is learnt.
3. From then on, any change whose context id, or whose parent's, is a learnt one is also the turn's own: the
   device's later reports under the same context, and an automation the change set off directly.
4. Every change passed to the tracker carries the turn's made-up id in place of a learnt real one.

The app's own user is asked of Home Assistant (`auth/current_user`) once connected. Until it is known, rule 2
takes any user.

What this gets wrong:

- A change to the same device, by the same Home Assistant user, inside that short window is taken as the app's
  own. Under the Supervisor every app that talks through the Supervisor's proxy is that same user; in Docker the
  token's owner is a person who may also be tapping the same device in the Home Assistant app at that moment.
  Such a change is then never counted as an undo. The window is the length of the call plus `GRACE`.
- A change by the app's own user that is not recognised as the turn's own (another app under the Supervisor, or
  a sentence the app passed on to another agent, which acts under the app's user) counts as undone "by someone
  signed in to Home Assistant", the rules' word for a person in the app, a dashboard or typed Assist.
- A device that reports the turn's change late, after the window and under a new context, is not recognised;
  it goes the plan's way, so it never counts as an undo, but a bounce back after it would.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

from .logic import undo
from .logic.decide import Plan

GRACE = 1.0      # seconds after an intent call's reply in which its devices' changes are still its own
LEARNT = 2000    # real context ids remembered


def context_for(turn_id: str) -> str:
    return f"hunch:{turn_id}"


class Undone:
    """The tracker, the recogniser of the app's own changes, and the verdicts as they fall due."""

    def __init__(self, settle: Callable[[undo.Verdict], None], clock: Callable[[], float] = time.monotonic) -> None:
        self.tracker = undo.Tracker()
        self.settle = settle
        self.clock = clock
        self.app_user: str | None = None
        self._calls: dict[str, tuple[frozenset[str], float | None]] = {}   # made-up id -> (entities, ended at)
        self._learnt: OrderedDict[str, str] = OrderedDict()                # real context id -> made-up id
        self._watched: dict[str, set[str]] = {}                              # entity -> keys watching it

    # Around an intent call.

    def before(self, turn_id: str, plan: Plan) -> None:
        """A turn is about to act on these devices: from now on it speaks for them, and their changes by the
        app's own user are its own."""
        ours = context_for(turn_id)
        self.tracker.turn(ours, plan.entity_ids)
        self._calls[ours] = (frozenset(plan.entity_ids), None)

    def after(self, turn_id: str) -> None:
        """The turn's calls have their replies; its window closes `GRACE` seconds from now."""
        ours = context_for(turn_id)
        if ours in self._calls:
            self._calls[ours] = (self._calls[ours][0], self.clock())

    def not_carried_out(self, turn_id: str) -> None:
        self.tracker.release(context_for(turn_id))
        self._calls.pop(context_for(turn_id), None)

    def watch(self, turn_id: str, plan: Plan, before: dict[str, undo.Seen], states: dict[str, str]) -> None:
        """Start watching a carried-out plan for `undo.WINDOW` seconds."""
        verdict = self.tracker.start(turn_id, context_for(turn_id), undo.watches_for(plan, before), self.clock(),
                                     states)
        if verdict is not None:
            self.settle(verdict)
            return
        for entity_id in plan.entity_ids:
            self._watched.setdefault(entity_id, set()).add(turn_id)

    # The events.

    def _own(self, entity_id: str, context: dict) -> str | None:
        """The made-up id of the turn whose own change this is, or None."""
        real, parent = context.get("id"), context.get("parent_id")
        for known in (real, parent):
            if known and known in self._learnt:
                return self._learnt[known]
        now = self.clock()
        # Newest first: when two turns act on one device moments apart, its change is the later turn's.
        for ours, (entities, ended) in reversed(list(self._calls.items())):
            if ended is not None and now > ended + GRACE:
                del self._calls[ours]
                continue
            user = context.get("user_id")
            if entity_id in entities and user and (self.app_user is None or user == self.app_user):
                if real:
                    self._learnt[real] = ours
                    while len(self._learnt) > LEARNT:
                        self._learnt.popitem(last=False)
                return ours
        return None

    def changed(self, event: dict) -> None:
        """One `state_changed` event from the websocket."""
        data = event.get("data") or {}
        entity_id, new = data.get("entity_id"), data.get("new_state")
        if not isinstance(entity_id, str) or not isinstance(new, dict):
            return
        context = event.get("context") or {}
        ours = self._own(entity_id, context)
        keys = self._watched.get(entity_id)
        if not keys:
            return
        context_id = ours or context.get("id")
        parent_id = context.get("parent_id")
        if parent_id in self._learnt:
            parent_id = self._learnt[parent_id]
        seen = undo.seen_from(entity_id, str(new.get("state")), new.get("attributes") or {})
        for key in list(keys):
            self.tracker.change(key, entity_id, seen, context_id, parent_id, context.get("user_id"), self.clock())
        self.due()

    def due(self) -> None:
        """Settle every verdict that has fallen due."""
        for verdict in self.tracker.due(self.clock()):
            for keys in self._watched.values():
                keys.discard(verdict.key)
            self.settle(verdict)
        self._watched = {entity_id: keys for entity_id, keys in self._watched.items() if keys}

    def next_due(self) -> float | None:
        return self.tracker.next_due()

    def stop(self) -> None:
        for verdict in self.tracker.stop():
            self.settle(verdict)
        self._watched.clear()
        self._calls.clear()
