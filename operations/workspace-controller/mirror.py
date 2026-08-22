#!/usr/bin/env python3
"""restricted-node-6dc3f140f9 mirror cache and task worktree lifecycle.

Credentials remain in Git's configured credential helper/SSH agent. This tool
never prints environment variables, credential files or remote URLs containing
userinfo.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import yaml


def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=False, timeout=1800)


class MirrorController:
    def __init__(self, lock_path: Path, mirror_root: Path, worktree_root: Path):
        self.lock = yaml.safe_load(lock_path.read_text())["modules"]
        self.mirror_root = mirror_root
        self.worktree_root = worktree_root

    def sync(self, modules: set[str] | None = None) -> dict[str, Any]:
        results = []
        self.mirror_root.mkdir(parents=True, exist_ok=True)
        for name, item in sorted(self.lock.items()):
            if modules and name not in modules:
                continue
            mirror = self.mirror_root / f"{name}.git"
            if mirror.exists():
                proc = run(["git", "-C", str(mirror), "remote", "update", "--prune"])
                action = "update"
            else:
                proc = run(["git", "clone", "--mirror", item["repository"], str(mirror)])
                action = "clone"
            present = run(["git", "-C", str(mirror), "cat-file", "-e", f"{item['commit']}^{{commit}}"])
            results.append({"module": name, "action": action, "exitCode": proc.returncode, "lockedCommitPresent": present.returncode == 0})
        return self._envelope("mirror.sync", results)

    def create(self, task_id: str, modules: set[str]) -> dict[str, Any]:
        if not task_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid task id")
        results = []
        for name in sorted(modules):
            item = self.lock[name]
            mirror = self.mirror_root / f"{name}.git"
            target = self.worktree_root / task_id / name
            if target.exists():
                raise FileExistsError(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            proc = run(["git", "clone", "--shared", "--no-checkout", str(mirror), str(target)])
            if proc.returncode == 0:
                proc = run(["git", "-C", str(target), "checkout", "--detach", item["commit"]])
            results.append({"module": name, "path": str(target), "commit": item["commit"], "exitCode": proc.returncode})
        return self._envelope("worktree.create", results)

    def cleanup(self, task_id: str, artifact_root: Path) -> dict[str, Any]:
        target = self.worktree_root / task_id
        artifact_root.mkdir(parents=True, exist_ok=True)
        records = []
        if target.exists():
            for repo in path_repositories(target):
                diff = run(["git", "-C", str(repo), "diff", "--binary", "HEAD"])
                status = run(["git", "-C", str(repo), "status", "--porcelain"])
                record = artifact_root / f"{task_id}-{repo.name}.patch"
                record.write_text(diff.stdout)
                records.append({"module": repo.name, "dirty": bool(status.stdout), "patch": str(record)})
            shutil.rmtree(target)
        return self._envelope("worktree.cleanup", records)

    @staticmethod
    def _envelope(action: str, result: Any) -> dict[str, Any]:
        op = f"op_{uuid.uuid4().hex}"
        return {"operationId": op, "action": action, "result": result, "auditRef": f"audit://workspace/{op}"}


def path_repositories(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.rglob(".git"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--mirrors", type=Path, required=True)
    parser.add_argument("--worktrees", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync"); sync.add_argument("modules", nargs="*")
    create = sub.add_parser("create"); create.add_argument("task_id"); create.add_argument("modules", nargs="+")
    cleanup = sub.add_parser("cleanup"); cleanup.add_argument("task_id"); cleanup.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    controller = MirrorController(args.lock, args.mirrors, args.worktrees)
    if args.command == "sync": result = controller.sync(set(args.modules) or None)
    elif args.command == "create": result = controller.create(args.task_id, set(args.modules))
    else: result = controller.cleanup(args.task_id, args.artifacts)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
