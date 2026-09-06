from pathlib import Path


ROOT = Path(__file__).parents[1]
EXCLUDED_PREFIXES = (
    "catalogs/qianwen/",
    "inventory/platform/generated/",
    "operations/ansible/roles/qwen_realtime/",
    "runbooks/platform-history/",
)


def test_current_platform_documentation_is_tenant_neutral():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or not (path.suffix.lower() in {".md", ".rst", ".adoc", ".txt"} or path.name.startswith("README")):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith(EXCLUDED_PREFIXES):
            continue
        text = path.read_text(errors="replace").lower()
        if "qwen" in text or "qianwen" in text:
            offenders.append(relative)
    assert offenders == [], "product-specific tenant names leaked into current platform documentation: " + ", ".join(offenders)
