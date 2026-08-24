#!/usr/bin/env python3
"""Fail closed when public DNS, Gateway Route and TLS ownership conflict."""

import argparse
import collections
import json
import pathlib
import subprocess
import sys

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
ROUTE_KINDS = {"HTTPRoute", "GRPCRoute", "TLSRoute", "TCPRoute", "UDPRoute"}


def documents():
    for path in (ROOT / "clusters" / "re8ch" / "components").rglob("*"):
        if path.suffix not in {".yaml", ".yml"}:
            continue
        if "templates" in path.parts:
            continue
        try:
            for item in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if isinstance(item, dict):
                    yield path.relative_to(ROOT).as_posix(), item
        except (OSError, yaml.YAMLError) as exc:
            yield path.relative_to(ROOT).as_posix(), {"_parseError": str(exc)}


def hostnames(item):
    metadata = item.get("metadata", {})
    annotations = metadata.get("annotations", {}) or {}
    kind = item.get("kind")
    if kind in ROUTE_KINDS:
        return item.get("spec", {}).get("hostnames", []) or []
    if kind == "Ingress":
        explicit = annotations.get("external-dns.alpha.kubernetes.io/hostname", "")
        if explicit:
            return [name.strip() for name in explicit.split(",") if name.strip()]
        return [rule.get("host") for rule in item.get("spec", {}).get("rules", []) if rule.get("host")]
    return []


def audit():
    errors, warnings, inventory = [], [], []
    authorities = collections.defaultdict(list)
    routes = collections.defaultdict(list)
    for path, item in documents():
        if "_parseError" in item:
            errors.append(f"{path}: invalid YAML: {item['_parseError']}")
            continue
        kind = item.get("kind", "")
        metadata = item.get("metadata", {})
        labels = metadata.get("labels", {}) or {}
        annotations = metadata.get("annotations", {}) or {}
        names = hostnames(item)
        reference = f"{path}:{kind}/{metadata.get('namespace', 'cluster')}/{metadata.get('name', '?')}"
        if kind in ROUTE_KINDS:
            for name in names:
                routes[name].append(reference)
            if kind == "TLSRoute" and annotations.get("networking.re8ch.com/tls-mode") != "passthrough":
                errors.append(f"{reference}: TLSRoute must declare networking.re8ch.com/tls-mode=passthrough")
        if labels.get("public.re8ch.com/external-dns") == "enabled":
            for name in names:
                authorities[name].append(reference)
        for name in names:
            inventory.append({"hostname": name, "kind": kind, "reference": reference,
                              "dnsAuthority": labels.get("public.re8ch.com/external-dns") == "enabled"})
    for name, owners in sorted(authorities.items()):
        if len(owners) != 1:
            errors.append(f"{name}: expected one DNS authority, found {len(owners)}: {', '.join(owners)}")
        if name not in routes:
            warnings.append(f"{name}: DNS-published but has no Gateway API Route")
    return errors, warnings, sorted(inventory, key=lambda row: (row["hostname"], row["reference"]))


def live_gate(context):
    command = ["kubectl", "--context", context, "get", "httproute,tlsroute,grpcroute", "-A", "-o", "json"]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode:
        return [f"live route query failed: {result.stderr.strip()}"]
    failures = []
    for item in json.loads(result.stdout).get("items", []):
        reference = f"{item['kind']}/{item['metadata'].get('namespace')}/{item['metadata']['name']}"
        conditions = [condition for parent in item.get("status", {}).get("parents", [])
                      for condition in parent.get("conditions", [])]
        for required in ("Accepted", "ResolvedRefs"):
            if not any(condition.get("type") == required and condition.get("status") == "True"
                       for condition in conditions):
                failures.append(f"{reference}: {required} is not True")
    gateway = subprocess.run(
        ["kubectl", "--context", context, "get", "gateway", "-A", "-o", "json"],
        check=False, capture_output=True, text=True,
    )
    if gateway.returncode:
        failures.append(f"live gateway query failed: {gateway.stderr.strip()}")
    else:
        for item in json.loads(gateway.stdout).get("items", []):
            for listener in item.get("status", {}).get("listeners", []):
                accepted = next((condition for condition in listener.get("conditions", [])
                                 if condition.get("type") == "Accepted"), {})
                if accepted.get("status") != "True":
                    failures.append(
                        f"Gateway/{item['metadata']['namespace']}/{item['metadata']['name']}"
                        f" listener/{listener.get('name')}: Accepted is not True ({accepted.get('reason', 'Missing')})"
                    )
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live-context")
    args = parser.parse_args()
    errors, warnings, inventory = audit()
    if args.live_context:
        errors.extend(live_gate(args.live_context))
    if args.json:
        print(json.dumps({"errors": errors, "warnings": warnings, "inventory": inventory}, indent=2))
    else:
        for message in warnings:
            print(f"WARN {message}")
        for message in errors:
            print(f"ERROR {message}")
        print(f"public routing inventory={len(inventory)} warnings={len(warnings)} errors={len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
