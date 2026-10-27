"""Collection-only bridge; no fixtures or test calls are executed."""
import json
import os
from pathlib import Path


def pytest_collection_finish(session):
    cases = {}
    for item in session.items:
        cases.setdefault(item.nodeid.split('::', 1)[0], []).append(item.nodeid)
    Path(os.environ['UNIT_COLLECTION_FILE']).write_text(json.dumps(cases))
