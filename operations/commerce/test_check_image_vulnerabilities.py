import json
import tempfile
import unittest
from pathlib import Path

from check_image_vulnerabilities import evaluate, findings


class ImageScanPolicyTest(unittest.TestCase):
    def test_harbor_complete_empty_report(self):
        self.assertEqual(findings("harbor", {
            "scan_overview": {"mime": {"scan_status": "Success"}},
            "vulnerability_report": {"mime": {"vulnerabilities": []}},
        }), [])

    def test_harbor_error_is_not_a_clean_scan(self):
        with self.assertRaisesRegex(ValueError, "did not complete"):
            findings("harbor", {
                "scan_overview": {"mime": {"scan_status": "Error"}},
                "vulnerability_report": {"mime": {"vulnerabilities": []}},
            })

    def test_trivy_image_report(self):
        self.assertEqual(findings("trivy", {
            "SchemaVersion": 2,
            "ArtifactType": "container_image",
            "Results": [{"Vulnerabilities": [{"Severity": "HIGH", "VulnerabilityID": "CVE-test"}]}],
        }), [{"Severity": "HIGH", "VulnerabilityID": "CVE-test"}])

    def test_trivy_missing_results_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "results are missing"):
            findings("trivy", {"SchemaVersion": 2, "ArtifactType": "container_image"})

    def test_high_finding_blocks_release(self):
        report = {"SchemaVersion": 2, "ArtifactType": "container_image",
                  "Results": [{"Vulnerabilities": [{"Severity": "HIGH", "VulnerabilityID": "CVE-test"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.json"
            path.write_text(json.dumps(report))
            self.assertEqual(evaluate("trivy", [path]), ["image.json: HIGH CVE-test"])


if __name__ == "__main__":
    unittest.main()
