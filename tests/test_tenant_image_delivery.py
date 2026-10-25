"""Check the image handoff against rendered Kubernetes objects, not a planner."""
from pathlib import Path
import re
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_image_handoff_renders_one_immutable_reference_for_the_active_owner():
    rendered = subprocess.check_output(
        ["kubectl", "kustomize", "clusters/re8ch/tenant-mcp-image"], cwd=ROOT, text=True
    )
    objects = list(yaml.safe_load_all(rendered))
    assert len(objects) == 1
    image = objects[0]
    assert image["kind"] == "ConfigMap"
    assert image["metadata"]["namespace"] == "flux-observability-system"
    assert image["metadata"]["labels"]["reconcile.fluxcd.io/watch"] == "Enabled"
    assert re.fullmatch(r"registry\.re8ch\.com/image/re8ch-tenant-mcp@sha256:[0-9a-f]{64}", image["data"]["image"])


def test_image_sync_reuses_verified_source_without_a_second_bundle():
    source = next(d for d in yaml.safe_load_all((ROOT / "clusters/re8ch/flux-system/gotk-sync.yaml").read_text()) if d["kind"] == "GitRepository")
    sync = yaml.safe_load((ROOT / "clusters/re8ch/flux-system/tenant-mcp-image.yaml").read_text())
    assert sync["spec"]["sourceRef"] == {"kind": "GitRepository", "name": source["metadata"]["name"]}
    assert source["spec"]["verify"]["mode"] == "HEAD"
    assert (ROOT / sync["spec"]["path"] / "image.yaml").is_file()
    active = yaml.safe_load((ROOT / "operations/releases/active.yaml").read_text())
    assert "platform/birfucate-metering" not in {m["module"] for m in active["modules"]}
