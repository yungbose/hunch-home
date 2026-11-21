"""Hunch Home, the one app: Hunch's model, its rules and a Wyoming conversation agent in one process.

`python -m hunch_app` starts it (`__main__.py`). The modules, in the order a sentence meets them:

- `agent`: the Wyoming server Home Assistant talks to, and the discovery message that offers it.
- `turn`: one sentence in, exactly one outcome out (done, passed, ignored, error).
- `model`: the model in this process; `answer(text)` gives what the rules' `decide` takes.
- `home`: the rules' `Home`, built from Home Assistant's exposed list, registries and states.
- `logic/`: the rules, copied from plug-in 0.1.1.
- `act`: carries a plan out through `POST /api/intent/handle`; the only place a device is switched.
- `passon`: passes a declined sentence to the agent the owner chose.
- `store`: settings, the record and the install number, on disk.
- `counts`: the daily counts, off unless the owner turns them on.
- `ha`: the Home Assistant client (REST and websocket).
- `app`: puts them together; `api` is what the page's server may call.
"""

import os

# ONNX Runtime's official builds send trace events to Microsoft unless this is set before it first loads
# (found 19 Nov 2026 for the server). Hunch sends nothing anywhere of its own doing.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

__version__ = "0.2.0"
NAME = "Hunch Home"
