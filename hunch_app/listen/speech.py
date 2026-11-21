"""The speech model listening uses: fetched once, on the owner's word, into the app's data folder; never in the image.

Parakeet TDT 0.6B v2 is NVIDIA's English speech-to-text model (CC BY 4.0), here in the 8-bit form the sherpa-onnx
project publishes, the same file Home Assistant's own Parakeet speech-to-text uses. When the owner first switches
listening on and confirms, the app:

1. checks there is room on the disk for the archive and what it unpacks to (`DISK_NEEDED`);
2. downloads `URL` into `<data>/speech/` as it comes, working out its SHA-256 on the way;
3. refuses (and deletes) a file whose size or SHA-256 is not the pinned one (`ARCHIVE_SHA256`, worked out once
   from the real file on 20 Nov 2026);
4. unpacks only the four files listening reads, checks each against its own pinned SHA-256, and moves the folder
   into place in one step, so a half-unpacked model is never used;
5. deletes the archive and writes `ready.json`, which is how a restart knows not to fetch it again.

This is the only thing the app ever fetches. Nothing here runs unless the owner asks for it on the page.
"""

from __future__ import annotations

import asyncio
import bz2
import contextlib
import hashlib
import json
import logging
import os
import shutil
import tarfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

LOG = logging.getLogger("hunch_app.listen")

NAME = "Parakeet TDT 0.6B v2"
MAKER = "NVIDIA"
LICENCE = "CC BY 4.0"
SOURCE = "GitHub (the sherpa-onnx project's releases)"
URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
       "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2")
ARCHIVE_BYTES = 482_440_461
ARCHIVE_SHA256 = "6fc4426b54aa549a0019b8a75b8dea59164e71790a37ac56a045ad57f915f714"
INSIDE = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
# The files listening reads, each with its size and SHA-256 (from the same archive, 20 Nov 2026).
FILES = {
    "encoder.int8.onnx": (652_282_309,
                          "92a10bd9c23936ea0566c29f963bc57c2a5df4261b6a8921c39abea66557ca8b"),
    "decoder.int8.onnx": (7_257_753, "4d097bae3bac5c96018d5293ba39f1b657296102b142affda40378f38b020f41"),
    "joiner.int8.onnx": (1_739_080, "77a29f3e5ac7d37074e0af465391b80f314015d5b97f0f90c6c7767f1d56f428"),
    "tokens.txt": (9_384, "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d"),
}
UNPACKED_BYTES = sum(size for size, _ in FILES.values())
DISK_NEEDED = ARCHIVE_BYTES + UNPACKED_BYTES + 50_000_000   # both at once while it unpacks, and a margin
FOLDER = "parakeet-tdt-0.6b-v2-int8"
READY = "ready.json"
ARCHIVE = "parakeet.tar.bz2"
CHUNK = 1 << 20


def megabytes(n: int) -> int:
    return round(n / 1_000_000)


class FetchError(Exception):
    """The speech model could not be fetched. The message is the plain sentence the owner reads."""


