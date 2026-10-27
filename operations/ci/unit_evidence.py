"""Bounded unit success evidence: exact inputs, one trusted baseline, fail closed."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
import time
import zipfile
from datetime import datetime

from . import render_evidence, unit_catalog

VERSION = 1
MAX_BYTES = 2 * 1024 * 1024
MAX_AGE = 14 * 86400
ARTIFACT = "unit-success-evidence"
SOURCES = {"main-unit-baseline.yml": ("push",), "full-regression.yml": ("schedule", "workflow_dispatch")}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def candidates(catalog):
    return {test: unit for test, unit in catalog["validation_units"].items() if unit["reuse"] == "candidate"}


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def api(path, binary=False):
    result = subprocess.run(["gh", "api", *(["--allow-escape-sequences"] if binary else []), path], capture_output=True, timeout=30, check=True)
    return result.stdout if binary else json.loads(result.stdout)


def latest_baseline(repository, now=None, request=api):
    """Inspect only latest completed run per allowed event; download at most one artifact.

    A newer failed regression/push is a barrier: never resurrect older success.
    No history search for missing, malformed or mismatching evidence.
    """
    now = time.time() if now is None else now
    try:
        runs = []
        queries = [(workflow, event) for workflow, events in SOURCES.items() for event in events]
        def latest(query):
            workflow, event = query
            return request(f"repos/{repository}/actions/workflows/{workflow}/runs?branch=main&event={event}&status=completed&per_page=1")
        # Three independent metadata reads, one selected artifact, no history traversal.
        with ThreadPoolExecutor(max_workers=3) as pool:
            pages = list(pool.map(latest, queries))
        for (workflow, event), page in zip(queries, pages):
            if not page["workflow_runs"]:
                continue
            run = page["workflow_runs"][0]
            if (run["path"] != f".github/workflows/{workflow}"
                    or run["event"] != event or run["head_branch"] != "main"
                    or run["head_repository"]["full_name"] != repository
                    or run["repository"]["full_name"] != repository or run["status"] != "completed"):
                return None, "untrusted-run"
            runs.append(run)
        if not runs:
            return None, "no-baseline"
        run = max(runs, key=lambda r: (r["created_at"], r["id"]))
        if run["conclusion"] != "success":
            return None, "latest-run-not-successful"
        if not 0 <= now - timestamp(run["created_at"]) <= MAX_AGE:
            return None, "expired-baseline"
        artifacts = request(f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
        matches = [a for a in artifacts if a["name"] == ARTIFACT]
        if len(matches) != 1:
            return None, "missing-baseline"
        artifact = matches[0]
        if artifact["expired"] or artifact["size_in_bytes"] > MAX_BYTES:
            return None, "expired-or-oversized-baseline"
        identity = artifact["workflow_run"]
        if identity["id"] != run["id"] or identity["head_sha"] != run["head_sha"]:
            return None, "artifact-identity-mismatch"
        raw = request(f"repos/{repository}/actions/artifacts/{artifact['id']}/zip", binary=True)
        if len(raw) > MAX_BYTES or artifact.get("digest") != "sha256:" + hashlib.sha256(raw).hexdigest():
            return None, "artifact-digest-mismatch"
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != "evidence.json" or members[0].file_size > MAX_BYTES:
                return None, "invalid-archive"
            evidence = json.loads(archive.read(members[0]))
        expected = {"repository": repository, "run_id": run["id"], "sha": run["head_sha"], "attempt": run["run_attempt"]}
        if evidence["version"] != VERSION or evidence["publisher"] != expected or not isinstance(evidence["units"], dict) or len(evidence["units"]) > 14:
            return None, "incompatible-baseline"
        return evidence, "latest-trusted-baseline"
    except (KeyError, TypeError, ValueError, AttributeError, RuntimeError, OSError, subprocess.SubprocessError, zipfile.BadZipFile):
        return None, "baseline-unavailable-or-invalid"


class Inputs:
    def __init__(self, root):
        self.root = Path(root)
        self.files = {}
        self.gitlinks = {}
        for entry in subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root).split(b"\0"):
            if entry:
                metadata, path = entry.split(b"\t", 1)
                mode, sha, _ = metadata.decode().split()
                if mode == "160000":
                    self.gitlinks[os.fsdecode(path)] = sha
        # Include filesystem additions/deletions, including ignored manifest files.
        # Runtime Python/pytest caches and Git internals are not validation inputs.
        def unreadable(error):
            raise error
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
            base = Path(directory)
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".pytest_cache"}]
            for name in [*dirs, *files]:
                path = base / name
                relative = path.relative_to(root).as_posix()
                if relative in self.gitlinks:
                    self.files[relative] = ["gitlink", self.gitlinks[relative]]
                    if name in dirs:
                        dirs.remove(name)
                elif path.is_symlink():
                    self.files[relative] = ["symlink", os.readlink(path)]
                elif path.is_file():
                    self.files[relative] = ["file", None]
                elif path.is_dir():
                    self.files[relative] = ["directory"]
        for path, sha in self.gitlinks.items():
            checkout = self.root / path
            if (checkout / '.git').exists():
                head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip()
                dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=checkout))
                self.files[path] = ['gitlink', sha, head, dirty]
            else:
                self.files[path] = ['gitlink', sha, None, False]

    def fingerprint(self, test, catalog, environment):
        unit = catalog["validation_units"][test]
        if reason := unit.get("reuse_blocked"):
            return None, reason
        patterns = unit_catalog.inputs_for(test, catalog)
        selected = {}
        import fnmatch
        for path, value in self.files.items():
            if not any(fnmatch.fnmatchcase(path, p) or path == p.removesuffix('/**') for p in patterns):
                continue
            if value[0] == "gitlink" and (value[3] or value[2] not in (None, value[1])):
                return None, "dirty-or-divergent-gitlink"
            if value[0] == "symlink":
                return None, "symlink-input"
            if value[0] == "file" and value[1] is None:
                value[1] = hashlib.sha256((self.root / path).read_bytes()).hexdigest()
            selected[path] = value
        missing = [p for p in patterns if not any(c in p for c in '*?[') and p not in selected]
        if missing:
            return None, "missing-declared-input"
        tools = environment["tools"]
        for name in unit["tools"]:
            if name != "python" and (not tools.get(name, {}).get("available") or not tools[name].get("version")):
                return None, "unavailable-toolchain"
        closures = {}
        previous_root = render_evidence.ROOT
        try:
            render_evidence.ROOT = self.root
            for root in unit.get("roots", []):
                key = render_evidence.input_key(root, environment)
                if key is None:
                    return None, "unclosed-render-inputs"
                closures[root] = key
        finally:
            render_evidence.ROOT = previous_root
        return digest({"version": VERSION, "catalog_version": catalog["version"], "declaration": unit, "defaults": catalog["unit_defaults"],
                       "inputs": selected, "closures": closures, "environment": environment}), "exact-inputs"


def match(record, fingerprint, cases, now=None):
    now = time.time() if now is None else now
    try:
        if record["fingerprint"] != fingerprint:
            return False, "fingerprint-mismatch"
        if len(cases) > 10000 or record["cases"] != sorted(cases) or not cases or len(cases) != len(set(cases)):
            return False, "case-set-mismatch"
        if record["result"] != "passed" or not 0 <= now - record["verified_at"] <= MAX_AGE:
            return False, "expired-or-unsuccessful-unit"
        origin = record["origin"]
        if (not isinstance(origin["run_id"], int) or origin["run_id"] <= 0
                or not re.fullmatch("[0-9a-f]{40}", origin["sha"]) or record["seconds"] < 0):
            return False, "invalid-origin"
        return True, "exact-success-evidence"
    except (KeyError, TypeError, ValueError):
        return False, "invalid-unit-evidence"
