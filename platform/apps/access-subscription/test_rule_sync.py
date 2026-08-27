import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rule_sync


class RuleSyncTests(unittest.TestCase):
    def test_builds_official_github_raw_api_fallback(self):
        self.assertEqual(
            rule_sync.github_api_raw_url("https://raw.githubusercontent.com/owner/repo/main/a b/rule.list"),
            "https://api.github.com/repos/owner/repo/contents/a%20b/rule.list?ref=main",
        )

    def test_clean_list_deduplicates_and_drops_comments(self):
        self.assertEqual(rule_sync.clean_list([b"# x\nDOMAIN,a\n", b"DOMAIN,a\nDOMAIN,b\n"]), b"DOMAIN,a\nDOMAIN,b\n")

    def test_converts_shadowrocket_domains_for_mosdns(self):
        raw = b"DOMAIN,a.example\nDOMAIN-SUFFIX,example.com\nIP-CIDR,192.0.2.5/24,no-resolve\n"
        self.assertEqual(rule_sync.mosdns_domains(raw), b"full:a.example\ndomain:example.com\n")

    def test_sync_promotes_atomic_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def fake_fetch(url):
                if "api.github.com" in url:
                    return b'{"sha":"0123456789012345678901234567890123456789"}'
                return b"M" * 256 if url.endswith(".mrs") else b"DOMAIN-SUFFIX,example.com\n"
            with patch.object(rule_sync, "ROOT", root), patch.object(rule_sync, "fetch", fake_fetch):
                version = rule_sync.sync(now=100)
                self.assertEqual((root / "current" / "manifest.json").exists(), True)
                self.assertEqual((root / "current" / "cn.mrs").read_bytes(), b"M" * 256)
                self.assertIn(version, (root / "current" / "manifest.json").read_text())
                self.assertIn("0123456789012345678901234567890123456789", (root / "current" / "manifest.json").read_text())

    def test_rejects_large_rule_drift(self):
        previous = {"recipeRevision": rule_sync.RECIPE_REVISION, "artifacts": {"cn.list": {"metric": 100}}}
        with self.assertRaises(ValueError):
            rule_sync.validate_change("cn.list", 130, previous)

    def test_recipe_upgrade_allows_audited_rule_expansion(self):
        previous = {"recipeRevision": rule_sync.RECIPE_REVISION - 1, "artifacts": {"ai.list": {"metric": 38}}}
        rule_sync.validate_change("ai.list", 71, previous)


if __name__ == "__main__":
    unittest.main()
