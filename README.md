# Hunch Home for Home Assistant

Hunch Home is a conversation agent for Home Assistant that understands home commands in your own words. Each
sentence you type or say to Assist goes to Hunch, a small model running on your own machine, which answers what
to do and what kind of thing it is about. The plug-in then finds the device among the entities you exposed to
Assist and carries the command out with Home Assistant's own built-in intents. Anything Hunch declines, or that
the plug-in cannot place safely, goes to Home Assistant's own agent, as if Hunch were not there. English only.

> **Locks and doors are on by default.** With **Locks and doors (experimental)** on, Hunch locks and unlocks
> locks and opens and closes doors, gates and garage doors, so a wrong answer moves a real lock or door, and a
> command that names none can reach every one in the room. To leave those to Home Assistant, switch it off when
> you set the plug-in up or later under **Reconfigure**. See [Locks and doors](#locks-and-doors).

## What you need

- Home Assistant 2026.11.0 or later, with an English assistant.
- A 64-bit machine: a Raspberry Pi 4 or 5 with a 64-bit system, or a PC (arm64 or amd64).
- About 550 MB of free memory for the Hunch server. Fine on 4 GB; tight on 2 GB with other things running.
- Home Assistant OS (or Supervised) for the server as an app, or Docker for the server beside Home Assistant
  Container.

No account, no token, no cloud. The model is inside the server and nothing is downloaded after the install.

## Install

There are two pieces. The model cannot run inside Home Assistant itself (Home Assistant's system has no build of
the library the model needs), so it runs in a small separate server on the same machine. This plug-in sends that
server each sentence and acts on the answer.

### 1. The Hunch Home server

**On Home Assistant OS**, the server is a Home Assistant app (what used to be called an add-on).

One click: [add the app repository to your Home Assistant](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fyungbose%2Fhunch-home-server).

Or by hand: **Settings > Apps > App store**, the three-dot menu, **Repositories**, and paste
`https://github.com/yungbose/hunch-home-server`. Install **Hunch Home server** and start it. It is ready when its
**Log** tab says `Hunch-1.1 Home typed build ready`. Your server address is `http://ea895109-hunch:8770`,
with no secret.

**On Home Assistant Container**, run the server image beside it. The
[server repository](https://github.com/yungbose/hunch-home-server) has a compose example; with plain Docker:

```sh
docker run -d --name hunch --restart unless-stopped \
  -p 127.0.0.1:8770:8770 -e HUNCH_NO_SECRET=1 \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  ghcr.io/yungbose/hunch-home-server:1.1.0
```

Your server address is `http://127.0.0.1:8770`, with no secret. This assumes Home Assistant runs on the host's
network, as its own installation guide sets it up. To run the server on another machine, it needs a secret; the
server repository explains how.

### 2. This plug-in

From Home Assistant 2026.11 the community store is built in, as the Marketplace.

One click: [open Hunch Home in your Marketplace](https://my.home-assistant.io/redirect/marketplace_repository/?owner=yungbose&repository=hunch-home&category=integration).
The first time you use the Marketplace, it shows its own warning about community plug-ins and asks you to
connect a GitHub account. Then download **Hunch Home** and restart Home Assistant.

Or by hand: copy `custom_components/hunch_home/` from this repository into the `custom_components/` folder of
your Home Assistant configuration folder, then restart.

## Setting it up

1. **Settings > Devices & services > Add integration**, and choose **Hunch Home**.
2. Fill in the form:

   | Field | What to enter |
   |---|---|
   | **Hunch server address** | `http://ea895109-hunch:8770` for the app, `http://127.0.0.1:8770` for Docker on the same machine. |
   | **Shared secret (optional)** | Leave it empty unless your server asks for one. See [The shared secret](#the-shared-secret). |
   | **Timeout** | Two seconds unless you have a reason to change it (0.2 to 30). A slower reply sends the sentence to Home Assistant's own agent. |
   | **Let Home Assistant's own sentences answer first** | On to start with. A sentence Home Assistant understands exactly (its built-in sentences and your sentence triggers) is carried out by Home Assistant, and Hunch is asked only about the rest. Switch it off to try Hunch first. |
   | **Locks and doors (experimental)** | On by default. See [Locks and doors](#locks-and-doors). |

3. **Settings > Voice assistants**: open an English assistant and choose **Hunch Home** as its conversation agent.
4. Under **Expose**, expose only the entities you want it to control. Give entities and areas useful names or
   aliases: the plug-in finds a device by its whole name or an alias. A scene must be a named entity.
5. Type "turn on the kitchen lights" in Assist.

You can change all five fields later with the integration's **Reconfigure**. The record and sharing are under
**Configure** (see [Your data](#your-data)).

## What it understands

For each sentence Hunch gives an action and a kind of thing, or "not for me". The kinds are Lights; Climate
(heating, cooling, blinds); Security (locks, doors, gates, garage doors, cameras, alarms); Speakers & TVs;
Timers and alarms; Scenes; Weather; Lists; Water; and Other device.

Answers from the released model, each asked once on 19 November 2026 (examples, not a measurement):

| You say | Hunch answers |
|---|---|
| turn on the kitchen lights | turn on / Lights |
| make it brighter in here | raise / Lights |
| set the heating to 21 | set temperature / Climate |
| turn the tv off | turn off / Speakers & TVs |
| play some jazz in the living room | turn on / Speakers & TVs |
| set a timer for ten minutes | turn on / Timers and alarms |
| add milk to my shopping list | add / Lists |
| what is the weather like | ask / Weather |
| open the garage door | open / Security |
| lock the side door | lock / Security |
| unlock all the doors | unlock / Security |
| i used to lock the door every night | not for me (right: it is not a command) |

What the plug-in can then do, by kind:

- **Lights:** on, off, a brightness, a colour, brighter and dimmer.
- **Climate:** thermostats on and off, a temperature; blinds, curtains and shutters open and closed.
- **Security:** locks; with Locks and doors on, also doors, gates and garage doors.
- **Speakers & TVs:** on, off, play something named, pause, resume, mute, volume, next, previous.
- **Timers and alarms:** start, cancel, check and change a simple timer, on an Assist satellite that has timers.
- **Scenes:** activate a named scene.
- **Weather:** the current weather.
- **Lists:** add an item to, or remove one from, a named to-do list.
- **Other device:** a named switch, fan or input boolean on and off; a vacuum started and sent home.
- **Water:** nothing yet; it goes to Home Assistant.

The exact rules are in [Reference: what runs](#reference-what-runs).

## How accurate it is

From version 1.1's published test: 474 real typed home commands in people's own words, gathered from public
posts and videos and held out for testing. "Right" means both answers right, the action and the kind.

| Result | Share of the 474 |
|---|---|
| Right | 70.9% |
| Wrong | 12.2% |
| Declined (handed back to Home Assistant) | 16.9% |

The previous version got 38.0% right.

By kind, on the same set:

| Kind | Right | Test sentences |
|---|---|---|
| Lights | 89.5% | 76 |
| Climate | 85.7% | 98 |
| Speakers & TVs | 64.9% | 94 |
| Security | 57.7% | 52 |
| Other device | 53.1% | 81 |

Timers and alarms, Scenes, Water, Weather and Lists have fewer than 30 test sentences each and are unmeasured.

Sentences that mention a device but are not commands ("I used to lock the door"): Hunch wrongly answers about
12 in 100 of them as a command.

What these figures do not say:

- They measure Hunch's two answers, not the whole plug-in. Whether the plug-in then finds the right device is
  not measured.
- The right answers in the test were set by two language models that agreed. No person checked them.
- They are typed sentences. A spoken command goes through Home Assistant's speech-to-text first, which the test
  does not include.

## What it gets wrong

Answers from the same session on 19 November 2026:

| You say | Hunch answers | What happens |
|---|---|---|
| open my front door | unlock / Security | With Locks and doors on, it unlocks a lock named front door. With no lock of that name, it unlocks every exposed lock in the room of the device that heard it. |
| close the garage door fifteen percent | set level / Climate | Wrong kind. The plug-in has no row for that pair and hands it back. |
| enable the lock | not for me | Handed back to Home Assistant. |
| control gate half | not for me | Handed back to Home Assistant. |

Other limits:

- In the test, about 1 in 8 commands got a wrong answer. The plug-in's checks catch some of those (see
  [When it hands back](#when-it-hands-back-to-home-assistant)); the rest act on the wrong thing or in the wrong
  way.
- One command per sentence. Two commands ("turn off the tv and turn on the lights"), a "not" or "don't", or a
  condition ("if", "unless") go to Home Assistant.
- It cannot delay anything. "Unlock the door in ten minutes" or "tonight" goes to Home Assistant; timers are
  the exception.
- Levels and temperatures must be digits. "Raise the lights to 80%" works; "raise them by 10%" goes to Home
  Assistant. Timer lengths also accept words from one to ninety-nine ("ten minutes").
- Vague media requests ("play some music"), weather forecasts, alarms, named timers, relative heating changes
  and Water commands go to Home Assistant.
- Scripts, buttons and automations are never run. A scene you name is activated as it stands, whatever it does.

## Locks and doors

**Locks and doors (experimental)** is one setting in the set-up and **Reconfigure** forms, on by default.

With it on, the plug-in locks and unlocks locks, opens and closes any cover you expose (doors, gates, garage
doors, skylights and motorised windows as well as blinds), and turns a gate or door wired as a switch on and
off when the sentence names that switch (never as part of a room).

A command that names no device the plug-in can find applies to a whole room: the room the sentence names, or
else the room of the device that heard it. So:

- "Unlock" with no lock's name in the sentence unlocks every exposed lock in that room. "Open my front door"
  answered unlock, in a home with no lock named "front door", does the same.
- "Open the blinds", in a room with no cover named "blinds", opens every exposed cover in that room. If the
  garage door is a cover in the same room, it opens too.

Each cover or switch found gets its own call by entity ID, so Home Assistant moves no cover the plug-in did not
find; an unlock for a room is one call that Home Assistant applies to the exposed locks in it. The only other
check is the one every command has: the sentence's own words must not contradict the answer ("lock the front
door" answered unlock goes to Home Assistant). It does not catch everything: "open my front door" answered
unlock passes it.

Security commands were 57.7% right in the test above. Before you rely on it: expose only the locks and doors
you want spoken commands to reach, keep them in rooms of their own in Home Assistant where you can, and try it
on your own doors.

With it off, the plug-in leaves unlocking, doors, gates, garage doors, motorised windows and entry switches to
Home Assistant's own sentences. It still locks locks and moves blinds, curtains and other window coverings. The
detail is under [Reference: what runs](#reference-what-runs). With it off, do not expose gate, door or garage
switches to Assist: the plug-in can refuse them only by name.

## Your data

Nothing leaves your computer unless you choose to share it to make Hunch better.

Each sentence goes to the Hunch server, which is on the same machine unless you put it on another. The server
never logs or stores a sentence or an answer, and makes no connection of its own. The plug-in keeps a record in
your own Home Assistant, and offers one separate choice to share daily counts, off unless you turn it on.

### The record

Hunch keeps a record of each sentence it handles and what it did, here in your Home Assistant, so you can see
what went wrong. The record is on by default; switch it off or clear it under the integration's **Configure**.

Each entry holds: when; the sentence; Hunch's action, kind and score, and the model's version when the server
gives one; what the plug-in did (the intents it fired and the entity IDs, or why it handed the sentence back,
with the detail of that reason); whether Home Assistant reported an error; and whether it was undone. A
sentence that Home Assistant's own sentences answered first is kept too, with no answer from Hunch.

**Undone** means that within 30 seconds of a command being carried out, something other than that command put a
device it changed back the other way: a person at the switch, someone in the app, an automation, or another
voice command. It is the cheapest honest sign that the plug-in did the wrong thing. On and off, lock and unlock,
open and close, pause and resume, and a brighter or dimmer step have an other way; a colour, a temperature, a
volume, a scene, a timer and the like do not, and are recorded as "not known" rather than guessed. The
plug-in's own calls in the same turn never count, nor does an automation the command itself set off directly.
An automation set off at one remove does count, as unavoidable noise. A device that flicks back and forth while
reporting counts only if it stays the other way for 3 seconds. If Hunch is asked about the same device again
within the window, that later command decides: "no, off" straight after a wrong "on" marks the first one
undone. The full rule is in `custom_components/hunch_home/hunch_logic/undo.py`.

The record keeps the latest 1,000 sentences, none older than 30 days, oldest dropped first: about 400 KB in a
private file under `.storage`, written by Home Assistant's own storage helper, at most once every ten seconds.
It survives restarts, and your Home Assistant backups hold it too, wherever you keep them. A device being
watched when Home Assistant stops is recorded as "not known". Deleting the integration's entry deletes the
record.

- **See it and export it:** **Developer tools > Actions**, choose **Hunch Home: Show the record**, and run it.
  The whole record, sentences included, is shown as YAML, ready to copy. The action is for administrators only.
- **Clear it:** **Configure > Clear the record**, or the action **Hunch Home: Clear the record**. It cannot be
  undone.
- **Switch it off:** **Configure > The record and sharing**, then **Keep a record**. Nothing new is kept; what is
  already kept stays until you clear it.
- **Diagnostics:** the integration's **Download diagnostics** file is made to be pasted into bug reports, so it
  leaves out the sentences, the hand-backs' details (which may name a device or a room), the entity IDs (cut to
  their domain, such as `light`), the server address and the secret. It shows the newest 50 entries that way.

Debug logging for the plug-in can include sentences, Hunch's answers, entity names and values. Think about what
will be written to Home Assistant's log before you turn it on.

### Sharing counts (your choice, off unless you turn it on)

**Share daily counts to make Hunch better** is a separate choice under **Configure > The record and sharing**.
It is off unless you turn it on, and Hunch Home works the same without it. The privacy notice is
[PRIVACY.md](PRIVACY.md).

Once a day it sends counts only, for the day before: how many sentences of each action and kind, what the
plug-in did with them (carried out, handed back and why, error), how many were undone, a rough spread of
scores, the plug-in's and the model's version numbers, and an install number made at random for this alone.
Never a sentence, a device or entity name, an entity ID, a room, or anything you typed. Every string in the
counts is a fixed word or a number; the plug-in checks that again before sending, and the receiving end checks
it a third time against the same word lists. The diagnostics file shows, under `sharing`, exactly what would be
sent for yesterday, whether sharing is on or off. The install number itself is left out of diagnostics, because
it is what deletes this home's counts; a short `install_reference` made from it stands in, and is the thing to
quote when writing to us.

Turning the choice off stops sending at once and deletes what this home sent: the plug-in asks the receiving end
to delete every day stored for its install number, and if it cannot reach it, tries again every hour until it
can (diagnostics show `deletion_owed` meanwhile). Nothing more is sent while a deletion is owed, even if sharing
is turned on again. Deleting the integration's entry while sharing is on asks for the deletion once before the
record, and with it the install number, is deleted; note your `install_reference` first in case that request
fails. The counts are kept for 24 months. [PRIVACY.md](PRIVACY.md) says who keeps them, why, and how to ask
about them: write to privacy@xevall.com, quoting the install reference.

## The shared secret

You need one only when the Hunch server can be reached from other machines on your network. The app and the
Docker command above need none.

**Shared secret (optional)** is a password field in the set-up and **Reconfigure** forms. When it is set, every
request carries `Authorization: Bearer <secret>`; when it is empty, no `Authorization` header is sent. The secret
is stored in the config entry, as Home Assistant stores every integration's credentials, and is never written
to the log, to a hand-back's reason or to the diagnostics file (which says only whether one is set). It may hold
letters, digits and symbols, with no spaces, up to 512 characters; the server asks for at least 16. Clearing the
field in **Reconfigure** removes it.

If the server turns a request away (HTTP 401), the sentence goes to Home Assistant's own agent like any other
server fault, and Hunch Home asks for attention under **Settings > Devices & services**. The form says **The
Hunch server needs a secret** when none is set, or **The Hunch server refused the secret** when the one set does
not match. Entering the secret there saves it and restarts the integration. Until then, every sentence goes to
Home Assistant's own agent.

## When it hands back to Home Assistant

These go to Home Assistant's built-in agent, with the sentence, conversation ID, context, language and
originating device passed through:

- "not for me", a declined answer, or a reply the plug-in cannot read;
- an action and kind with no row in the table below, a value it cannot read, a missing name, or more than one
  equally good match;
- a server that cannot be reached, answers with an error or takes longer than the **Timeout**;
- a sentence whose words disagree with the answer (below);
- a delayed command, more than one command, a negation or a condition;
- an intent error or a device that fails.

The hand-back always selects Home Assistant's built-in agent, so it cannot reach Hunch again even when Hunch is
the assistant's agent. A server fault logs one warning line; other hand-backs log at debug level.

The disagreement check is a small English whole-word check. It catches opposite directions such as "unlock"
answered as lock, "lock" answered as unlock (unless "unlock" is also said), "off", "kill", "stop", "disable" or
"out" answered as turn on, "start", "enable" or "resume" answered as turn off, "close" answered as open, "open"
answered as close, "dim", "dimmer" or "darker" answered as raise, and "brighter" or "brighten" answered as
lower. A turn on with "out" anywhere hands back, so "switch on the light out the back" goes to Home Assistant
too. Durations, clock times and delay words such as "tomorrow", "tonight", "later", "after", "soon", "in a bit",
"this evening", "at bedtime" and "when I leave" also hand back, except in the supported timer commands. These
deliberately limited checks do not prove that every other answer agrees with the sentence.

A device can partly act before failing. Home Assistant's own agent may then repeat that part.

## Reference: what runs

This section is for people who want the exact rules. The complete table is in
`custom_components/hunch_home/hunch_logic/table.py`. Only pairs listed there run; every other pair goes to Home
Assistant. Names and slots were checked against the
[Home Assistant intent list](https://github.com/OHF-Voice/intents/blob/main/intents.yaml) and the
[2026.11.0 handlers](https://github.com/home-assistant/core/tree/2026.11.0/homeassistant/components).

| Action | Kind | Intent and value slots |
|---|---|---|
| turn on / turn off | Lights | `HassTurnOn` / `HassTurnOff`, `domain: [light]`, or a switch named by a name that says lamp or light (`domain: [switch]`) |
| set level | Lights | `HassLightSet`, needs a value: `brightness` from 0 to 100 |
| raise / lower | Lights | With a brightness in the sentence, `HassLightSet` as for set level. With none, one call per light: `HassLightSet` 25 points up or down (never below 10%), or `HassTurnOn` / `HassTurnOff` for a light that cannot dim or a named lamp switch |
| set colour | Lights | `HassLightSet`, needs a value: `color` |
| turn on / turn off | Climate | `HassTurnOn` / `HassTurnOff`, `domain: [climate]` |
| set temperature | Climate | `HassClimateSetTemperature`, needs a value: `temperature` |
| lock / unlock | Security | `HassTurnOn` / `HassTurnOff`, `domain: [lock]` |
| open / close | Climate or Security | `HassTurnOn` / `HassTurnOff`, one call per entity, any cover (`domain: [cover]`) or a named switch (`domain: [switch]`) |
| turn on / turn off | Scenes | `HassTurnOn` for a named scene; turn off hands back |
| turn on / turn off | Speakers & TVs | `HassTurnOn` / `HassTurnOff`, `domain: [media_player]` |
| turn on, with unmute / resume / play | Speakers & TVs | `HassMediaPlayerUnmute` / `HassMediaUnpause` / `HassMediaSearchAndPlay`; play needs a value: `search_query` |
| turn off, with mute / pause / stop | Speakers & TVs | `HassMediaPlayerMute` / `HassMediaPause` |
| set level / raise / lower | Speakers & TVs | `HassSetVolume` (`volume_level`) / `HassSetVolumeRelative` (`volume_step`) |
| next / previous | Speakers & TVs | `HassMediaNext` / `HassMediaPrevious` |
| ask | Weather | `HassGetWeather`, current weather only |
| add / remove | Lists | `HassListAddItem` / `HassListRemoveItem`, needs a value: `item`, and a named to-do list |
| turn on / turn off / remove | Timers and alarms | `HassStartTimer` (needs a value: `hours`, `minutes`, `seconds`) / `HassCancelTimer` |
| ask / raise / lower | Timers and alarms | `HassTimerStatus` / `HassIncreaseTimer` / `HassDecreaseTimer`; changes need a duration |
| turn on / turn off | Other device | `HassTurnOn` / `HassTurnOff` for a named switch, fan or input boolean; a vacuum uses `HassVacuumStart` / `HassVacuumReturnToBase` |
| announce | Any known kind | `HassBroadcast`, needs a value: `message` |

**Finding the device.** `name` and, where supported, `area` slots select the target. The plug-in searches only
exposed entities of the row's domains. Whole names or aliases count, ignoring case and punctuation. A longer
name at the same position wins, so Desk Lamp wins over Lamp. Different devices named separately, or equal
matches that the named room or the originating device's room cannot settle, hand back. A named room without a
device selects the domain in that room where the table permits it. No room means the originating device's area,
never the whole home. Home Assistant does its own matching, exposure and feature checks again when it handles
the intent.

**Lamps on smart plugs.** A lamp on a smart plug is a switch to Home Assistant. A Lights command reaches it only
when the sentence names it by a name that says lamp or light ("Reading lamp"); a room command never does, since
a switch in a room may be a kettle or a fan. A name that begins with "the" is also found without it: "lamp off"
finds "the lamp".

**Brighter and dimmer.** With no level, each light moves 25 points from its present level: an off light counts
as 0, dimming stops at 10% and leaves an off light off, and a light that cannot dim is switched on or off
instead. A light whose level Home Assistant does not report hands the sentence back.

**Values.** Levels and temperatures must be digits, with an optional percentage or temperature unit; no number
words or ranges. Explicit Celsius or Fahrenheit is converted to the home's unit. Temperatures must be positive
and at most 35 Celsius or 95 Fahrenheit after conversion; higher values hand back. Colour names are a short list
in `custom_components/hunch_home/hunch_logic/values.py`; two colours hand back. Add reads the words after "add"
or "put" up to "to my", "to the", "on my", "on the" or "onto"; remove uses "from" or "off". A list must be
named as well as an item. Media search reads the words after "play" up to "on" or "in"; vague requests such as
"play some music" hand back. These are limited English parsers, not another language model.

**Timers and weather.** Timer lengths also accept English number words from one to ninety-nine, plus "an hour"
and "half an hour". A timer must say "timer", be unnamed and have no room or delayed command. Alarms, named
timers and selectors such as "the 10 minute timer" hand back. Timer and broadcast intents need an Assist
satellite that supports them. Weather needs one exposed weather entity or a matching name, and does not answer
tomorrow's forecast. Water, questions about a device's state, and relative heating changes hand back. Named list
or weather entities that need a room to tell them apart are passed by entity ID, since their intents take no
`area` slot.

**Scenes.** A scene you name is activated as it stands, whatever it contains. Scripts, buttons and automations
are never run by this plug-in.

**With Locks and doors off.** Unlocking is left to Home Assistant's own sentences. Covers are operated only when
their device class is `blind`, `curtain`, `shade`, `shutter` or `awning`. Doors, gates, garage doors, dampers and
covers with no known class hand back. So does a cover of class `window`: in Home Assistant that is a motorised
window or skylight, an opening into the home rather than something that covers one. A room-wide cover request
selects only these window coverings and sends each entity ID separately, with the same allowed `device_class`
list for Home Assistant to check again. It never sends a command to the room's whole cover domain.

Gate, door and garage openers are often wired as switches, buttons or scripts, where any "turn on" moves them.
The plug-in refuses them by name only: an Other device command, or a turn on or turn off that reaches a switch,
scene, script, button or input boolean, hands back when the sentence, the device's name or alias, or its entity
ID holds door, gate, garage, opener, latch, lock, bolt, entry, entrance, shutter, buzzer or intercom (with
plurals). A gate called "Front Barrier" would not be caught. Any turn on or turn off whose sentence says open,
close, shut, lock or unlock also hands back, since those rows never open, close or lock anything; "shut off" and
"shut down" answered turn off still act. A scene that opens a door, a gate or a garage ("Leaving", "Arrive
home") is left to Home Assistant when its name or entity ID holds an entry word (for example
`scene.garage_arrive`).

## What has been tried, and what has not

Tried:

- Home Assistant 2026.11.2 in Docker on a Raspberry Pi 5, with a real lamp.
- Home Assistant OS 18.4 in a virtual machine, with the server as an app: set up through the normal form, four
  sentences carried out, the shared secret, the record, and sharing switched on and off.

Not tried yet: a PC (amd64) at home, a Home Assistant Green or a Raspberry Pi 4, colour bulbs, rooms of mixed
lights, real locks, covers, media players, and timers through a satellite. Most of what the reference table lists
has been checked against Home Assistant's code and with stand-in devices in automated tests, not on real
devices. Try it on devices that do no harm first, and please report what breaks at
[github.com/yungbose/hunch-home/issues](https://github.com/yungbose/hunch-home/issues).

## Removing it and rolling back

1. Under **Settings > Voice assistants**, choose Home Assistant again as the assistant's conversation agent.
2. Delete the Hunch Home entry under **Settings > Devices & services**. This deletes the record. If sharing was
   on, it asks once for this home's counts to be deleted (see [Sharing counts](#sharing-counts-your-choice-off-unless-you-turn-it-on)).
3. Remove the plug-in from the Marketplace, or delete `custom_components/hunch_home/`, then restart Home
   Assistant.
4. Uninstall the Hunch Home server app, or stop and remove the `hunch` container. Removing the plug-in does not
   touch the server.

To roll back an update installed by hand, put the previous `custom_components/hunch_home/` folder back and
restart.

## Help and bugs

Open an issue at [github.com/yungbose/hunch-home/issues](https://github.com/yungbose/hunch-home/issues). Attach
the integration's **Download diagnostics** file: it leaves out your sentences, names, the server address and
the secret. Problems with the server itself go to
[github.com/yungbose/hunch-home-server/issues](https://github.com/yungbose/hunch-home-server/issues).

## Licence

The plug-in's code is Apache 2.0 ([LICENSE](LICENSE)). The model inside the server is CC BY-NC 4.0: free at
home, for research and for other non-commercial use. A company that wants to use it commercially needs
permission from xevall: licensing@xevall.com.

Made by [xevall](https://xevall.com). The model's page:
[huggingface.co/xevall/hunch-1-home](https://huggingface.co/xevall/hunch-1-home).
