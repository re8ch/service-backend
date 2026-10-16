from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_supabase_runtime_is_reconciled_by_the_data_storage_domain() -> None:
    kustomization = yaml.safe_load(
        (ROOT / "clusters/re8ch/domains/data-storage/source/kustomization.yaml").read_text()
    )

    resources = kustomization["resources"]
    assert "../../../components/supabase-pg-re8ch/supabase-rest-runtime.yaml" in resources
