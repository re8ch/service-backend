import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "promote_tenant_mcp", ROOT / "operations/releases/promote_tenant_mcp.py"
)
PROMOTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTION)


def additions(image: str) -> dict[str, str]:
    return dict(PROMOTION.changes(image, ROOT))


def test_promotion_updates_static_gitops_derivatives_atomically():
    image = "registry.re8ch.com/image/re8ch-tenant-mcp@sha256:" + "a" * 64
    proposed = additions(image)
    assert set(proposed) == {
        str(PROMOTION.SOURCE),
        str(PROMOTION.BUNDLE),
        str(PROMOTION.INVENTORY),
    }
    assert proposed[str(PROMOTION.SOURCE)].count(image) == 1
    assert proposed[str(PROMOTION.BUNDLE)].count(image) == 1

    release = next(
        item
        for item in yaml.safe_load_all(proposed[str(PROMOTION.SOURCE)])
        if isinstance(item, dict) and item.get("kind") == "HelmRelease"
    )
    inventory = yaml.safe_load(proposed[str(PROMOTION.INVENTORY)])
    record = next(item for item in inventory["releases"] if item["id"] == PROMOTION.RELEASE_ID)
    assert record["values"]["inlineSha256"] == PROMOTION.digest(release["spec"]["values"])
    assert record["declarationSha256"] == PROMOTION.digest(release)


def test_promotion_is_a_noop_for_current_image():
    current = PROMOTION.IMAGE_PATTERN.search((ROOT / PROMOTION.SOURCE).read_text()).group()
    assert PROMOTION.changes(current, ROOT) == []


def test_promotion_rejects_mutable_image():
    try:
        PROMOTION.changes("registry.re8ch.com/image/re8ch-tenant-mcp:latest", ROOT)
    except ValueError as error:
        assert "immutable" in str(error)
    else:
        raise AssertionError("mutable tenant MCP image was accepted")
