import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary.interaction import EntityRef, InteractionObservation, supporting_evidence_is_adoptable


class InteractionModelTest(unittest.TestCase):
    def test_two_independent_supporting_domains_can_be_adopted(self):
        self.assertTrue(supporting_evidence_is_adoptable([
            ("alertmanager", "webhook", 0.8), ("telegram", "provider-callback", 0.6),
        ]))

    def test_logs_never_complete_delivery_inference(self):
        self.assertFalse(supporting_evidence_is_adoptable([
            ("alertmanager", "webhook", 0.8), ("telegram", "application-log", 0.99),
        ]))

    def test_two_methods_in_one_failure_domain_are_not_independent(self):
        self.assertFalse(supporting_evidence_is_adoptable([
            ("telegram", "callback", 0.8), ("telegram", "poll", 0.8),
        ]))

    def test_direct_recipient_addresses_are_rejected(self):
        with self.assertRaises(ValueError):
            EntityRef(kind="recipient", id="someone@example.com")

    def test_raw_payload_is_not_part_of_observation_schema(self):
        value = InteractionObservation.from_dict({
            "interaction_id": "int-1", "native_type": "custom", "native_status": "accepted",
            "relation": "delivered_to", "source": {"kind": "service", "id": "service-a"},
            "target": {"kind": "recipient", "id": "recipient-001"},
            "runtime": {"kind": "runtime", "id": "telegram"},
            "observed_at": "2026-09-19T00:00:00Z", "authority": "provider_callback",
            "evidence": {"strength": "authoritative", "failure_domain": "telegram", "method": "callback", "confidence": 1,
                         "payload_hash": "a" * 64},
            "idempotency_key": "telegram:update:1",
        })
        self.assertNotIn("payload", value.to_dict())

    def test_raw_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "raw message bodies"):
            InteractionObservation.from_dict({"payload": {"text": "do not store this"}})


if __name__ == "__main__":
    unittest.main()
