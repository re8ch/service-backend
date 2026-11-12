from pathlib import Path
import importlib.util

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("biziops_observe", ROOT / "operations/biziops/observe.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(module)


def test_all_governed_deployments_are_observed():
    catalog = yaml.safe_load((ROOT / "inventory/public-sites.yaml").read_text())
    expected = sum(len(site["deployments"]) for site in catalog["spec"]["sites"])
    rows = module.governed_deployments(catalog)
    assert len(rows) == expected
    assert all(row["url"].startswith("https://") for row in rows)


def test_source_catalog_starts_with_public_probe_and_declares_existing_tools():
    catalog = yaml.safe_load((ROOT / "operations/biziops/sources.yaml").read_text())
    sources = {item["id"]: item for item in catalog["spec"]["sources"]}
    assert sources["public-web"]["enabled"] is True
    assert {"cloudflare", "google-analytics", "posthog"} <= sources.keys()


def test_summary_preserves_unknown_instead_of_inventing_zero():
    payload = {
        "observedAt": "2026-11-13T00:00:00+00:00",
        "sources": [{"id": "posthog", "method": "query-api", "state": "declared-unconfigured"}],
        "observations": [{
            "displayName": "Example",
            "deployment": "global",
            "region": "global",
            "facts": {name: {"status": None} for name in ("home", "robots", "sitemap", "llms")},
            "tls": {},
        }],
    }
    summary = module.render_summary(payload)
    assert "unknown" in summary
    assert "declared-unconfigured" in summary


def _video_catalog(**overrides):
    source = {
        "id": "youtube", "displayName": "YouTube", "method": "data-api-v3", "collector": "youtube",
        "credentialEnvironment": ["YOUTUBE_API_KEY"], "enabled": True,
        "accounts": [{"id": "@re8ch", "label": "RE8CH"}],
    }
    source.update(overrides)
    return {"spec": {"videoSources": [source]}}


def test_video_catalog_declares_major_platforms():
    catalog = yaml.safe_load((ROOT / "operations/biziops/sources.yaml").read_text())
    ids = {item["id"] for item in catalog["spec"]["videoSources"]}
    assert {"youtube", "bilibili", "tiktok", "douyin", "kuaishou", "wechat-channels"} <= ids


def test_video_states_never_fetch_without_accounts_or_credentials():
    def forbidden(url, timeout):
        raise AssertionError("must not fetch")

    no_accounts = module.observe_video(_video_catalog(accounts=[]), {"YOUTUBE_API_KEY": "k"}, forbidden, 1)
    assert no_accounts[0]["state"] == "no-accounts"
    missing = module.observe_video(_video_catalog(), {}, forbidden, 1)
    assert missing[0]["state"] == "missing-credential"
    assert missing[0]["accounts"][0]["facts"] == {}
    disabled = module.observe_video(_video_catalog(enabled=False), {"YOUTUBE_API_KEY": "k"}, forbidden, 1)
    assert disabled[0]["state"] == "declared-unconfigured"


def test_youtube_collector_reads_statistics_and_hides_hidden_subscribers():
    seen = []

    def fake(url, timeout):
        seen.append(url)
        hidden = "forHandle" not in url
        return {"items": [{"statistics": {
            "subscriberCount": "1200", "viewCount": "34000", "videoCount": "18", "hiddenSubscriberCount": hidden,
        }}]}

    catalog = _video_catalog(accounts=[{"id": "@re8ch"}, {"id": "UCabc"}])
    rows = module.observe_video(catalog, {"YOUTUBE_API_KEY": "k"}, fake, 1)
    first, second = (account["facts"] for account in rows[0]["accounts"])
    assert first == {"followers": 1200, "views": 34000, "videos": 18}
    assert second["followers"] is None and second["views"] == 34000
    assert "forHandle=%40re8ch" in seen[0] and "id=UCabc" in seen[1]


def test_bilibili_collector_records_risk_control_as_error_not_zero():
    def fake(url, timeout):
        return {"code": 0, "data": {"follower": 42}} if "vmid=1" in url else {"code": -352}

    catalog = _video_catalog(id="bilibili", collector="bilibili", credentialEnvironment=[],
                             accounts=[{"id": "1"}, {"id": "2"}])
    rows = module.observe_video(catalog, {}, fake, 1)
    ok, blocked = (account["facts"] for account in rows[0]["accounts"])
    assert ok == {"followers": 42}
    assert blocked == {"error": "bilibili-code--352"}
    payload = {"observedAt": "t", "sources": [], "observations": [], "video": rows}
    summary = module.render_summary(payload)
    assert "bilibili-code--352" in summary and "| 42 |" in summary


def test_collector_errors_do_not_leak_api_key():
    def boom(url, timeout):
        raise OSError("connection refused for " + url)

    rows = module.observe_video(_video_catalog(), {"YOUTUBE_API_KEY": "secret-key"}, boom, 1)
    assert "secret-key" not in str(rows)
