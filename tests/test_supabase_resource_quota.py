from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
QUOTA_PATHS = (
    ROOT / "platform/incidentrelay/database-quota.yaml",
    ROOT / "clusters/re8ch/components/supabase-pg-re8ch/supabase-pg-re8ch.yaml",
)


def _supabase_quota(path: Path) -> dict:
    return next(
        document
        for document in yaml.safe_load_all(path.read_text())
        if document
        and document.get("kind") == "ResourceQuota"
        and document.get("metadata", {}).get("name") == "supabase-one-cpu"
    )


def test_supabase_quota_has_postgrest_oom_headroom() -> None:
    for path in QUOTA_PATHS:
        hard = _supabase_quota(path)["spec"]["hard"]
        assert hard["requests.memory"] == "1280Mi"
        assert hard["limits.memory"] == "4096Mi"
