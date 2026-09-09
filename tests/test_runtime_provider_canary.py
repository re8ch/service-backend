import importlib.util
import pathlib
import unittest
from unittest import mock


PATH = pathlib.Path(__file__).parents[1] / "clusters/re8ch/components/runtime-provider-canary/gateway.py"
SPEC = importlib.util.spec_from_file_location("runtime_provider_gateway", PATH)
GATEWAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATEWAY)


class Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self): return b'{"ok":true}'


class RuntimeProviderGatewayTest(unittest.TestCase):
    def setUp(self):
        GATEWAY.COUNTERS.update(requests=0, fallbacks=0, failures=0)

    def test_primary_provider(self):
        with mock.patch.object(GATEWAY.urllib.request, "urlopen", return_value=Response()) as call:
            status, _, _, provider = GATEWAY.invoke("/invoke/hello", b"{}", {})
        self.assertEqual(status, 200)
        self.assertEqual(provider, GATEWAY.PROVIDERS[0])
        self.assertEqual(call.call_count, 1)

    def test_fallback_provider(self):
        with mock.patch.object(GATEWAY.urllib.request, "urlopen", side_effect=[OSError("primary down"), Response()]):
            status, _, _, provider = GATEWAY.invoke("/invoke/hello", b"{}", {})
        self.assertEqual(status, 200)
        self.assertEqual(provider, GATEWAY.PROVIDERS[1])
        self.assertEqual(GATEWAY.COUNTERS["fallbacks"], 1)

    def test_all_providers_fail_closed(self):
        with mock.patch.object(GATEWAY.urllib.request, "urlopen", side_effect=OSError("down")):
            status, _, body, provider = GATEWAY.invoke("/invoke/hello", b"{}", {})
        self.assertEqual(status, 503)
        self.assertEqual(provider, "none")
        self.assertIn(b"all runtime providers unavailable", body)


if __name__ == "__main__":
    unittest.main()

