#!/usr/bin/env python3
"""RE8CH federated workspace scanner, reconciler and materializer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class WorkspaceError(RuntimeError):
    pass


def operation(action: str, result: Any, *, target: str = "workspace") -> dict[str, Any]:
    op = f"op_{uuid.uuid4().hex}"
    return {"operationId": op, "action": action, "target": target, "result": result, "auditRef": f"audit://workspace/{op}"}


def git(path: Path, *argv: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(path), *argv], text=True, capture_output=True, check=False)
    if check and proc.returncode:
        raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(argv)} failed")
    return proc.stdout.strip()


def repo_root(path: Path) -> Path | None:
    if not path.exists():
        return None
    value = git(path, "rev-parse", "--show-toplevel", check=False)
    return Path(value).resolve() if value else None


def repository_key(url: str) -> str:
    value = url.strip().removesuffix(".git")
    value = value.replace("git@github.com:", "github.com/").replace("git@gitlab.com:", "gitlab.com/")
    value = value.replace("git@github-re8ch-cloud-functions:", "github.com/")
    value = value.removeprefix("https://").removeprefix("ssh://git@")
    return value.lower()


@dataclass(frozen=True)
class Module:
    name: str
    catalog: str
    ownership: str
    repository: str
    branch: str
    mac_path: str
    r640_path: str
    write_policy: str
    dependencies: tuple[str, ...]
    source: Path


class Registry:
    def __init__(self, root: Path):
        self.root = root
        self.modules = self._load_modules()

    def _load_modules(self) -> dict[str, Module]:
        modules: dict[str, Module] = {}
        for path in sorted(self.root.glob("catalogs/*/modules/*/module.yaml")):
            doc = yaml.safe_load(path.read_text())
            meta, spec = doc["metadata"], doc["spec"]
            module = Module(meta["name"], meta["catalog"], spec["ownership"], spec["repository"]["url"], spec["repository"]["defaultBranch"], spec["workspace"]["macPath"], spec["workspace"]["r640Path"], spec["writePolicy"], tuple(spec.get("dependencies", [])), path)
            if module.name in modules:
                raise WorkspaceError(f"duplicate module: {module.name}")
            modules[module.name] = module
        return modules

    def validate(self) -> None:
        paths: dict[tuple[str, str], str] = {}
        for module in self.modules.values():
            for host, location in (("mac", module.mac_path), ("restricted-node-6dc3f140f9", module.r640_path)):
                key = (host, location)
                if key in paths:
                    raise WorkspaceError(f"path collision: {host}:{location}: {paths[key]} and {module.name}")
                paths[key] = module.name
            missing = set(module.dependencies) - self.modules.keys()
            if missing:
                raise WorkspaceError(f"{module.name}: missing dependencies {sorted(missing)}")


class Controller:
    def __init__(self, root: Path):
        self.root = root
        self.registry = Registry(root)
        self.sources = yaml.safe_load((root / "workspace/sources.yaml").read_text())["spec"]

    def list(self) -> dict[str, Any]:
        return operation("workspace.list", [module.__dict__ | {"source": str(module.source.relative_to(self.root))} for module in self.registry.modules.values()])

    def scan(self) -> dict[str, Any]:
        found: dict[str, dict[str, Any]] = {}
        roots = [Path(item) for item in self.sources["localRoots"]]
        for start in roots:
            if not start.exists():
                continue
            candidates = [start]
            if start.is_dir():
                candidates += [path.parent for path in start.rglob(".git") if ".git" not in path.parent.parts]
            for candidate in candidates:
                root = repo_root(candidate)
                if root is None or str(root) in found:
                    continue
                found[str(root)] = self._observe(root)
        return operation("workspace.scan", {"repositories": sorted(found.values(), key=lambda item: item["path"]), "codexProjects": self._codex_projects()})

    def status(self) -> dict[str, Any]:
        rows = []
        mac_root = Path(self.sources["macWorkspaceRoot"])
        lock = self._read_lock()
        for module in self.registry.modules.values():
            path = mac_root / module.mac_path
            observed = self._observe(path) if repo_root(path) else None
            locked = lock.get("modules", {}).get(module.name)
            drift = []
            if observed is None:
                drift.append("missing")
            else:
                if observed["dirty"]:
                    drift.append("dirty")
                if observed["repository"] != module.repository:
                    drift.append("remote_changed")
                if locked and observed["commit"] != locked["commit"]:
                    drift.append("commit_drift")
            rows.append({"module": module.name, "path": str(path), "locked": locked, "observed": observed, "drift": drift})
        return operation("workspace.status", rows)

    def discovery(self) -> dict[str, Any]:
        scan = self.scan()["result"]
        published = {repository_key(module.repository) for module in self.registry.modules.values()}
        candidates = []
        for repo in scan["repositories"]:
            if not repo["repository"] or repository_key(repo["repository"]) in published:
                continue
            candidates.append({**repo, "status": "proposal-required", "autoPublished": False})
        return operation("workspace.discovery", {"candidates": candidates, "count": len(candidates)})

    def resolve_lock(self) -> dict[str, Any]:
        modules = {}
        def resolve(module: Module) -> tuple[Module, str]:
            out = subprocess.run(["git", "ls-remote", module.repository, f"refs/heads/{module.branch}"], text=True, capture_output=True, check=False, timeout=30)
            if out.returncode or not out.stdout.strip():
                raise WorkspaceError(f"cannot resolve {module.name}: {out.stderr.strip()}")
            return module, out.stdout.split()[0]

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(resolve, module) for module in self.registry.modules.values()]
            for future in as_completed(futures):
                module, commit = future.result()
                modules[module.name] = {"repository": module.repository, "defaultBranch": module.branch, "commit": commit, "dependencies": list(module.dependencies)}
        schema_hash = hashlib.sha256((self.root / "schemas/workspace-module.schema.json").read_bytes()).hexdigest()
        return {"apiVersion": "workspace.re8ch.com/v1alpha1", "kind": "WorkspaceLock", "metadata": {"generation": int(time.time()), "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "schemaHash": schema_hash}, "modules": modules}

    def materialize(self, catalogs: set[str], *, dry_run: bool) -> dict[str, Any]:
        lock = self._read_lock()
        target_root = Path(self.sources["macWorkspaceRoot"])
        actions = []
        for module in self.registry.modules.values():
            if catalogs and module.catalog not in catalogs:
                continue
            target = target_root / module.mac_path
            locked = lock.get("modules", {}).get(module.name)
            if not locked:
                raise WorkspaceError(f"{module.name} is not locked")
            if target.exists():
                actions.append({"module": module.name, "action": "preserve-existing", "path": str(target)})
                continue
            actions.append({"module": module.name, "action": "clone", "path": str(target), "commit": locked["commit"]})
            if dry_run:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--filter=blob:none", module.repository, str(target)], check=True)
            subprocess.run(["git", "-C", str(target), "checkout", "--detach", locked["commit"]], check=True)
        return operation("workspace.materialize", {"dryRun": dry_run, "actions": actions}, target=str(target_root))

    def build_indexes(self) -> dict[str, Any]:
        projects, knowledge, tools, deployments = [], [], [], []
        for module in self.registry.modules.values():
            doc = yaml.safe_load(module.source.read_text())
            spec = doc["spec"]
            projects.append({"id": module.name, "catalog": module.catalog, "ownership": module.ownership, "repository": module.repository, "projectType": spec["projectType"]})
            knowledge.extend({"module": module.name, "ref": ref} for ref in spec.get("knowledge", []))
            tools.extend({"module": module.name, "ref": ref} for ref in spec.get("tools", []))
            deployments.extend({"module": module.name, "ref": ref} for ref in spec.get("deployments", []))
        return {"projects": projects, "knowledge": knowledge, "tools": tools, "deployments": deployments}

    def sync(self, *, write: bool) -> dict[str, Any]:
        desired = self.resolve_lock()
        current = self._read_lock()
        changed = current.get("modules", {}) != desired["modules"] or current.get("metadata", {}).get("schemaHash") != desired["metadata"]["schemaHash"]
        if write and changed:
            path = self.root / "workspace/workspace.lock.yaml"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(yaml.safe_dump(desired, sort_keys=True))
            temporary.replace(path)
        return operation("workspace.sync", {"changed": changed, "written": bool(write and changed), "lock": desired})

    def write_indexes(self) -> dict[str, Any]:
        indexes = self.build_indexes()
        output = self.root / "indexes"
        output.mkdir(parents=True, exist_ok=True)
        for name, content in indexes.items():
            path = output / f"{name}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")
            temporary.replace(path)
        return operation("workspace.index", {"written": True, "counts": {name: len(value) for name, value in indexes.items()}})

    def _read_lock(self) -> dict[str, Any]:
        path = self.root / "workspace/workspace.lock.yaml"
        return yaml.safe_load(path.read_text()) if path.exists() else {"modules": {}}

    @staticmethod
    def _observe(path: Path) -> dict[str, Any]:
        return {"path": str(path), "repository": git(path, "remote", "get-url", "origin", check=False), "branch": git(path, "branch", "--show-current", check=False), "commit": git(path, "rev-parse", "HEAD"), "dirty": bool(git(path, "status", "--porcelain", check=False))}

    def _codex_projects(self) -> list[dict[str, Any]]:
        db = Path(os.path.expanduser(self.sources["codexCatalog"]))
        if not db.exists():
            return []
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT cwd,count(*) FROM local_thread_catalog WHERE missing_candidate=0 GROUP BY cwd ORDER BY count(*) DESC").fetchall()
        return [{"path": path, "threads": count, "git": bool(repo_root(Path(path)))} for path, count in rows if path]


def main() -> None:
    parser = argparse.ArgumentParser(prog="re8ch-workspace")
    script = Path(__file__).resolve()
    default_root = script.parents[2] if len(script.parents) > 2 else Path(os.getenv("WORKSPACE_ROOT", "/srv/service-backend"))
    parser.add_argument("--root", type=Path, default=default_root)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "scan", "discovery", "status", "resolve-lock", "doctor"):
        sub.add_parser(name)
    index = sub.add_parser("index")
    index.add_argument("--write", action="store_true")
    sync = sub.add_parser("sync")
    sync.add_argument("--write", action="store_true")
    materialize = sub.add_parser("materialize")
    materialize.add_argument("--catalog", action="append", default=[])
    materialize.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    controller = Controller(args.root)
    if args.command == "doctor":
        controller.registry.validate(); result = operation("workspace.doctor", {"healthy": True, "modules": len(controller.registry.modules)})
    elif args.command == "index": result = controller.write_indexes() if args.write else operation("workspace.index", controller.build_indexes())
    elif args.command == "sync": result = controller.sync(write=args.write)
    elif args.command == "resolve-lock": result = operation("workspace.resolve-lock", controller.resolve_lock())
    elif args.command == "materialize": result = controller.materialize(set(args.catalog), dry_run=not args.apply)
    else: result = getattr(controller, args.command)()
    json.dump(result, sys.stdout, indent=2, default=str); print()


if __name__ == "__main__":
    main()
