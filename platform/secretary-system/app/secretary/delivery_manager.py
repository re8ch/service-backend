from __future__ import annotations

import os
import time
import uuid

from .ledger import PostgresLedger
from .model import FactEvent, utc_now


def unknown_fact(attempt: FactEvent) -> FactEvent:
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"re8ch:delivery-unknown:{attempt.event_id}"))
    return FactEvent.from_dict({
        "event_id": event_id,
        "incident_id": attempt.incident_id,
        "type": "delivery.unknown",
        "subject": attempt.subject,
        "actor": {"kind": "system", "id": "secretary-delivery-manager"},
        "observed_at": utc_now(),
        "source": "secretary",
        "authority": "tool_observation",
        "evidence": {
            "strength": "supporting",
            "failure_domain": "secretary-delivery-manager",
            "artifact_ref": attempt.event_id,
            "confidence": 1,
            "method": "receipt-timeout",
        },
        "causation_id": attempt.event_id,
        "idempotency_key": f"delivery-unknown:{attempt.event_id}",
        "data": {
            **attempt.data,
            "delivery_state": "unknown",
            "reason": "no_stronger_channel_evidence_before_deadline",
            "attempt_event_id": attempt.event_id,
        },
    })


def run_once(ledger: PostgresLedger, older_than_seconds: int) -> int:
    return sum(
        1 for attempt in ledger.unresolved_delivery_attempts(older_than_seconds)
        if ledger.append(unknown_fact(attempt))
    )


def main() -> None:
    ledger = PostgresLedger(os.environ["SECRETARY_DATABASE_URL"])
    timeout = int(os.environ.get("DELIVERY_UNKNOWN_AFTER_SECONDS", "300"))
    interval = int(os.environ.get("DELIVERY_RECONCILE_INTERVAL_SECONDS", "30"))
    while True:
        run_once(ledger, timeout)
        time.sleep(max(5, interval))


if __name__ == "__main__":
    main()
