import copy
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("ledger", ROOT / "ledger.py")
ledger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ledger)


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.record = json.loads((ROOT / "fixtures/record.json").read_text())

    def test_verified_record_projects(self):
        ledger.validate(self.record, "re8ch/service-backend", "1" * 40, "re8ch-k3s")
        row = ledger.project(self.record, {"status": "first_revision"})
        self.assertTrue(row["deployment_verified"])
        self.assertNotIn("ssa_receipt", row)

    def test_failed_probe_is_rejected(self):
        record = copy.deepcopy(self.record)
        record["deployment"]["live_observation"]["probes"][0]["result"] = "failed"
        with self.assertRaises(ledger.LedgerError):
            ledger.validate(record)

    def test_wrong_target_is_rejected(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.validate(self.record, expected_cluster="other")

    def test_changed_profile_is_not_compared(self):
        previous = copy.deepcopy(self.record)
        previous["benchmark"]["profile_digest"] = "sha256:" + "f" * 64
        self.assertEqual(ledger.compare(self.record, previous)["status"], "not_comparable")

    def test_secret_material_is_rejected(self):
        record = copy.deepcopy(self.record)
        record["benchmark"]["profile"] = "kubeconfig-export"
        with self.assertRaises(ledger.LedgerError):
            ledger.validate(record)


if __name__ == "__main__":
    unittest.main()
