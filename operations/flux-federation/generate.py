#!/usr/bin/env python3
"""Generate isolated Flux controller stacks and split cluster-scoped guardrails."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

import yaml


DOMAINS = {
    "agent-runtime": ("flux-agent-system", "re8ch-ai/agent-gitops"),
    "identity-access": ("flux-identity-system", "re8ch/identity-gitops"),
    "observability-metering": ("flux-observability-system", "siren-observer/observability-gitops"),
    "networking": ("flux-network-system", "pillars-infra/network-gitops"),
}
CONTROLLERS = {"source-controller", "kustomize-controller", "helm-controller", "notification-controller"}
TOOLKIT_GROUPS = [
    "source.toolkit.fluxcd.io",
    "kustomize.toolkit.fluxcd.io",
    "helm.toolkit.fluxcd.io",
    "notification.toolkit.fluxcd.io",
]

# Cluster-scoped resources that are intentionally published by an autonomous
# domain repository after the initial inventory split. Keep this list explicit:
# it is the reviewed authority boundary, not a wildcard escape hatch.
EXTERNAL_CLUSTER_AUTHORITY = {
    "networking": {
        "cilium.io": {"ciliumpodippools"},
    },
}


def load_documents(path: Path) -> list[dict]:
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def dump_documents(path: Path, documents: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n".join(yaml.safe_dump(item, sort_keys=False) for item in documents))


def discovery(path: Path) -> dict[tuple[str, str], tuple[str, bool]]:
    lines = path.read_text().splitlines()
    header = lines[0]
    starts = [header.index(name) for name in ("NAME", "SHORTNAMES", "APIVERSION", "NAMESPACED", "KIND", "VERBS")]
    result = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        values = [line[starts[i]: starts[i + 1]].strip() for i in range(len(starts) - 1)]
        values.append(line[starts[-1]:].strip())
        name, _, api_version, namespaced, kind, _ = values
        result[(api_version, kind)] = (name, namespaced == "true")
    return result


def api_group(api_version: str) -> str:
    return "" if "/" not in api_version else api_version.split("/", 1)[0]


def controller_objects(root: Path, namespace: str, domain: str) -> list[dict]:
    source = load_documents(root / "clusters/re8ch/flux-system/gotk-components.yaml")
    selected = []
    for item in source:
        kind = item["kind"]
        name = item.get("metadata", {}).get("name")
        if kind in {"ServiceAccount", "Service", "Deployment"} and name in CONTROLLERS:
            obj = copy.deepcopy(item)
        elif kind == "NetworkPolicy" and name in {"allow-egress", "allow-scraping", "allow-webhooks"}:
            obj = copy.deepcopy(item)
        elif kind == "ResourceQuota" and name == "critical-pods-flux-system":
            obj = copy.deepcopy(item)
            obj["metadata"]["name"] = f"critical-pods-{namespace}"
        else:
            continue
        obj["metadata"]["namespace"] = namespace
        labels = obj["metadata"].setdefault("labels", {})
        labels["gitops.re8ch.com/domain"] = domain
        if kind == "Deployment":
            pod_labels = obj["spec"]["template"]["metadata"].setdefault("labels", {})
            pod_labels["gitops.re8ch.com/domain"] = domain
            container = obj["spec"]["template"]["spec"]["containers"][0]
            container["args"] = [
                "--watch-all-namespaces=false" if arg == "--watch-all-namespaces=true" else
                arg.replace("notification-controller.flux-system.svc.cluster.local", f"notification-controller.{namespace}.svc.cluster.local")
                for arg in container.get("args", [])
            ]
            container.setdefault("env", []).append({"name": "CONTROLLER_DOMAIN", "value": domain})
            obj["spec"]["replicas"] = 1
            pod_spec = obj["spec"]["template"]["spec"]
            pod_spec["affinity"] = {
                "podAntiAffinity": {
                    "preferredDuringSchedulingIgnoredDuringExecution": [{
                        "weight": 100,
                        "podAffinityTerm": {
                            "topologyKey": "kubernetes.io/hostname",
                            "labelSelector": {
                                "matchExpressions": [{
                                    "key": "gitops.re8ch.com/domain",
                                    "operator": "Exists",
                                }],
                            },
                        },
                    }],
                },
            }
        selected.append(obj)
    selected.append({
        "apiVersion": "policy/v1", "kind": "PodDisruptionBudget",
        "metadata": {"name": "kustomize-controller", "namespace": namespace, "labels": {"gitops.re8ch.com/domain": domain}},
        "spec": {"minAvailable": 1, "selector": {"matchLabels": {"app": "kustomize-controller"}}},
    })
    return selected


def internal_rbac(namespace: str, domain: str) -> list[dict]:
    subjects = [{"kind": "ServiceAccount", "name": name, "namespace": namespace} for name in sorted(CONTROLLERS)]
    return [
        {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
            "metadata": {"name": "flux-controller-runtime", "namespace": namespace, "labels": {"gitops.re8ch.com/domain": domain}},
            "rules": [
                {"apiGroups": TOOLKIT_GROUPS, "resources": ["*"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
                {"apiGroups": [""], "resources": ["secrets", "configmaps", "serviceaccounts", "events"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
                {"apiGroups": ["coordination.k8s.io"], "resources": ["leases"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
            "metadata": {"name": "flux-controller-runtime", "namespace": namespace, "labels": {"gitops.re8ch.com/domain": domain}},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "flux-controller-runtime"},
            "subjects": subjects,
        },
    ]


def application_rbac(domain: str, namespace: str, documents: list[dict], resources: dict) -> list[dict]:
    namespaced: dict[str, set[str]] = {}
    cluster: dict[str, set[str]] = {}
    target_namespaces = set()
    for item in documents:
        key = (item["apiVersion"], item["kind"])
        if key not in resources:
            raise SystemExit(f"missing discovery for {domain}: {key}")
        resource, is_namespaced = resources[key]
        group = api_group(item["apiVersion"])
        bucket = namespaced if is_namespaced else cluster
        bucket.setdefault(group, set()).add(resource)
        if is_namespaced:
            target_namespaces.add(item.get("metadata", {}).get("namespace", "default"))
    for group, resource_names in EXTERNAL_CLUSTER_AUTHORITY.get(domain, {}).items():
        cluster.setdefault(group, set()).update(resource_names)
    rules = lambda groups: [
        {"apiGroups": [group], "resources": sorted(values), "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]}
        for group, values in sorted(groups.items())
    ]
    namespaced_rules = rules(namespaced)
    # Kubernetes rejects creation or mutation of an RBAC role when the caller
    # does not already hold every permission that role grants. Give the domain
    # reconciler escalation and binding rights only for the exact Role names in
    # its compiled bundle, rather than broad workload permissions or wildcard
    # RBAC authority.
    managed_roles = sorted({
        item["metadata"]["name"]
        for item in documents
        if item["apiVersion"] == "rbac.authorization.k8s.io/v1" and item["kind"] == "Role"
    })
    if managed_roles:
        namespaced_rules.append({
            "apiGroups": ["rbac.authorization.k8s.io"],
            "resources": ["roles"],
            "resourceNames": managed_roles,
            "verbs": ["escalate", "bind"],
        })
    role_name = f"flux-{domain}-namespaced"
    cluster_name = f"flux-{domain}-cluster"
    subjects = [
        {"kind": "ServiceAccount", "name": "kustomize-controller", "namespace": namespace},
        {"kind": "ServiceAccount", "name": "helm-controller", "namespace": namespace},
    ]
    result = [{
        "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
        "metadata": {"name": role_name, "labels": {"gitops.re8ch.com/domain": domain}}, "rules": namespaced_rules,
    }]
    for target in sorted(target_namespaces):
        result.append({
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
            "metadata": {"name": role_name, "namespace": target, "labels": {"gitops.re8ch.com/domain": domain}},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role_name},
            "subjects": subjects,
        })
    if cluster:
        result.extend([
            {
                "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
                "metadata": {"name": cluster_name, "labels": {"gitops.re8ch.com/domain": domain}}, "rules": rules(cluster),
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
                "metadata": {"name": cluster_name, "labels": {"gitops.re8ch.com/domain": domain}},
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": cluster_name},
                "subjects": subjects,
            },
        ])
    return result


def relocate_flux_objects(documents: list[dict], namespace: str) -> list[dict]:
    """Keep all Flux custom resources in the namespace watched by their controller stack."""
    relocated = []
    for original in documents:
        item = copy.deepcopy(original)
        group = api_group(item["apiVersion"])
        if group not in TOOLKIT_GROUPS:
            relocated.append(item)
            continue
        previous = item.setdefault("metadata", {}).get("namespace", "default")
        item["metadata"]["namespace"] = namespace
        spec = item.setdefault("spec", {})
        if item["kind"] == "HelmRelease":
            spec.setdefault("targetNamespace", previous)
            spec.setdefault("storageNamespace", previous)
            if "chart" in spec:
                spec["chart"].setdefault("spec", {}).setdefault("sourceRef", {})["namespace"] = namespace
            if "chartRef" in spec:
                spec["chartRef"]["namespace"] = namespace
        if item["kind"] == "Kustomization" and "sourceRef" in spec:
            spec["sourceRef"]["namespace"] = namespace
        relocated.append(item)
    return relocated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--api-resources", type=Path, required=True)
    parser.add_argument("--external-root", type=Path, required=True)
    args = parser.parse_args()
    resources = discovery(args.api_resources)
    controllers = []
    guardrails: dict[str, list[dict]] = {domain: [] for domain in DOMAINS}
    seen_guardrails = set()
    for domain, (namespace, _) in DOMAINS.items():
        original = load_documents(args.root / f"clusters/re8ch/domains/{domain}/resources.yaml")
        external = []
        for item in original:
            _, is_namespaced = resources[(item["apiVersion"], item["kind"])]
            if is_namespaced:
                external.append(item)
                continue
            identity = (item["apiVersion"], item["kind"], item.get("metadata", {}).get("name"))
            if item["kind"] == "Namespace" or identity in seen_guardrails:
                continue
            seen_guardrails.add(identity)
            guardrails[domain].append(item)
        external = relocate_flux_objects(external, namespace)
        repo_dir = next(path for path in args.external_root.glob("*-gitops") if yaml.safe_load((path / "policies/domain.yaml").read_text())["domain"] == domain)
        dump_documents(repo_dir / "clusters/re8ch/resources.yaml", external)
        controllers.extend(controller_objects(args.root, namespace, domain))
        controllers.extend(internal_rbac(namespace, domain))
        controllers.extend(application_rbac(domain, namespace, external, resources))
    dump_documents(args.root / "clusters/re8ch/flux-federation/controllers.yaml", controllers)
    guardrail_root = args.root / "clusters/re8ch/flux-federation/guardrails"
    for domain, documents in guardrails.items():
        dump_documents(guardrail_root / f"{domain}.yaml", documents)
    legacy = args.root / "clusters/re8ch/flux-federation/global-guardrails.yaml"
    legacy.unlink(missing_ok=True)
    print({"controllers": len(controllers), "guardrails": {domain: len(items) for domain, items in guardrails.items()}})


if __name__ == "__main__":
    main()
