#!/usr/bin/env python3
"""Build CAS patches from the protected infrastructure bundle without printing values."""
import argparse
import base64
import json
import os
from pathlib import Path

import yaml

SOURCE = "sha256:99c7f741aec926fe7e3839adf488d58c6fb7522d73b42fbd820a9bdbf8fbdc64"
OWNER = "infra-contracts-recovery-20261030"
KEYS = {
    "ai-operations-ssh-config": {"ssh-config", "byoc-known-hosts"},
    "infrastructure-contracts": {"compat-nodes.yaml"},
}


def target(path: Path, name: str, require_empty: bool) -> dict:
    value = json.loads(path.read_text())
    meta = value.get("metadata", {})
    if value.get("kind") != "Secret" or meta.get("name") != name or meta.get("namespace") != "ai-operations":
        raise ValueError("unexpected recovery target")
    if meta.get("labels", {}).get("operations.re8ch.com/recovery") != OWNER:
        raise ValueError("unexpected recovery owner")
    if require_empty and value.get("data"):
        raise ValueError("recovery target is not empty")
    return value


def payloads(rendered: Path) -> dict[str, dict[str, str]]:
    documents = [item for item in yaml.safe_load_all((rendered / "ai-operations-inventory.yaml").read_text()) if item]
    ssh = next(item for item in documents if item["metadata"]["name"] == "ai-operations-ssh-config")
    if ssh["metadata"]["annotations"]["operations.re8ch.com/source-digest"] != SOURCE:
        raise ValueError("source digest mismatch")
    raw = {
        "ai-operations-ssh-config": ssh["data"],
        "infrastructure-contracts": {"compat-nodes.yaml": (rendered / "compat-nodes.yaml").read_text()},
    }
    result = {}
    for name, values in raw.items():
        if set(values) != KEYS[name] or any(not isinstance(value, str) or not value for value in values.values()):
            raise ValueError("projection mismatch")
        result[name] = {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}
    return result


def build(args) -> None:
    targets = {
        "ai-operations-ssh-config": target(args.ssh_target, "ai-operations-ssh-config", True),
        "infrastructure-contracts": target(args.contracts_target, "infrastructure-contracts", True),
    }
    data = payloads(args.rendered)
    args.output.mkdir(mode=0o700)
    for name, value in targets.items():
        meta = value["metadata"]
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": meta["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
            {"op": "test", "path": "/metadata/labels/operations.re8ch.com~1recovery", "value": OWNER},
            {"op": "add", "path": "/data", "value": data[name]},
            {"op": "add", "path": "/metadata/annotations/operations.re8ch.com~1source-digest", "value": SOURCE},
            {"op": "add", "path": "/metadata/annotations/operations.re8ch.com~1recovery-state", "value": "restored"},
        ]
        descriptor = os.open(args.output / f"{name}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(patch, stream)
    print(json.dumps({"planned": sorted(targets), "sourceDigest": SOURCE}))


def verify(args) -> None:
    for name, path in (("ai-operations-ssh-config", args.ssh_target), ("infrastructure-contracts", args.contracts_target)):
        value = target(path, name, False)
        if set(value.get("data", {})) != KEYS[name] or not all(value["data"].values()):
            raise ValueError("restored projection incomplete")
        annotations = value["metadata"].get("annotations", {})
        if annotations.get("operations.re8ch.com/source-digest") != SOURCE or annotations.get("operations.re8ch.com/recovery-state") != "restored":
            raise ValueError("restored projection metadata mismatch")
    print(json.dumps({"restored": sorted(KEYS), "sourceDigest": SOURCE, "complete": True}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--rendered", type=Path)
    parser.add_argument("--ssh-target", type=Path, required=True)
    parser.add_argument("--contracts-target", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.verify:
        verify(args)
    elif args.rendered and args.output:
        build(args)
    else:
        parser.error("build requires --rendered and --output")


if __name__ == "__main__":
    main()