class SpeechModel:
    """The speech model's files under `<data>/speech/`."""

    def __init__(self, folder: Path | str, url: str = URL, archive_sha256: str = ARCHIVE_SHA256,
                 archive_bytes: int = ARCHIVE_BYTES, files: dict | None = None) -> None:
        self.folder = Path(folder)
        self.url = url
        self.archive_sha256 = archive_sha256
        self.archive_bytes = archive_bytes
        self.files = FILES if files is None else files
        self.unpacked_bytes = sum(size for size, _ in self.files.values())
        # Set when a fetch is stopped, so its unpacking thread ends at once. One for each fetch: a new fetch never
        # clears the stop of an earlier one whose thread may still be ending.
        self._stop = threading.Event()

    @property
    def model(self) -> Path:
        return self.folder / FOLDER

    def ready(self) -> bool:
        """Fetched and checked before (`ready.json`), and every file still there at its size."""
        try:
            kept = json.loads((self.folder / READY).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(kept, dict) or kept.get("archive_sha256") != self.archive_sha256:
            return False
        for name, (size, _) in self.files.items():
            try:
                if (self.model / name).stat().st_size != size:
                    return False
            except OSError:
                return False
        return True

    def disk_free(self) -> int | None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            return shutil.disk_usage(self.folder).free
        except OSError:
            return None

    def disk_needed(self) -> int:
        return self.archive_bytes + self.unpacked_bytes + 50_000_000

    # Fetching.

    async def fetch(self, session, progress: Callable[[str, int, int], None]) -> float:
        """Download, check, unpack and check again; `progress(stage, done, total)` is called as it goes
        ("fetching" in bytes, then "unpacking"). Returns the seconds it took. Raises `FetchError` (plain words);
        whatever was half made is deleted."""
        began = time.monotonic()
        self._stop = stop = threading.Event()
        free = self.disk_free()
        if free is not None and free < self.disk_needed():
            raise FetchError(f"There is not enough free disk space for the speech model. It needs about "
                             f"{gigabytes(self.disk_needed())} GB while it is fetched and unpacked, and this "
                             f"machine has {gigabytes(free)} GB free.")
        self.folder.mkdir(parents=True, exist_ok=True)
        archive = self.folder / ARCHIVE
        try:
            await self._download(session, archive, progress)
            progress("unpacking", 0, self.unpacked_bytes)
            # Unpacking runs on a thread of its own; its reports are handed to the event loop, never run there.
            loop = asyncio.get_running_loop()
            await asyncio.to_thread(self._unpack, archive, stop,
                                    lambda *report: loop.call_soon_threadsafe(progress, *report))
        except FetchError:
            self._tidy()
            raise
        except asyncio.CancelledError:
            stop.set()
            self._tidy()
            raise
        except OSError as error:
            self._tidy()
            if getattr(error, "errno", None) == 28:   # no space left on the device
                raise FetchError("The disk filled up while the speech model was being fetched. Nothing was "
                                 "kept. Free some space and try again.") from None
            raise FetchError("The speech model could not be saved on this machine. Nothing was kept.") from None
        finally:
            with contextlib.suppress(OSError):
                archive.unlink()
        seconds = time.monotonic() - began
        LOG.info("listening: the speech model was fetched and checked in %.0f s", seconds)
        return seconds

    async def _download(self, session, archive: Path, progress) -> None:
        import aiohttp

        digest = hashlib.sha256()
        done = 0
        progress("fetching", 0, self.archive_bytes)
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
            async with session.get(self.url, timeout=timeout) as reply:
                if reply.status != 200:
                    raise FetchError(f"GitHub did not give the speech model (it answered {reply.status}). "
                                     "Nothing was kept. Try again later.")
                with archive.open("wb") as handle:
                    async for block in reply.content.iter_chunked(CHUNK):
                        done += len(block)
                        if done > self.archive_bytes:
                            raise FetchError(_NOT_THE_FILE)
                        digest.update(block)
                        await asyncio.to_thread(handle.write, block)
                        progress("fetching", done, self.archive_bytes)
        except (aiohttp.ClientError, TimeoutError):
            raise FetchError("Hunch could not reach GitHub to fetch the speech model. Check this machine's "
                             "internet connection and try again. Nothing was kept.") from None
        if done != self.archive_bytes or digest.hexdigest() != self.archive_sha256:
            raise FetchError(_NOT_THE_FILE)

    def stop(self) -> None:
        """Stop the fetch in progress (its unpacking thread ends within one block)."""
        self._stop.set()

    def _unpack(self, archive: Path, stop: threading.Event, progress) -> None:
        part = self.folder / (FOLDER + ".part")
        self._remove_folder(part)
        part.mkdir(parents=True)
        try:
            self._unpack_into(archive, part, stop, progress)
        except BaseException:
            self._remove_folder(part)
            raise
        self._remove_folder(self.model)
        os.replace(part, self.model)
        record = {"name": NAME, "url": self.url, "archive_sha256": self.archive_sha256,
                  "files": {name: sha for name, (_, sha) in self.files.items()}, "at": time.time()}
        partial = self.folder / (READY + ".part")
        partial.write_text(json.dumps(record), encoding="utf-8")
        os.replace(partial, self.folder / READY)

    def _unpack_into(self, archive: Path, part: Path, stop: threading.Event, progress) -> None:
        wanted = {f"{INSIDE}/{name}": name for name in self.files}
        done = 0
        with bz2.open(archive, "rb") as packed, tarfile.open(fileobj=packed, mode="r|") as tar:
            for member in tar:
                name = wanted.pop(member.name, None)
                if name is None:
                    continue
                if not member.isfile() or member.size != self.files[name][0]:
                    raise FetchError(_NOT_THE_FILE)
                source = tar.extractfile(member)
                digest = hashlib.sha256()
                with (part / name).open("wb") as handle:
                    while block := source.read(CHUNK):
                        if stop.is_set():
                            raise FetchError("stopped")
                        digest.update(block)
                        handle.write(block)
                        done += len(block)
                        progress("unpacking", done, self.unpacked_bytes)
                if digest.hexdigest() != self.files[name][1]:
                    raise FetchError(_NOT_THE_FILE)
        if wanted:
            raise FetchError(_NOT_THE_FILE)

    def _tidy(self) -> None:
        """Delete what a failed or stopped fetch left: the archive and the half-unpacked folder."""
        with contextlib.suppress(OSError):
            (self.folder / ARCHIVE).unlink()
        self._remove_folder(self.folder / (FOLDER + ".part"))

    def _remove_folder(self, folder: Path) -> None:
        """Delete a folder of the model's own files, one known file at a time, then the folder."""
        for name in self.files:
            with contextlib.suppress(OSError):
                (folder / name).unlink()
        with contextlib.suppress(OSError):
            folder.rmdir()


_NOT_THE_FILE = ("The file that came was not the speech model Hunch expects (its checksum did not match), so it "
                 "was deleted and not used. Try again later.")


def gigabytes(n: int) -> str:
    return f"{n / 1_000_000_000:.1f}"
