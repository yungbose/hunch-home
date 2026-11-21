"""The words and settings the app and its page share.

The page's server (`web.py`) calls the app through `web.Backend`, and the shapes inside each reply are the ones
written in that protocol's docstring (a ruling of 20 Nov 2026: the page's side wins, the app is changed
to match). `hunch_app.app.App` implements it; `web.StandIn` is the page's own stand-in.

| Page address | `App` method |
|---|---|
| `GET api/state` | `state()` |
| `GET api/events` | `events()` |
| `GET api/record?limit=` | `record(limit)` |
| `POST api/try` `{text, room?}` | `try_sentence(text, room)` |
| `POST api/settings` | `update_settings(changes)` |
| `POST api/record/clear` | `clear_record()` |
| `POST api/assistant/setup` `{pipeline_id, sidebar}` | `setup_assistant(pipeline_id, sidebar)` (`assistant.py`) |
| `POST api/assistant/undo` `{pipeline_id}` | `undo_assistant(pipeline_id)` (`assistant.py`) |
| `POST api/teach` `{sentence, action, kind}` | `teach(...)` (`teach.py`) |
| `GET api/taught` | `taught()` (`teach.py`) |
| `DELETE api/taught/<id>` | `forget(taught_id)` (`teach.py`) |
| `GET api/data` | `data()` (`data.py`) |
| `GET api/record/download?kind=full` or `report` | `download(kind)` (`data.py`) |

`NotYet` reaches the page as a plain "not yet" answer (HTTP 501), for a part not built.

`state()["listening"]` (`listen.Listening.status`) is `{available, status, problem, tried, microphone,
microphone_name, chosen, model, memory}`: `status` is "off", "needs model" (off, and the speech model has not been
fetched), "fetching", "starting", "on" or "could not start" (with `problem`, one plain sentence); `tried` is when
the microphone was last tried (ISO 8601) while it is not answering and Hunch keeps trying, else None; `microphone` is the
input's id and `microphone_name` its description (from the Supervisor); `chosen` whether the owner chose it in
the app's Audio setting (None outside the Supervisor); `model` is {`ready`, `name`, `maker`, `licence`, `source`,
`url`, `download_mb`, `disk_mb`, `needs_disk_mb`, `progress`: {`stage` "fetching" | "unpacking", `done_mb`,
`total_mb`, `fraction`} | None}; `memory` is {`free_mb` | None, `needed_mb`, `warning` (plain words, or "")}.
`update_settings({"listening": true})` fetches the speech model first when it is not here: the page asks the
owner before it sends that. The setting reads true only once the model is here.

`state()["teach"]` is `{actions, kinds, can_decline, notice}`: what the model in use can be taught; whether a
sentence can be taught as not a home command (`{action: "not for me", kind: "none"}`); and a plain note, or "",
when the kept examples could not all be read or used. Each taught example is `{id, sentence, action, kind, time,
active}`; `active` is false for one the model in use could not take (it is kept, and used again when it can).

Every message that can reach the owner is plain words: no setting's key, no code word, no Python name.
"""

from __future__ import annotations

# The five outcomes a sentence can have (`turn.py`), and the label the page and the record show for each.
DONE, PASSED, IGNORED, ERROR, NOTHING = "done", "passed", "ignored", "error", "nothing"
OUTCOMES = (DONE, PASSED, IGNORED, ERROR, NOTHING)
LABEL_DONE = "Hunch did it"
LABEL_NOTHING = "Hunch did nothing"            # a command Hunch would only be guessing at (`hold.py`)
LABEL_PASSED = "Passed to {agent}"            # the agent's name as Home Assistant lists it
LABEL_IGNORED = "Ignored"
LABEL_ERROR = "Could not reach {agent}"

# Where a sentence came from.
ASSIST, LISTENING = "assist", "listening"

# Event kinds on the live stream (each event's `event` field).
EVENT_TURN = "turn"        # one sentence and its outcome
EVENT_HEARING = "hearing"  # listening mode: speech started
EVENT_QUIET = "quiet"      # listening mode: speech ended and deciding begins (`speech` false: it was only a noise)
EVENT_HOME = "home"        # the home changed: {"event": "home", "home": <state()["home"]>}

# Settings: name, default. The names are `web.SETTINGS`'s; `update_settings` takes any subset.
SETTINGS_DEFAULTS = {
    "locks_and_doors": True,       # bool: unlocking, and opening and closing doors, gates and garage doors
    "pass_to": "conversation.home_assistant",  # str: the agent a declined sentence goes to (never Hunch's own)
    "keep_record": True,           # bool: keep the record of turns on this machine
    "share_counts": False,         # bool: send the daily counts (no words in them); off unless turned on
    "listening": False,            # bool: listening with no wake word (experimental; `listen/`)
    "mic_room": None,              # str | None: the room the microphone is in, by its name (or area id)
}

# The settings' names as the page shows them, for messages.
SETTING_NAMES = {
    "locks_and_doors": "Locks and doors",
    "pass_to": "Pass on to",
    "keep_record": "Keep a record",
    "share_counts": "Share daily counts",
    "listening": "Listen with no wake word",
    "mic_room": "Microphone's room",
}

# The privacy notice, PRIVACY.md in the app's public repository: the only place this address is written. The
# counts (`counts.PRIVACY_NOTICE`) and the page's state (`privacy_url`) take it from here; the page's own link
# (page/index.html) is replaced by `privacy_url` when the page loads, and a test keeps the two the same.
PRIVACY_URL = "https://github.com/yungbose/hunch-home/blob/main/PRIVACY.md"


class NotYet(NotImplementedError):
    """The part of the app this call needs is not built yet. The message says so in plain words."""


# `store.check_settings`'s short reasons, and the sentence the owner reads for each.
PLAIN = {
    "not a set of settings": "Hunch could not read those settings.",
    "no such setting": "There is no such setting.",
    "not on or off": "{setting} is either on or off.",
    "not an agent": "That is not an agent in Home Assistant.",
    "not a room": "That room is not in this home.",
    "no such agent": "That agent is not in this Home Assistant.",
    "that is Hunch itself": "Hunch cannot pass sentences on to itself. Choose another agent.",
}


def plain(name: str, reason: str) -> str:
    """The sentence the owner reads for a refused setting."""
    return PLAIN.get(reason, "That setting could not be saved.").format(setting=SETTING_NAMES.get(name, "It"))


class SettingsError(ValueError):
    """Settings that cannot be saved; nothing was saved. `errors` maps each refused setting's name to its short
    reason; the message is the plain sentences the owner reads (`plain`)."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__(" ".join(dict.fromkeys(plain(name, why) for name, why in errors.items())))
        self.errors = dict(errors)
