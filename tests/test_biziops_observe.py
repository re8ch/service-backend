from pathlib import Path
import importlib.util
import io
import json

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


def test_posthog_observes_every_accessible_project(monkeypatch):
    responses = {
        "/api/projects/?limit=100": {"results": [
            {"id": 174040, "name": "Artchais"},
            {"id": 347656, "name": "RE8CH Business"},
        ]},
        "/api/projects/174040/query/": {"results": [[0, 0, 0, 0, 1190, "2026-08-16T16:30:47Z"]]},
        "/api/projects/347656/query/": {"results": [[3, 2, 8, 12, 12, "2026-11-13T01:00:00Z"]]},
    }

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_urlopen(request, timeout):
        del timeout
        path = request.full_url.removeprefix("https://eu.posthog.com")
        return Response(json.dumps(responses[path]).encode())

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    result = module.posthog_observations("https://eu.posthog.com", "secret", 5)
    assert [item["name"] for item in result["projects"]] == ["Artchais", "RE8CH Business"]
    assert result["projects"][0]["eventsAllTime"] == 1190
    assert result["projects"][1]["sessions30d"] == 3
