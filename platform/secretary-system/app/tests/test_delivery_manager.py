import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary.api import fact
from secretary.delivery_manager import unknown_fact


class DeliveryManagerTest(unittest.TestCase):
    def test_receipt_timeout_stays_unknown(self):
        attempt = fact(
            "inc_test", "delivery.attempted", "owner@example.com",
            actor_kind="system", actor_id="secretary", source="secretary",
            authority="authenticated_action", strength="authoritative",
            failure_domain="secretary-system", method="smtp-request",
            idempotency_key="test:attempt", data={"channel": "email"},
        )
        event = unknown_fact(attempt)
        self.assertEqual(event.type, "delivery.unknown")
        self.assertEqual(event.causation_id, attempt.event_id)
        self.assertNotEqual(event.type, "delivery.failed")
        self.assertNotEqual(event.type, "delivery.provider_accepted")
        self.assertEqual(event.idempotency_key, f"delivery-unknown:{attempt.event_id}")


if __name__ == "__main__":
    unittest.main()
