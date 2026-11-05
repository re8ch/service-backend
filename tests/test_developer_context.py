import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "operations/developer-context/context.py"


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=ROOT, text=True, capture_output=True)


def test_developer_context_is_valid_and_complete():
    result = run("doctor")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    module_count = len(list(ROOT.glob("catalogs/*/modules/*/module.yaml")))
    assert payload["healthy"] is True
    assert payload["nodes"] >= module_count + 1


def test_developer_context_index_is_current():
    result = run("index", "--check")
    assert result.returncode == 0, result.stderr


def test_developer_context_wiki_projection_is_current():
    result = run("wiki", "--check")
    assert result.returncode == 0, result.stderr


def test_collaboration_surfaces_are_not_authorities():
    declaration = yaml.safe_load((ROOT / "workspace/developer-context.yaml").read_text())
    surfaces = declaration["spec"]["surfaces"]
    assert surfaces["wiki"]["authority"] is False
    assert surfaces["gists"]["authority"] is False
    assert all(project["authority"] is False for project in surfaces["projects"])


def test_every_module_has_generated_context_edges():
    graph = json.loads((ROOT / "indexes/developer-context.json").read_text())
    edges = {(edge["from"], edge["to"], edge["type"]) for edge in graph["edges"]}
    for path in ROOT.glob("catalogs/*/modules/*/module.yaml"):
        module = yaml.safe_load(path.read_text())["metadata"]["name"]
        assert (module, "service-backend-context", "context-provided-by") in edges


def test_worktree_creation_rejects_unsafe_branch_before_mutation(tmp_path):
    result = run(
        "worktree-create", "--branch", "feature/unsafe", "--path", str(tmp_path / "task"),
        "--module", "service-backend", "--objective", "test",
    )
    assert result.returncode == 1
    assert "branch must match" in result.stderr
