#!/usr/bin/env python3
"""Report GitOps, tenant-boundary, and runtime-controller drift without writes."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(os.getenv("RE8CH_SERVICE_BACKEND_ANCHOR", Path(__file__).resolve().parents[2]))
CLASSIFICATION = Path(os.getenv("RE8CH_NAMESPACE_CLASSIFICATION", ROOT / "clusters/re8ch/components/toolhive/namespace-access-classification.yaml"))


def run_json(*argv):
    command = list(argv)
    if command and command[0] == "kubectl":
        command.insert(1, "--request-timeout=20s")
    return json.loads(subprocess.run(command, check=True, text=True, capture_output=True, timeout=30).stdout)


def gitops_namespaces():
    names = set()
    for item in yaml.safe_load_all(CLASSIFICATION.read_text()):
        if item and item.get("kind") == "Namespace":
            labels = item.get("metadata", {}).get("labels", {})
            if labels.get("re8ch.com/config-authority", "gitops") == "gitops":
                names.add(item["metadata"]["name"])
    return names


def authority(name, labels, declared):
    if labels.get("tenancy.re8ch.com/managed") == "true" and labels.get("tenancy.re8ch.com/managed-by") == "tenant-control-controller":
        return "tenant-controller"
    if name in declared:
        return "gitops"
    if labels.get("app.kubernetes.io/managed-by") or labels.get("re8ch.com/managed-by"):
        return "runtime-controller"
    return "unclassified"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", choices=("json", "text"), default="json")
    args = parser.parse_args()
    declared = gitops_namespaces()
    namespaces = run_json("kubectl", "get", "namespaces", "-o", "json")["items"]
    flux = run_json("kubectl", "get", "kustomizations.kustomize.toolkit.fluxcd.io", "-A", "-o", "json")["items"]
    boundaries = run_json("kubectl", "get", "resourcequotas,limitranges,networkpolicies,rolebindings", "-A", "-o", "json")["items"]
    boundary_index = {
        (item.get("metadata", {}).get("namespace"), item.get("kind", "").lower(), item.get("metadata", {}).get("name"))
        for item in boundaries
    }
    counts = {key: 0 for key in ("gitops", "tenant-controller", "runtime-controller", "unclassified")}
    findings = []
    for item in namespaces:
        meta = item.get("metadata", {})
        name, labels = meta["name"], meta.get("labels", {})
        owner = authority(name, labels, declared)
        counts[owner] += 1
        if owner == "gitops":
            expected = {"re8ch.com/config-authority": "gitops"}
            if name in declared:
                expected.update({"re8ch.com/access-policy": "toolhive-v1"})
            missing = {key: value for key, value in expected.items() if labels.get(key) != value}
            if missing:
                findings.append({"scope": "gitops", "namespace": name, "reason": "label-drift", "expected": missing})
        elif owner == "tenant-controller":
            tenant = labels.get("tenancy.re8ch.com/tenant")
            for kind, resource in (("resourcequota", "tenant-boundary"), ("limitrange", "tenant-defaults"), ("networkpolicy", "tenant-default-deny-cross-tenant"), ("rolebinding", "tenant-resource-provisioner")):
                if (name, kind, resource) not in boundary_index:
                    findings.append({"scope": "tenant-boundary", "namespace": name, "tenant": tenant, "reason": "missing-boundary", "resource": f"{kind}/{resource}"})
        else:
            findings.append({"scope": owner, "namespace": name, "reason": "ownership-review"})
    for item in flux:
        meta, status = item.get("metadata", {}), item.get("status", {})
        ready = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), {})
        if ready.get("status") != "True":
            findings.append({"scope": "gitops", "namespace": meta.get("namespace"), "resource": f"Kustomization/{meta.get('name')}", "reason": ready.get("reason", "not-ready"), "message": ready.get("message", "")[:240]})
    report = {"authorityCounts": counts, "gitopsDeclared": len(declared), "findings": findings, "driftCount": len(findings)}
    if args.output == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"authorities={counts} drift={len(findings)}")
        for finding in findings:
            print(json.dumps(finding, sort_keys=True))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
