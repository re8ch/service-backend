"""Session-local parsed inputs. No disk cache or directory-list caching.

Readers always reopen inputs; content, parser identity/version and mode must match.
The opt-in context is shared by script/importlib consumers via this canonical module.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import sys
from pathlib import Path

import yaml

_ACTIVE = ContextVar("validation_input_snapshot", default=None)


class InputSnapshot:
    def __init__(self):
        self._entries = {}
        self.parsed = 0
        self.reused = 0

    def documents(self, path, *, mode, loader=None):
        # read_text preserves the callers' universal-newline/text parser semantics.
        content = Path(path).read_bytes()
        text = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        parser = (loader, yaml.__version__) if mode.startswith("yaml") else (json.loads, sys.version)
        slot = (str(Path(path).absolute()), mode, parser)
        digest = hashlib.sha256(content).digest()
        previous = self._entries.get(slot)
        if previous is not None and previous[0] == digest:
            self.reused += 1
            yield from deepcopy(previous[1])
            return
        # Discard stale success before parsing; exceptions/partial iteration never cache.
        self._entries.pop(slot, None)
        documents = []
        for document in _parse(text, mode, loader):
            documents.append(deepcopy(document))
            yield document
        self._entries[slot] = (digest, documents)
        self.parsed += 1


def _parse(text, mode, loader):
    if mode == "yaml-all":
        yield from yaml.load_all(text, Loader=loader)
    elif mode == "yaml":
        yield yaml.load(text, Loader=loader)
    elif mode == "json":
        yield json.loads(text)
    else:
        raise ValueError(f"unknown input parser mode: {mode}")


@contextmanager
def session_inputs():
    snapshot = InputSnapshot()
    token = _ACTIVE.set(snapshot)
    try:
        yield snapshot
    finally:
        _ACTIVE.reset(token)
        snapshot._entries.clear()


def _documents(path, mode, loader=None):
    snapshot = _ACTIVE.get()
    if snapshot is not None:
        yield from snapshot.documents(path, mode=mode, loader=loader)
    else:
        yield from _parse(Path(path).read_text(encoding="utf-8"), mode, loader)


def yaml_documents(path, *, loader=yaml.SafeLoader):
    return _documents(path, "yaml-all", loader)


def yaml_document(path, *, loader=yaml.SafeLoader):
    # Exhaust the generator so even single-document success is committed to cache.
    return list(_documents(path, "yaml", loader))[0]


def json_document(path):
    return list(_documents(path, "json"))[0]
