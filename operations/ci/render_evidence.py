#!/usr/bin/env python3
"""Reuse deterministic local Kustomize renders from successful main regression runs.

GitHub artifacts provide storage and run identity. This adapter only computes
local input closure and matches evidence; it never publishes or applies releases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
# Unknown fields may introduce inputs. Fall back to a real render until reviewed.
FIELDS = set("apiVersion kind metadata resources bases components patches patchesStrategicMerge patchesJson6902 configMapGenerator secretGenerator generatorOptions configurations crds openapi namePrefix nameSuffix namespace commonLabels commonAnnotations labels annotations images replicas replacements buildMetadata sortOptions".split())


class Uncacheable(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_key(root: str, toolchain: dict) -> str | None:
    files: dict[str, str] = {}
    visited: set[Path] = set()

    def local(path: Path) -> Path:
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise Uncacheable("symlink")
        resolved = path.resolve()
        if not resolved.is_relative_to(ROOT) or not resolved.exists():
            raise Uncacheable("external or missing input")
        return resolved

    def record(path: Path):
        path = local(path)
        if not path.is_file():
            raise Uncacheable("non-file")
        files[str(path.relative_to(ROOT))] = digest(path.read_bytes())

    def reference(base: Path, value: str):
        if not isinstance(value, str) or "://" in value or "::" in value:
            raise Uncacheable("remote or unsupported reference")
        path = local(base / value)
        if path.is_dir():
            visit(path)
        else:
            record(path)

    def visit(base: Path):
        base = local(base)
        if base in visited:
            return
        visited.add(base)
        definitions = [base / name for name in ("kustomization.yaml", "kustomization.yml", "Kustomization") if (base / name).is_file()]
        if len(definitions) != 1:
            raise Uncacheable("ambiguous definition")
        definition = definitions[0]
        record(definition)
        config = yaml.safe_load(definition.read_text())
        if not isinstance(config, dict) or set(config) - FIELDS:
            raise Uncacheable("unsupported kustomize fields")
        for field in ("resources", "bases", "components", "configurations", "crds"):
            for value in config.get(field, []):
                reference(base, value)
        for field in ("patches", "patchesJson6902"):
            for patch in config.get(field, []):
                if not isinstance(patch, dict):
                    raise Uncacheable("unsupported patch")
                if "path" in patch:
                    reference(base, patch["path"])
        for patch in config.get("patchesStrategicMerge", []):
            if isinstance(patch, str):
                # Multiline inline patches are entirely in the definition.
                if "\n" not in patch:
                    reference(base, patch)
            elif not isinstance(patch, dict):
                raise Uncacheable("unsupported strategic patch")
        for field in ("configMapGenerator", "secretGenerator"):
            for generator in config.get(field, []):
                if set(generator) - {"name", "namespace", "behavior", "files", "envs", "literals", "options"}:
                    raise Uncacheable("unsupported generator")
                for value in generator.get("files", []):
                    reference(base, value.split("=", 1)[-1])
                for value in generator.get("envs", []):
                    reference(base, value)
        for replacement in config.get("replacements", []):
            if "path" in replacement:
                reference(base, replacement["path"])
        if config.get("openapi", {}).get("path"):
            reference(base, config["openapi"]["path"])

    try:
        visit(ROOT / root)
    except (Uncacheable, OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        return None
    identity = {"version": 1, "root": root, "inputs": files, "toolchain": toolchain}
    return digest(json.dumps(identity, sort_keys=True).encode())


def toolchain_identity() -> dict:
    return {
        "kubectl": json.loads(subprocess.check_output(["kubectl", "version", "--client", "-o", "json"], text=True)),
        "python": platform.python_version(), "os": platform.system(), "arch": platform.machine(),
        "yaml": yaml.__version__,
        "validator": digest(Path(__file__).read_bytes()),
        "workflow": digest((ROOT / ".github/workflows/full-regression.yml").read_bytes()),
        "tests": digest((ROOT / "tests/test_render_evidence.py").read_bytes()),
    }


def trusted_run(run: dict, repository: str) -> bool:
    return (run.get("head_branch") == "main" and run.get("conclusion") == "success"
            and run.get("event") in {"schedule", "workflow_dispatch"}
            and run.get("path") == ".github/workflows/full-regression.yml"
            and (run.get("head_repository") or {}).get("full_name") == repository)


def baseline_run() -> str:
    repository = os.environ["GITHUB_REPOSITORY"]
    try:
        response = subprocess.check_output([
            "gh", "api", f"repos/{repository}/actions/workflows/full-regression.yml/runs?branch=main&status=success&per_page=5"
        ], text=True, stderr=subprocess.DEVNULL, timeout=30)
        for run in json.loads(response)["workflow_runs"]:
            if not trusted_run(run, repository):
                continue
            response = subprocess.check_output([
                "gh", "api", f"repos/{repository}/actions/runs/{run['id']}/artifacts"
            ], text=True, stderr=subprocess.DEVNULL, timeout=30)
            if any(a["name"] == "module-render-evidence" and not a["expired"] for a in json.loads(response)["artifacts"]):
                return str(run["id"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        print("Baseline unavailable; rendering selected roots normally.")
    return ""


def render_roots(roots: list[str], baseline: dict, toolchain: dict) -> dict:
    records = {}
    reused = []
    executed = []
    for root in roots:
        key = input_key(root, toolchain)
        previous = baseline.get("records", {}).get(root, {})
        if not isinstance(previous, dict):
            previous = {}
        rendered = previous.get("rendered")
        if (key and previous.get("key") == key and isinstance(rendered, str)
                and previous.get("digest") == digest(rendered.encode())):
            reused.append(root)
        else:
            rendered = subprocess.check_output(["kubectl", "kustomize", root], cwd=ROOT, text=True)
            executed.append(root)
        # A failed render raises before any success evidence can be written.
        if key:
            records[root] = {"key": key, "digest": digest(rendered.encode()), "rendered": rendered}
    return {"version": 1, "records": records, "reused": reused, "executed": executed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--find-baseline", action="store_true")
    parser.add_argument("--roots", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.find_baseline:
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"run_id={baseline_run()}\n")
        return
    baseline = {}
    if args.baseline and args.baseline.is_file():
        try:
            candidate = json.loads(args.baseline.read_text())
            if candidate.get("version") == 1 and isinstance(candidate.get("records"), dict):
                baseline = candidate
        except (ValueError, AttributeError):
            pass
    result = render_roots(json.loads(args.roots.read_text()), baseline, toolchain_identity())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True))
    summary = f"Module render evidence: {len(result['reused'])} reused, {len(result['executed'])} executed."
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as output:
            output.write(summary + "\n\n")
            for status in ("reused", "executed"):
                for root in result[status]:
                    output.write(f"- {status}: `{root}`\n")


if __name__ == "__main__":
    main()
