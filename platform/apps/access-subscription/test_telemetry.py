import unittest

from telemetry import accumulate


class TelemetryTests(unittest.TestCase):
    def test_accumulates_deltas_by_inbound_user(self):
        first = [{"id": "c1", "upload": 10, "download": 20, "metadata": {"inboundUser": "device-a"}}]
        state = accumulate({}, first, now=100)
        self.assertEqual(state["devices"]["device-a"]["download"], 20)
        second = [{"id": "c1", "upload": 15, "download": 35, "metadata": {"inboundUser": "device-a"}}]
        state = accumulate(state, second, now=101)
        self.assertEqual(state["devices"]["device-a"]["upload"], 15)
        self.assertEqual(state["devices"]["device-a"]["download"], 35)
        self.assertEqual(state["devices"]["device-a"]["activeConnections"], 1)

    def test_ignores_unauthenticated_connections(self):
        state = accumulate({}, [{"id": "x", "upload": 10, "download": 10, "metadata": {}}], now=100)
        self.assertEqual(state["devices"], {})


if __name__ == "__main__":
    unittest.main()
