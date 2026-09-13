import datetime as dt
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary.model import FactEvent
from secretary.policy import delivery_is_ack, evaluate_resolution


NOW = dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc)


def event(kind, *, authority="tool_observation", actor="system", domain="a", method="http", minutes=10, data=None):
    event_id = str(uuid.uuid4())
    return FactEvent.from_dict({
        "event_id": event_id, "incident_id": "inc_test", "type": kind, "subject": "service/x",
        "actor": {"kind": actor, "id": "actor-1"},
        "observed_at": (NOW - dt.timedelta(minutes=minutes)).isoformat(),
        "received_at": NOW.isoformat(), "source": "test", "authority": authority,
        "evidence": {"strength": "authoritative", "failure_domain": domain, "artifact_ref": "artifact:x", "confidence": 1, "method": method},
        "causation_id": event_id, "correlation_id": "inc_test", "idempotency_key": event_id,
        "data": data or {"recovered": True},
    })


class PolicyTest(unittest.TestCase):
    def evidence(self):
        return [
            event("health.recovered", domain="cluster", method="kubernetes-ready"),
            event("probe.observed", domain="external-cn", method="https", data={"recovered": True}),
            event("resolution.proposed", authority="model_inference", actor="agent", domain="agent", method="reasoning", data={}),
        ]

    def test_warning_can_auto_close_with_independent_evidence(self):
        decision = evaluate_resolution("warning", self.evidence(), 300, NOW)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.independent_evidence, 2)

    def test_same_failure_domain_and_method_is_not_independent(self):
        events = self.evidence()
        events[1] = event("probe.observed", domain="cluster", method="kubernetes-ready", data={"recovered": True})
        decision = evaluate_resolution("warning", events, 300, NOW)
        self.assertFalse(decision.allowed)
        self.assertIn("fewer_than_two_independent_recovery_observations", decision.reasons)

    def test_model_cannot_create_observation(self):
        with self.assertRaises(ValueError):
            event("observation.recorded", authority="model_inference", actor="agent")

    def test_read_receipt_is_not_ack(self):
        self.assertFalse(delivery_is_ack(event("delivery.human_seen", authority="provider_callback", actor="provider")))

    def test_only_verified_human_action_is_ack(self):
        ack = event("responsibility.acknowledged", authority="authenticated_action", actor="human", data={"identity_verified": True})
        self.assertTrue(delivery_is_ack(ack))

    def test_critical_requires_human_close(self):
        decision = evaluate_resolution("critical", self.evidence(), 300, NOW)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.next_state, "verified")
        closed = event("human.responded", authority="authenticated_action", actor="human", data={"identity_verified": True, "action": "confirm_resolution"})
        decision = evaluate_resolution("critical", self.evidence() + [closed], 300, NOW)
        self.assertTrue(decision.allowed)

    def test_new_negative_reopens_investigation(self):
        negative = event("health.failed", minutes=1, data={"recovered": False})
        decision = evaluate_resolution("warning", self.evidence() + [negative], 300, NOW)
        self.assertFalse(decision.allowed)
        self.assertIn("unresolved_negative_evidence", decision.reasons)


if __name__ == "__main__":
    unittest.main()
