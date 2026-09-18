import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from secretary.api import alert_incident_id, alertmanager_events, alertmanager_observations


class AlertmanagerFactsTest(unittest.TestCase):
    def payload(self, status="firing"):
        return {
            "status": status,
            "receiver": "secretary-critical",
            "groupKey": "{}:{namespace=\"ledger\"}",
            "externalURL": "http://alertmanager:9093",
            "alerts": [{
                "fingerprint": "abc123",
                "startsAt": "2026-09-14T08:00:00Z",
                "endsAt": "2026-09-14T08:05:00Z" if status == "resolved" else "0001-01-01T00:00:00Z",
                "labels": {"alertname": "ServiceDown", "namespace": "ledger", "service": "ledger-api", "severity": "critical", "owner": "re8ch/ledger"},
                "annotations": {"summary": "ledger unavailable"},
            }],
        }

    def test_firing_records_alert_observation_only(self):
        events = alertmanager_events(self.payload())
        self.assertEqual([event.type for event in events], ["alert.observed"])
        self.assertNotIn("delivery.attempted", [event.type for event in events])

    def test_resolved_does_not_create_delivery_attempt(self):
        events = alertmanager_events(self.payload("resolved"))
        self.assertEqual([event.type for event in events], ["alert.resolved"])

    def test_incident_identity_separates_recurrences(self):
        first = alert_incident_id("abc", "2026-09-14T08:00:00Z")
        retry = alert_incident_id("abc", "2026-09-14T08:00:00Z")
        recurrence = alert_incident_id("abc", "2026-09-15T08:00:00Z")
        self.assertEqual(first, retry)
        self.assertNotEqual(first, recurrence)

    def test_webhook_becomes_side_channel_observation_without_raw_body(self):
        observations = alertmanager_observations(self.payload())
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].relation, "receiver_invoked")
        self.assertEqual(observations[0].authority, "runtime_receipt")
        self.assertTrue(observations[0].evidence.payload_hash)
        self.assertNotIn("annotations", observations[0].metadata)


if __name__ == "__main__":
    unittest.main()
