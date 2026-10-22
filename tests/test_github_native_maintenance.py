from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github/dependabot.yml"
SECURITY_WORKFLOW = ROOT / ".github/workflows/github-security-checks.yml"


def dependabot_directories(ecosystem: str) -> set[str]:
    config = yaml.safe_load(DEPENDABOT.read_text())
    return {
        item["directory"]
        for item in config["updates"]
        if item["package-ecosystem"] == ecosystem
    }


def repository_directories(filename: str) -> set[str]:
    return {
        "/" + str(path.parent.relative_to(ROOT))
        for path in ROOT.rglob(filename)
        if "source" not in path.parts and "node_modules" not in path.parts
    }


def test_dependabot_covers_declared_package_manifests():
    assert dependabot_directories("npm") == repository_directories("package-lock.json")
    assert dependabot_directories("gomod") == repository_directories("go.mod")
    assert dependabot_directories("pip") == {"/"}
    assert dependabot_directories("github-actions") == {"/"}


def test_dependabot_updates_are_bounded_and_grouped():
    config = yaml.safe_load(DEPENDABOT.read_text())
    assert config["version"] == 2
    for item in config["updates"]:
        assert item["schedule"]["interval"] == "weekly"
        assert item["open-pull-requests-limit"] <= 5
        assert item["groups"]
        assert set(item["labels"]) == {"dependencies", "automation/dependabot"}


def test_dependency_review_uses_pinned_action_and_github_runner():
    text = SECURITY_WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    job = workflow["jobs"]["dependency-review"]
    assert job["runs-on"] == "ubuntu-latest"
    step = job["steps"][0]
    assert step["uses"] == (
        "actions/dependency-review-action@"
        "a1d282b36b6f3519aa1f3fc636f609c47dddb294"
    )
    assert step["with"]["fail-on-severity"] == "high"
    assert "self-hosted" not in text
