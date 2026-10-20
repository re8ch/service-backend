from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "clusters/re8ch/components/supabase-pg-re8ch/supabase-rest-runtime.yaml"
POSTGREST_IMAGE = (
    "docker.io/postgrest/postgrest:v14.12@"
    "sha256:54000f24847d01a2c2302e0041cf0618b875c57fb48507d743cfa9aaa50bf43c"
)


def test_supabase_postgrest_uses_reproducible_public_image_and_oom_headroom():
    documents = list(yaml.safe_load_all(MANIFEST.read_text()))
    deployment = next(
        document
        for document in documents
        if document
        and document.get("kind") == "Deployment"
        and document.get("metadata", {}).get("name") == "supabase-rest"
    )
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    annotations = deployment["spec"]["template"]["metadata"]["annotations"]

    assert container["image"] == POSTGREST_IMAGE
    assert container["resources"]["requests"]["memory"] == "128Mi"
    assert container["resources"]["limits"]["memory"] == "512Mi"
    assert annotations["re8ch.com/recovery-rollout"] == "2026-10-21-search-path"

    env = {item["name"]: item for item in container["env"]}
    assert env["PGRST_DB_SCHEMAS"]["value"] == "public,storage"
    assert env["PGRST_DB_EXTRA_SEARCH_PATH"]["value"] == "public"
