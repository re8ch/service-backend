import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("mirror", ROOT / "operations/workspace-controller/mirror.py")
mirror = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mirror; spec.loader.exec_module(mirror)


def test_mirror_and_task_worktree_are_isolated(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "initial"], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    lock = tmp_path / "lock.yaml"
    lock.write_text(yaml.safe_dump({"modules": {"demo": {"repository": str(source), "commit": commit, "defaultBranch": "main", "dependencies": []}}}))
    controller = mirror.MirrorController(lock, tmp_path / "mirrors", tmp_path / "worktrees")
    assert controller.sync()["result"][0]["lockedCommitPresent"] is True
    created = controller.create("task-1", {"demo"})
    checkout = Path(created["result"][0]["path"])
    (checkout / "README.md").write_text("changed\n")
    assert (source / "README.md").read_text() == "hello\n"
    cleaned = controller.cleanup("task-1", tmp_path / "artifacts")
    assert cleaned["result"][0]["dirty"] is True
    assert not checkout.exists()
