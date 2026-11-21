![A dithered mascot juggling a lamp, a kettle, a clock, a bulb and a radio beside a drawing of a house, with the title Hunch Home](images/banner.png)

# Hunch Home reference

What to look up after the [read-me](README.md). Docker has its own page, [DOCKER.md](DOCKER.md).

## The settings on the page

| Setting | Default | What it does |
|---|---|---|
| Locks and doors | On | Unlocking a named lock, and opening and closing a named door, gate or garage door. |
| Pass on to | The agent your assistant had before, or Home Assistant | Where sentences that are not home commands go. |
| Keep a record | On | The record on this machine. Off: nothing new is kept. |
| Share daily counts | Off | The daily counts ([PRIVACY.md](PRIVACY.md)). |
| Listen with no wake word | Off | Experimental; see [below](#switching-on-listening). |
| Microphone's room | None | Where a heard command that names no room acts. |

Each change is saved at once. The app's **Configuration** tab has no options of its own, apart from **Audio** for
listening.

**Restarts.** While the app restarts, Assist may say "Error communicating with service" for a second or two.
And for about half a minute after Home Assistant itself restarts, Assist answers nothing: that is Home Assistant
starting, and Hunch answers again once it has.

## Switching on listening

1. Plug a USB microphone into the Home Assistant machine (Home Assistant's own voice devices do not work for
   this). On the app's **Configuration** tab, under **Audio**, choose it as the input, save, and restart the app.
   In Docker, give the container the microphone as [compose.example.yaml](compose.example.yaml) shows.
2. On the app's page, under **Settings**, switch on **Listen with no wake word** and set **Microphone's room**.
3. The first time, press **Fetch it and switch on**. The app downloads its speech model, Parakeet TDT 0.6B v2,
   once from GitHub (about 482 MB, about 661 MB on the machine), and unpacking takes a few minutes.

It needs about 0.9 GB more memory while it is on. Switching it off closes the microphone at once.

## What the app may do in Home Assistant

The app has an administrator's access to Home Assistant, which Home Assistant gives every app that asks for its
API. It uses it to read your devices and rooms, to switch the devices you expose to Assist through Assist's own
commands, and to set up an assistant when you press the button.

**Add Hunch to my assistant** changes two settings of the assistant you choose, and nothing else: Hunch becomes
its conversation agent, and **Prefer handling commands locally** is switched on, so Home Assistant's own
sentences still answer first. With its box ticked, it also puts the app in the sidebar. **Stop using Hunch** puts
both settings back. For listening, the app reads the description of the microphone you chose from the
Supervisor, so the page can name it.

Hunch holds no key and calls no outside service. A sentence it passes on goes through your Home Assistant to the
agent you chose, as if you had asked that agent yourself.

## Trying the page without Home Assistant

From a copy of this repository, with the packages in `requirements.lock` installed: `python -m hunch_app --demo`,
then open http://127.0.0.1:8099/. The real app and page run against a stand-in Home Assistant with a made-up
home, and a stand-in model that knows a few sentences. Nothing leaves the machine. On the real app, **Try a
sentence** shows what Hunch would do with any sentence, and moves nothing.

## What has been tried, and what has not

Tried in Home Assistant OS 18.4 with Home Assistant 2026.11.2 in a virtual machine, with the real model, on
stand-in devices: the app found under **Discovered** and added; the button and **Stop using Hunch**; typed
sentences carried out with Home Assistant's exposure check, and a device hidden from Assist left alone; the lock
and door rules on stand-in locks and covers; sentences passed on to Home Assistant's own agent; a device that did
not respond, the app stopped and started, Home Assistant restarted; uninstalling, with and without **Stop using
Hunch**. Listening was tried there with a simulated microphone playing computer-made speech: a command was carried
out, ordinary talk left no trace in the record, the log or Home Assistant, "unlock the front door" and "open the
garage door" did nothing, and "open the blinds" moved only the blinds in the microphone's room. Also tried: the
app's code run on a Mac with an access token, against Home Assistant OS in a virtual machine, where a sentence
switched a light.

On a Raspberry Pi 5 with Home Assistant in Docker, on 20 November 2026, the app was built there, ran beside Home
Assistant, and switched a real lamp from real sentences in one informal session (about a quarter of a second
through the whole assistant as a middle value, not a measurement). With a real USB microphone, listening sat in
an empty room for twenty minutes and heard, kept and moved nothing. That evening, commands played from a laptop's
speaker about a metre away, in computer voices and recordings of the owner's own voice, switched the lamp for 43
of 83, each time the right lamp; the rest did nothing, most often because the speech-to-text wrote "lamp" as
"lamb", "land" or "man". In about six minutes of ordinary talk, one sentence ("she switched the lamp on and
started reading") switched the lamp, in two voices; nothing else did. Commands for a later time, a "do not", and
a command with a second voice talking over it did nothing. This is not a measurement, and no person spoke to it
live.

Installing from this repository was tried on 21 Nov 2026 in a Home Assistant OS test machine (64-bit Arm): Home
Assistant pulled the published image in under two minutes, the app was ready 16 seconds after Start, and set-up
and a first command worked. The published image was run the same day in Docker on a Raspberry Pi 5
beside Home Assistant Container, as [DOCKER.md](DOCKER.md) says: it was ready in under ten seconds and typed
commands switched a real lamp.

Not tried yet: a PC (amd64) at home, Home Assistant OS on a Raspberry Pi, a Home Assistant Green, real locks,
covers and speakers, voice through a voice device, a person speaking to listening live, and timers. Nor has a
sentence passed on to a language model connected through Home Assistant (an OpenAI, Gemini, Anthropic or Ollama
agent): only Home Assistant's own agent has been tried. It should work the same way, since the app hands the
sentence to whichever agent you choose under **Pass on to**. The short lines Hunch gives when it does not carry
out a home command have been tried against a stand-in Home Assistant only. Most of the app's behaviour has been checked against
Home Assistant's code and stand-in devices in automated tests, not on real devices. Try it on devices that do no
harm first, and please report what breaks.
