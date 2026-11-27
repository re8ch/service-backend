import io
import json
import pathlib
import subprocess
import tarfile

import yaml

ROOT = pathlib.Path(__file__).parents[1]


def test_materializes_only_executor_role_and_approved_catalog(tmp_path):
    manifest = {"releaseId": "agent-runtime-domain-toolhive-system"}
    objects = [{
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "re8ch-approved-tool-catalog", "namespace": "toolhive-system"},
        "data": {"registry.json": '{"version":"1.0.0"}'},
    }, {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": "unrelated", "namespace": "toolhive-system"},
    }]
    bundle = tmp_path / "release.bundle"
    with tarfile.open(bundle, "w:gz") as archive:
        for name, value in (("manifest.json", manifest), ("objects.json", objects)):
            payload = json.dumps(value).encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    output = tmp_path / "bootstrap.yaml"
    subprocess.run([
        "python", str(ROOT / "operations/releases/toolhive_catalog_bootstrap.py"),
        "--bundle", str(bundle),
        "--access", str(ROOT / "clusters/re8ch/components/ops/github-actions-cloud-domains-executor-rbac.yaml"),
        "--output", str(output),
    ], check=True)
    docs = list(yaml.safe_load_all(output.read_text()))
    assert [(doc["kind"], doc["metadata"]["name"]) for doc in docs] == [
        ("Role", "agent-runtime-domain-toolhive-system"),
        ("ConfigMap", "re8ch-approved-tool-catalog"),
    ]
