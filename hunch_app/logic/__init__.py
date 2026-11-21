"""The rules, kept apart from Home Assistant and from the app's wiring.

Copied from the plug-in's `hunch_logic/` as of plug-in 0.1.1 and owned here from now on; the plug-in's copy is
frozen. Nothing in this package imports `homeassistant`, `aiohttp` or the model, so it can be tested on its own.
The app's modules beside it (`home.py`, `act.py`, `turn.py`, `store.py`, `counts.py`) turn Home Assistant's
replies into the plain records used here and carry out the plan these modules return.

- `client`: Hunch's answer and a hand-back, and reading an answer dict (the request to a separate server is
  gone: the model runs in the app).
- `table`: from Hunch's two answers (what to do, what kind of thing) to a Home Assistant intent.
- `values`: small, plainly limited readers for a value Hunch does not return (a number, a colour, an item).
- `match`: finding the device or room in the sentence among the home's own names.
- `decide`: the whole decision, a plan to run or a reason to hand the sentence back.
- `speech`: what is said when Home Assistant's intent leaves the reply empty.
- `record`: the local record of each sentence and what became of it, bounded, and its two views (whole for
  the user, redacted for diagnostics).
- `undo`: the rule for "undone", and the tracker that applies it to state changes.
- `summary`: the daily counts with no words in them.
- `share`: sending those counts, only if the owner chose to, and never while there is no address to send to.

Dropped from the plug-in's copy: `settings.py` (the set-up and options forms' validation; the app validates its
own settings in `store.py`) and, in `client.py`, the request to the server and its shared secret.
"""
