"""The plug-in's logic, kept apart from Home Assistant.

Nothing in this package imports `homeassistant` (or `aiohttp`), so it can be tested on its own. The thin glue
beside it (`__init__.py`, `config_flow.py`, `conversation.py`, `homeview.py`) turns Home Assistant's objects
into the plain records used here and carries out the plan these modules return.

- `client`: the request to the Hunch server's `POST /api/home` (with the shared secret when one is set), and
  reading its reply.
- `table`: from Hunch's two answers (what to do, what kind of thing) to a Home Assistant intent.
- `values`: small, plainly limited readers for a value Hunch does not return (a number, a colour, an item).
- `match`: finding the device or room in the sentence among the home's own names.
- `decide`: the whole decision, a plan to run or a reason to hand the sentence back.
- `speech`: what the plug-in says when Home Assistant's intent leaves the reply empty.
- `settings`: URL, timeout, secret, preference and option validation without Home Assistant.
- `record`: the local record of each sentence and what became of it, bounded, and its two views (whole for
  the user, redacted for diagnostics).
- `undo`: the rule for "undone", and the tracker that applies it to state changes.
- `summary`: the daily counts with no words in them.
- `share`: sending those counts, only if the owner chose to, and never while there is no address to send to.
"""
