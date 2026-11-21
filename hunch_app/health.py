"""The image's health check, without a network connection: a heartbeat file the app touches while it serves.

The check used to open a connection to the Wyoming port. Under the Supervisor that port answers only Home
Assistant's address, so the check's own connection from 127.0.0.1 was refused and logged as a warning at every
start (the test machine, 20 Nov 2026, image checks 3 and 12). The allow-list is right to refuse it, and stays
exactly as strict.

Now, while the Wyoming server is serving, the app touches `HUNCH_HEALTH_FILE` (`/tmp/hunch-home.alive` by
default, inside the container) every `EVERY` seconds, and removes it when it stops or the server ends. The check
(`python -m hunch_app health`) reads only the file's time: healthy when it was touched within `STALE` seconds.
No port is opened, so nothing outside the container can ask, and nothing is logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path

LOG = logging.getLogger("hunch_app.health")

DEFAULT_FILE = "/tmp/hunch-home.alive"   # inside the app's own container
EVERY = 10.0    # seconds between beats
STALE = 45.0    # seconds after which a beat no longer counts (the image checks every 30 s)


def beat_file(env: Mapping[str, str]) -> Path:
    return Path((env.get("HUNCH_HEALTH_FILE") or DEFAULT_FILE).strip())


def healthy(path: Path, now: float | None = None, stale: float = STALE) -> bool:
    """Whether the app touched `path` within the last `stale` seconds."""
    try:
        touched = path.stat().st_mtime
    except OSError:
        return False
    return (time.time() if now is None else now) - touched <= stale


def _touch(path: Path) -> None:
    path.touch(exist_ok=True)
    os.utime(path)


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


async def keep_alive(path: Path, serving: Callable[[], bool], every: float = EVERY) -> None:
    """Touch `path` every `every` seconds while `serving()` is true; remove it when it is not, and when
    cancelled. A file that cannot be written is said once in the log, and the check then reports the app
    unhealthy."""
    said = False
    try:
        while True:
            if serving():
                try:
                    _touch(path)
                    said = False
                except OSError as error:
                    if not said:
                        said = True
                        LOG.warning("could not write the health check's file (%s)", type(error).__name__)
            else:
                _remove(path)
            await asyncio.sleep(every)
    finally:
        _remove(path)
