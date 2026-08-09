import os, tempfile, unittest
os.environ["GEODB_PATH"] = "/nonexistent"
os.environ["MIN_HOLD_SECONDS"] = "60"
from path_controller import GeoDB, State


class ControllerTests(unittest.TestCase):
    def test_four_quadrants(self):
        state = State()
        state.geo.classify = lambda d: "CN" if d.endswith(".cn") else "GLOBAL"
        state.observe_peer({"peer_id": "cn-a", "region": "CN", "health": True, "mode": "P2P", "rtt_ms": 30, "loss": 0, "utilization": .1})
        cases = [("CN", "x.cn", "DIRECT"), ("CN", "x.com", "OVERSEAS_FABRIC"),
                 ("GLOBAL", "x.com", "DIRECT"), ("GLOBAL", "x.cn", "CN_FABRIC")]
        for region, dest, expected in cases:
            got = state.decide({"client_id": region + dest, "client_region": region, "destination": dest})
            self.assertEqual(expected, got["decision"])
        self.assertEqual("cn-a", state.decisions[-1]["netbird_peer"])

    def test_saturation_can_outweigh_latency(self):
        state = State()
        state.observe_peer({"peer_id": "small", "region": "CN", "health": True, "mode": "P2P", "rtt_ms": 20, "loss": 0, "utilization": 1})
        state.observe_peer({"peer_id": "large", "region": "CN", "health": True, "mode": "P2P", "rtt_ms": 80, "loss": 0, "utilization": .1})
        peer, _ = state.choose_peer("canary", "CN", 1000)
        self.assertEqual("large", peer)

    def test_geodb_hash(self):
        import hashlib, json
        payload = json.dumps({"version": "v1", "cn_cidrs": ["192.0.2.205/24"], "cn_domains": ["example.cn"]}).encode()
        with tempfile.NamedTemporaryFile() as f:
            f.write(payload); f.flush()
            import path_controller
            path_controller.GEODB_PATH = f.name
            path_controller.GEODB_SHA256 = hashlib.sha256(payload).hexdigest()
            geo = GeoDB()
            self.assertEqual("CN", geo.classify("192.0.2.121"))
            self.assertEqual("CN", geo.classify("www.example.cn"))


if __name__ == "__main__": unittest.main()
