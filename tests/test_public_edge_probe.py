import importlib.util
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / "operations/monitoring/public_edge_probe.py"
SPEC = importlib.util.spec_from_file_location("public_edge_probe", PATH)
probe_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe_module)


class PublicEdgeProbeTest(unittest.TestCase):
    def test_catalog_covers_all_declared_services(self):
        release = Path(__file__).resolve().parents[1] / "platform/public-edge-manager/release.yaml"
        targets = probe_module.targets(release)
        self.assertGreater(len(targets), 30)
        self.assertEqual(len(targets), len({target["host"] for target in targets}))
        self.assertTrue(all(target["url"].startswith("https://") for target in targets))

    def test_every_4xx_and_5xx_is_a_failure(self):
        target = {"service": "test", "host": "example.com", "url": "https://example.com/"}
        for status in (400, 401, 403, 404, 429, 500, 503):
            with self.subTest(status=status), patch.object(probe_module.socket, "getaddrinfo"), \
                 patch.object(probe_module.urllib.request, "build_opener") as opener:
                opener.return_value.open.side_effect = urllib.error.HTTPError(target["url"], status, "error", {}, None)
                self.assertFalse(probe_module.probe(target, 1)["ok"])


if __name__ == "__main__":
    unittest.main()
