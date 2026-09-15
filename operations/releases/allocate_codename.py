#!/usr/bin/env python3
"""Allocate or verify human-readable, module-local immutable release names."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "operations/releases/codenames.yaml"
CATALOG = ROOT / "operations/ci/domain-catalog.yaml"


def module_themes() -> dict[str, str]:
    catalog = yaml.safe_load(CATALOG.read_text())
    return {
        root: config["theme"]
        for config in catalog["domains"].values()
        for root in config["roots"]
    }


def proposed(module: str, state: dict) -> str:
    themes = module_themes()
    if module not in themes:
        raise SystemExit(f"unknown deployable module: {module}")
    record = state["modules"].get(module, {"releases": []})
    index = len(record["releases"])
    words = state["themes"][themes[module]]
    base = words[index % len(words)]
    cycle = index // len(words)
    if cycle > len(state["revision_words"]):
        raise SystemExit("codename vocabulary exhausted; extend the reviewed vocabulary before releasing")
    return base if cycle == 0 else f"{base}-{state['revision_words'][(cycle - 1) % len(state['revision_words'])]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("module", help="Kustomize root, e.g. platform/advanced-fabric")
    parser.add_argument("--claim", action="store_true", help="record the next name in state")
    parser.add_argument("--semver", help="optional machine-facing SemVer annotation")
    args = parser.parse_args()
    if args.semver and not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+].+)?", args.semver):
        raise SystemExit("invalid SemVer annotation")
    state = yaml.safe_load(STATE.read_text())
    name = proposed(args.module, state)
    if not re.fullmatch(r"[a-z]+(?:-[a-z]+)?", name):
        raise SystemExit(f"invalid codename: {name}")
    print(name)
    if args.claim:
        record = state["modules"].setdefault(args.module, {"releases": []})
        release = {"codename": name}
        if args.semver:
            release["semver"] = args.semver
        record["releases"].append(release)
        STATE.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    main()
