#!/usr/bin/env python3
"""Export the reviewed, non-secret desired state of migrated business namespaces.

This is intentionally narrow: it only reads the four re8ch-owned destination
namespaces, excludes Secrets and runtime-only resources, and removes cluster
identity/status fields before writing GitOps manifests.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "clusters/re8ch/components/re8ch-business-namespaces"

EXPORTS = {
    "re8ch-ledger": ["deployment", "service", "configmap", "networkpolicy", "httproute"],
    "re8ch-neo4j": [
        "statefulset",
        "service",
        "configmap",
        "persistentvolumeclaim",
        "cronjob",
        "networkpolicy",
        "ingress",
    ],
    "re8ch-anysite": [
        "deployment",
        "service",
        "persistentvolumeclaim",
        "networkpolicy",
        "ingress",
    ],
    "re8ch-dataspace": [
        "deployment",
        "service",
        "persistentvolumeclaim",
        "networkpolicy",
        "ingress",
        "httproute",
    ],
}

SKIP_NAMES = {"kube-root-ca.crt", "tenant-default-deny-cross-tenant"}
LEDGER_ACTIVE = {
    "nuclio-ledger-api-gateway",
    "nuclio-ledger-gnucash-report-runner",
    "nuclio-ledger-wechat-auth",
    "ledger-api-gateway-local",
    "ledger-gnucash-report-runner-local",
    "ledger-wechat-auth-local",
    "ledger-wechat-auth-agent-token-20260727",
    "allow-public-ledger",
    "ledger-public",
}


def kubectl_json(namespace: str, kinds: list[str]) -> dict:
    command = [
        "ssh",
        "restricted-node-6dc3f140f9",
        "sudo",
        "k3s",
        "kubectl",
        "get",
        ",".join(kinds),
        "-n",
        namespace,
        "-o",
        "json",
    ]
    return json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)


def clean(item: dict) -> dict:
    metadata = item["metadata"]
    for key in (
        "creationTimestamp",
        "generation",
        "managedFields",
        "ownerReferences",
        "resourceVersion",
        "uid",
    ):
        metadata.pop(key, None)
    annotations = metadata.get("annotations", {})
    annotations = {
        key: value
        for key, value in annotations.items()
        if key.startswith(("external-dns.alpha.kubernetes.io/", "prometheus.io/"))
    }
    metadata["annotations"] = annotations
    if not annotations:
        metadata.pop("annotations", None)
    item.pop("status", None)

    if item["kind"] == "Service":
        spec = item["spec"]
        for key in ("clusterIP", "clusterIPs", "ipFamilies", "ipFamilyPolicy", "internalTrafficPolicy"):
            spec.pop(key, None)
    elif item["kind"] == "PersistentVolumeClaim":
        item["spec"].pop("volumeName", None)
    elif item["kind"] == "ConfigMap" and metadata["name"] == "ledger-wechat-auth-agent-token-20260727":
        item["binaryData"] = {
            key: base64.b64encode(value.encode()).decode()
            for key, value in item.pop("data", {}).items()
        }
    return item


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    resources: list[str] = []
    for namespace, kinds in EXPORTS.items():
        documents = []
        for item in kubectl_json(namespace, kinds)["items"]:
            name = item["metadata"]["name"]
            if name in SKIP_NAMES:
                continue
            if namespace == "re8ch-ledger" and name not in LEDGER_ACTIVE:
                continue
            documents.append(clean(item))
        path = DESTINATION / f"{namespace}.yaml"
        path.write_text(yaml.safe_dump_all(documents, sort_keys=False), encoding="utf-8")
        resources.append(path.name)

    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": resources,
    }
    (DESTINATION / "kustomization.yaml").write_text(
        yaml.safe_dump(kustomization, sort_keys=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
