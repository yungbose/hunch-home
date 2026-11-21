"""The model in this process: Hunch's typed build, checked against its manifest, loaded once, asked one way.

From the server's `hunch_server/model.py`. The folder is the typed build's (`manifest.json`, `model_q4.onnx`
with its data file, `tokenizer.json` and `lookup/`); the runtime package `hunch_home`, copied into the image
from the model's own release, does the matching. Needs onnxruntime, tokenizers and numpy (imported only when a
model is loaded); never torch.

One call: `await model.answer(text)` gives what the rules' `decide` takes: an `Answer`, or a `HandBack` when
there is no answer to give (no model loaded: "server error"; a reply the rules cannot read: "bad reply"). The
model's own dict goes through the same check the plug-in made of the server's reply (`logic.client.read_reply`),
so what reaches the rules is exactly what reached them before. Time limits are the caller's (`turn.py`).

The model runs in one worker thread, so two sentences never use it at once. A stand-in for tests (and the app
before its model has loaded) only needs `ready`, `version` and `answer`.

Teaching by example is the runtime's own (`Hunch.teach` and `Hunch.forget`, its API.md), reached only through
`teach.py`: a loaded model also has `actions`, `kinds`, `can_decline`, `teach(examples)` and `forget(ids)`, run
in the same worker thread as answering.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from . import fast
from .logic.client import NO_KIND, NOT_FOR_ME, Answer, HandBack, read_reply

LOG = logging.getLogger("hunch_app.model")
MANIFEST = "manifest.json"
NEEDED = ("model_q4.onnx", "model_q4.onnx_data", "tokenizer.json", "lookup/settings.json",
          "lookup/vectors.npy")
NOT_LOADED = HandBack("server error", "the model is not loaded")
# What the page's state says when loading failed (`App.model_problem`); the page adds where sentences go.
COULD_NOT_LOAD = "Hunch could not load its model."


class ModelError(RuntimeError):
    """The model folder is not a complete, unchanged typed build. The message names what is wrong."""


class Model(Protocol):
    ready: bool
    version: str | None

    async def answer(self, text: str) -> Answer | HandBack:
        ...


class NoModel:
    """No model (still loading, or it failed to load): every sentence gets the hand-back `NOT_LOADED`."""

    ready = False
    version = None

    async def answer(self, text: str) -> Answer | HandBack:
        return NOT_LOADED


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(folder: Path) -> dict:
    path = folder / MANIFEST
    if not folder.is_dir():
        raise ModelError(f"the model folder {folder} does not exist; set HUNCH_MODEL_DIR to the typed "
                         "build's folder (the one holding manifest.json)")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ModelError(f"{folder} has no {MANIFEST}; give the typed build's folder as it was "
                         "downloaded") from None
    except (OSError, ValueError) as error:
        raise ModelError(f"could not read {path} ({type(error).__name__})") from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ModelError(f"{path} does not list the build's files")
    return manifest


def check_manifest(folder: Path) -> dict:
    """Refuse a folder whose files are missing or are not the ones its manifest lists (sha256 of each).
    Returns the manifest."""
    manifest = read_manifest(folder)
    if manifest.get("input") not in (None, "typed"):
        raise ModelError(f"{folder} holds a {manifest.get('input')} build; the app needs the typed build")
    entries = [entry for entry in manifest["files"] if isinstance(entry, dict)]
    listed = {entry.get("path"): entry.get("sha256") for entry in entries}
    unlisted = [name for name in NEEDED if name not in listed]
    if unlisted:
        raise ModelError(f"{folder} is not the typed ONNX build: its manifest does not list "
                         f"{', '.join(unlisted)}")
    missing = [name for name in listed if not (folder / name).is_file()]
    if missing:
        raise ModelError(f"{folder} is missing {', '.join(sorted(missing))}")
    changed = [name for name, digest in listed.items() if _sha256(folder / name) != digest]
    if changed:
        raise ModelError(f"in {folder}, {', '.join(sorted(changed))} differ from the files the manifest "
                         "lists; download the build again")
    return manifest


def version_of(manifest: dict) -> str:
    return str(manifest.get("version") or "unknown")


def to_answer(detail: object) -> Answer | HandBack:
    """The runtime's answer dict, read as the plug-in read the server's reply."""
    try:
        body = json.dumps(detail, allow_nan=True)
    except (TypeError, ValueError):
        return HandBack("bad reply", "the model's answer is not plain data")
    return read_reply(200, body)


class Loaded:
    """A loaded engine (anything with `answer(text) -> dict`), asked in one worker thread."""

    ready = True

    def __init__(self, engine, version: str) -> None:
        self.engine = engine
        self.version = version
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hunch-model")

    async def answer(self, text: str) -> Answer | HandBack:
        loop = asyncio.get_running_loop()
        try:
            detail = await loop.run_in_executor(self._worker, self.engine.answer, text)
        except Exception as error:  # noqa: BLE001 - a failing model hands the sentence on, it never stops the app
            LOG.warning("the model failed on a sentence (%s)", type(error).__name__)
            return HandBack("server error", type(error).__name__)
        return to_answer(detail)

    def close(self) -> None:
        self._worker.shutdown(wait=False, cancel_futures=True)

    # Teaching (`teach.py`), in the same worker thread as answering, so the two never overlap.

    @property
    def actions(self) -> tuple[str, ...]:
        return self.engine.actions

    @property
    def kinds(self) -> tuple[str, ...]:
        return self.engine.kinds

    @property
    def can_decline(self) -> bool:
        return self.engine.can_decline

    async def teach(self, examples: list[tuple[str, str, str]]) -> list[int | None]:
        return await asyncio.get_running_loop().run_in_executor(self._worker, self.engine.teach, examples)

    async def forget(self, ids: list[int]) -> int:
        return await asyncio.get_running_loop().run_in_executor(self._worker, self.engine.forget, ids)


# Teaching: the owner's examples are the runtime's under this one owner name, and each pair of answers is one
# command of the runtime's, named here. The names never reach the owner.
OWNER = "home"


def command_for(action: str, kind: str) -> str:
    return f"taught: {action} / {kind}"


class Engine:
    """The runtime's `Hunch`, giving its answer dict with the model's version beside it, and the runtime's own
    teaching by example (`teach.py` is its only caller)."""

    def __init__(self, hunch, version: str) -> None:
        self.hunch = hunch
        self.version = version

    def answer(self, text: str) -> dict:
        from hunch_home.hunch import plain

        return {**plain(dict(self.hunch.answer_text(text)._detail)), "version": self.version}

    # Teaching by example: the runtime's `teach` and `forget` (its API.md), under one owner.

    @property
    def actions(self) -> tuple[str, ...]:
        return tuple(self.hunch.actions)

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self.hunch.kinds)

    @property
    def can_decline(self) -> bool:
        """Whether the runtime takes an example whose answer is "not for me" (its table's `check_answers`)."""
        try:
            from hunch_home.table import NO_KIND as RUNTIME_NO_KIND
            from hunch_home.table import NOT_FOR_ME as RUNTIME_NOT_FOR_ME
            from hunch_home.table import check_answers

            check_answers(RUNTIME_NOT_FOR_ME, RUNTIME_NO_KIND)
        except (ImportError, ValueError):
            return False
        return (RUNTIME_NOT_FOR_ME, RUNTIME_NO_KIND) == (NOT_FOR_ME, NO_KIND)

    def teach(self, examples: list[tuple[str, str, str]]) -> list[int | None]:
        """Teach the runtime each (sentence, action, kind); its id for each, in order, or None for one it
        refused. One `Hunch.teach` call per pair of answers, under that pair's own command name. If the model
        fails part-way (anything but a refusal), those taught in this call are removed again before it raises, so
        it is never left holding some of them."""
        ids: list[int | None] = [None] * len(examples)
        try:
            self._teach(examples, ids)
        except Exception:
            done = [taught_id for taught_id in ids if taught_id is not None]
            if done:
                self.hunch.forget(OWNER, ids=done)
            raise
        return ids

    def _teach(self, examples: list[tuple[str, str, str]], ids: list[int | None]) -> None:
        groups: dict[tuple[str, str], list[int]] = {}
        for i, (_, action, kind) in enumerate(examples):
            groups.setdefault((action, kind), []).append(i)
        for (action, kind), members in groups.items():
            sentences = [examples[i][0] for i in members]
            try:
                done = self.hunch.teach(command_for(action, kind), action, kind, sentences=sentences, owner=OWNER)
            except ValueError as error:
                if len(members) == 1:
                    LOG.warning("the model refused a taught example (%s)", error)
                    continue
                for i in members:   # one at a time, so one refused sentence does not take the others with it
                    one: list[int | None] = [None]
                    self._teach([examples[i]], one)
                    ids[i] = one[0]
                continue
            for i, taught_id in zip(members, done["ids"], strict=True):
                ids[i] = int(taught_id)

    def forget(self, ids: list[int]) -> int:
        return int(self.hunch.forget(OWNER, ids=list(ids)))

    def taught_count(self) -> int:
        return len(self.hunch.examples(OWNER))


def load(folder: Path, threads: int = 2, use_fast: bool = False) -> Loaded:
    """Check the folder, make or find the fast copy when asked, load the typed build and warm it up.
    Blocking: call it in a worker thread."""
    manifest = check_manifest(folder)  # first, so a wrong folder gets its plain message before anything loads

    from hunch_home.builds import OnnxText
    from hunch_home.hunch import Hunch
    from hunch_home.store import SETTINGS, load_lookup

    name = fast.ORIGINAL
    if use_fast:
        made = fast.fast_copy(folder)
        if made is None:
            LOG.warning("could not write the faster copy beside the model (the folder is read-only); "
                        "running the standard 4-bit route")
        else:
            name = made.name
    lookup = load_lookup(folder / "lookup")
    document = json.loads((folder / "lookup" / SETTINGS).read_text(encoding="utf-8"))
    encoder = OnnxText(folder, dict(lookup.settings.encoder), name, threads)
    if encoder.dimension != lookup.dimension:
        raise ModelError(f"the model gives {encoder.dimension} numbers; the folder's examples hold "
                         f"{lookup.dimension}")
    hunch = Hunch(lookup, encoder, {"folder": str(folder), "repository": None, "revision": None}, document)
    engine = Engine(hunch, version_of(manifest))
    engine.answer("warm up")  # so the first real sentence is not slow
    return Loaded(engine, engine.version)


def prepare(folder: str, version: str | None = None) -> int:
    """Check a model folder against its manifest (and its version, when given) and write its fast copy beside
    it. Run while the image is built (`python -m hunch_app.model prepare FOLDER [VER]`, plain Python with no
    packages), so the running app never needs to write into its model folder."""
    path = Path(folder)
    try:
        manifest = check_manifest(path)
    except ModelError as error:
        print(f"the model folder is not ready: {error}", file=sys.stderr)
        return 1
    found = version_of(manifest)
    if version and found != version:
        print(f"the model folder holds version {found}, not {version}: give the folder of version {version}, "
              f"or build with --build-arg HUNCH_VERSION={found}", file=sys.stderr)
        return 1
    made = fast.fast_copy(path)
    if made is None:
        print(f"could not write the fast copy into {path}", file=sys.stderr)
        return 1
    print(f"Hunch-{found} Home typed build checked ({len(manifest['files'])} files); fast copy {made.name}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) in (3, 4) and sys.argv[1] == "prepare":
        sys.exit(prepare(*sys.argv[2:]))
    print("python -m hunch_app.model prepare FOLDER [VERSION]", file=sys.stderr)
    sys.exit(2)
