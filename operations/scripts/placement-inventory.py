#!/usr/bin/env python3
"""Report fixed-node placement that must be classified before migration.

This is deliberately read-only.  A hostname pin is never auto-rewritten: the
report groups it into reviewed pinned locality, a portable PlacementClass
candidate, or an item needing an owner's decision.
"""
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[2]
PINNED_MARKER = "scheduling.re8ch.com/pinned-reason"

def documents(path):
    try:
        return [x for x in yaml.safe_load_all(path.read_text()) if isinstance(x, dict)]
    except yaml.YAMLError:
        return []

def templates(value):
    if not isinstance(value, dict): return
    if "spec" in value and isinstance(value["spec"], dict):
        spec = value["spec"]
        template = spec.get("template")
        if isinstance(template, dict): yield template
        for child in spec.get("jobTemplate", {}).get("spec", {}).get("template",),:
            if isinstance(child, dict): yield child
    for child in value.values():
        if isinstance(child, (dict, list)): yield from templates(child)
    if isinstance(value, list):
        for child in value: yield from templates(child)

def main():
    rows = []
    for path in ROOT.rglob("*.yaml"):
        if "/vendor/" in str(path) or "/.git/" in str(path): continue
        for doc in documents(path):
            for template in templates(doc):
                metadata, spec = template.get("metadata") or {}, template.get("spec") or {}
                hostname = spec.get("nodeName") or (spec.get("nodeSelector") or {}).get("kubernetes.io/hostname")
                if not hostname: continue
                annotations = metadata.get("annotations", {})
                classification = "pinned" if annotations.get(PINNED_MARKER) else "needs-owner-review"
                placement = annotations.get("scheduling.re8ch.com/placement-class", "-")
                rows.append((classification, str(path.relative_to(ROOT)), hostname, placement))
    print("| Classification | Manifest | Hostname | PlacementClass |\n|---|---|---|---|")
    for row in sorted(rows): print("| " + " | ".join(row) + " |")
    return 0

if __name__ == "__main__": sys.exit(main())
