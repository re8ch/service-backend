#!/usr/bin/env python3
"""Build concise, reviewable Agent memories from approved module metadata.

The generator reads only repository-root AGENTS.md/README.md files and never
copies credentials, environment files, or arbitrary build output.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import yaml

from workspace import Registry, repository_candidates, repository_key


def git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def repositories(roots: list[Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        candidates = repository_candidates(root)
        for candidate in candidates:
            remote = git(candidate, "remote", "get-url", "origin")
            if remote:
                key = repository_key(remote)
                if key not in result or len(candidate.resolve().parts) < len(result[key].resolve().parts):
                    result[key] = candidate
    return result


def synopsis(path: Path) -> str:
    if not path.exists():
        return ""
    lines = []
    in_fence = False
    for raw in path.read_text(errors="replace").splitlines()[:160]:
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith(("![", "[![", "<", "---")):
            continue
        line = re.sub(r"^#+\s*", "", line)
        if len(line) < 4:
            continue
        lines.append(line)
        if sum(map(len, lines)) >= 900:
            break
    return " ".join(lines)[:1000]


def render(module, document: dict, checkout: Path | None) -> str:
    spec = document["spec"]
    existing_agents = checkout / "AGENTS.md" if checkout else None
    readme = checkout / "README.md" if checkout else None
    purpose = synopsis(readme) if readme else ""
    return "\n".join([
        f"# {module.name} Agent MEMORY",
        "",
        "> Generated from the GitLab workspace catalog and repository-root documentation. Product source remains authoritative.",
        "",
        f"- Catalog: `{module.catalog}`",
        f"- Ownership: `{module.ownership}`; write policy: `{module.write_policy}`",
        f"- Repository: `{module.repository}` (`{module.branch}`)",
        f"- Catalog source: `{module.source.parent.relative_to(module.source.parents[4]) / module.source_path}` ({module.source_kind})",
        f"- Metadata policy: `{module.metadata_policy}`",
        f"- Project type: `{spec['projectType']}`; lifecycle: `{spec['lifecycle']}`",
        f"- Knowledge: {', '.join(f'`{item}`' for item in spec.get('knowledge', [])) or 'none declared'}",
        f"- Tools: {', '.join(f'`{item}`' for item in spec.get('tools', [])) or 'none declared'}",
        f"- Deployments: {', '.join(f'`{item}`' for item in spec.get('deployments', [])) or 'none declared'}",
        f"- Dependencies: {', '.join(f'`{item}`' for item in spec.get('dependencies', [])) or 'none'}",
        "",
        "## Working rules",
        "",
        "Read the repository-root AGENTS.md first when present, then README and declared knowledge. Work in a project-local Git worktree and preserve dirty checkouts. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.",
        "",
        "## Repository synopsis",
        "",
        purpose or "No local repository-root README was available during generation; inspect the locked checkout before changing code.",
        "",
        f"Existing repository AGENTS: `{'yes' if existing_agents and existing_agents.exists() else 'no'}`",
        "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    registry = Registry(args.root)
    sources = yaml.safe_load((args.root / "workspace/sources.yaml").read_text())["spec"]
    found = repositories([Path(item) for item in sources["localRoots"]])
    changed = []
    for module in registry.modules.values():
        document = yaml.safe_load(module.source.read_text())
        content = render(module, document, found.get(repository_key(module.repository)))
        target = module.source.parent / "MEMORY.md"
        if not target.exists() or target.read_text(errors="replace") != content:
            changed.append(str(target.relative_to(args.root)))
            if args.write:
                target.write_text(content)
    print(yaml.safe_dump({"modules": len(registry.modules), "changed": changed, "written": args.write}, sort_keys=False))


if __name__ == "__main__":
    main()
