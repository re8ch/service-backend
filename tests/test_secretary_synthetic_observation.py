import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / "operations/monitoring/secretary_observation.py"
SPEC = importlib.util.spec_from_file_location("secretary_observation", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class SecretarySyntheticObservationTest(unittest.TestCase):
    def test_k6_failures_record_only_failed_external_contracts(self):
        catalog = {"probes": [
            {"id": "public", "owner": "platform/test", "vantage": "external", "url": "https://example.com"},
            {"id": "internal", "owner": "platform/test", "vantage": "internal", "url": "http://service"},
        ]}
        summary = {"root_group": {"checks": {
            "public contract": {"passes": 2, "fails": 1},
            "internal contract": {"passes": 0, "fails": 3},
        }}}
        with patch.object(module, "publish") as publish:
            self.assertEqual(module.publish_k6_failures(summary, catalog, "42"), 1)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(publish.call_args.kwargs["subject"], "public/public")

    def test_recurrence_uses_stable_interaction_and_run_idempotency(self):
        payloads = []

        class Response:
            status = 202
            def __enter__(self): return self
            def __exit__(self, *args): return False

        def capture(request, timeout):
            payloads.append(json.loads(request.data))
            self.assertIn("x-secretary-signature", {key.lower(): value for key, value in request.header_items()})
            return Response()

        env = {"SECRETARY_OBSERVATION_URL": "https://example.com/v1/observations", "SECRETARY_WEBHOOK_SECRET": "test"}
        with patch.dict(os.environ, env), patch.object(module.urllib.request, "urlopen", side_effect=capture):
            for run_id in ("42", "43"):
                module.publish(kind="k6.contract", subject="public/public", run_id=run_id,
                               summary="failed", artifact_ref="github-actions:42:k6-contract-summary")
        self.assertEqual(payloads[0]["interaction_id"], payloads[1]["interaction_id"])
        self.assertNotEqual(payloads[0]["idempotency_key"], payloads[1]["idempotency_key"])


if __name__ == "__main__":
    unittest.main()
