"""Cross-process attempt budget. State contains counters only, never HTTP data."""
import fcntl
import json
import time
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


def initialize(path, maximum, reserved=0, rate=1):
    if not 1 <= maximum <= 2000 or not 0 <= reserved < maximum or not 1 <= rate <= 5:
        raise ValueError("invalid request budget")
    with Path(path).open("x") as handle:
        json.dump({"maximum": maximum, "reserved": reserved, "rate": rate, "used": 0, "next": 0}, handle)


def take(path, *, compensation=False):
    # Hold the lock through pacing so concurrent processes cannot burst.
    with Path(path).open("r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        state = json.load(handle)
        limit = state["maximum"] if compensation else state["maximum"] - state["reserved"]
        if state["used"] >= limit:
            raise BudgetExceeded("request budget exhausted")
        wait = state["next"] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        state["used"] += 1  # Consume before sending, including lost responses.
        state["next"] = time.monotonic() + 1 / state["rate"]
        handle.seek(0)
        json.dump(state, handle)
        handle.truncate()
        handle.flush()
        return state["used"]
