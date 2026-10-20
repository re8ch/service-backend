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
R640_LOCAL_ENDPOINTS = {
    "agent-runtime": {"artifacts": 18090, "events": 18292},
    "observability-metering": {"artifacts": 28090, "events": 28292},
}
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

# Namespaced workload resources Helm needs to inspect even when they are
# controller-created and therefore absent as top-level objects in the bundle.
EXTERNAL_NAMESPACED_AUTHORITY = {
    "agent-runtime": {"apps": {"replicasets"}},
    "observability-metering": {
        "apps": {"replicasets"},
        "cert-manager.io": {"certificates", "issuers"},
        "coordination.k8s.io": {"leases"},
    },
    "networking": {
        "apps": {"replicasets"},
        "gateway.networking.k8s.io": {"referencegrants"},
    },
}

# Namespaced custom resources rendered by an adopted chart but absent from the
# compiled domain bundle. Keep these rules object-name scoped as well as bound
# only in the explicit target namespaces below.
EXTERNAL_NAMESPACED_NAMED_AUTHORITY = {
    "observability-metering": {
        "toolhive.stacklok.dev": {
            "mcpexternalauthconfigs": {"admin-vmcp-backend-auth", "tenant-vmcp-backend-auth"},
            "mcpserverentries": {"admin-api-backend", "tenant-api-backend"},
        },
    },
}

# Name-scoped escalation/binding for Roles rendered inside product Helm charts.
# These do not appear as top-level objects in the compiled domain bundle.
EXTERNAL_NAMESPACED_ROLE_AUTHORITY = {
    "observability-metering": {
        "tenant-api-credential-read",
        "tenant-native-platform-tool-read",
        "tenant-platform-tool-upstream-read",
        "tenant-provider-credential-write",
        "tenant-provider-database-metrics-anysite",
        "tenant-provider-database-metrics-secretary",
        "tenant-provider-grafana-read",
        "tenant-provider-harbor-read",
        "otel-operator-opentelemetry-operator-leader-election",
        "victoria-metrics-operator",
    },
    "networking": {
        "among-clusters-credential-broker",
        "cilium-bgp-control-plane-secrets",
        "cilium-config-agent",
        "cilium-gateway-secrets",
        "cilium-operator-gateway-secrets",
        "cilium-operator-tlsinterception-secrets",
        "cilium-operator-ztunnel",
        "cilium-tlsinterception-secrets",
        "public-edge-manager-cloudflare",
    },
}

# Namespaces created by an adopted Helm release are not necessarily present as
# top-level objects in the compiled domain bundle. Keep these bindings explicit
# so a chart cannot expand Network Flux into arbitrary namespaces.
EXTERNAL_TARGET_NAMESPACES = {
    # CosyVoice is an adopted ROCm workload whose private voice assets live on
    # NAS and therefore are intentionally absent from the legacy compiled
    # service-backend bundle. Agent Flux still needs a namespace-scoped binding
    # before it can reconcile the authoritative agent-gitops declaration.
    "agent-runtime": {"catalog", "cosyvoice"},
    # Billing remains a separate database/runtime boundary, but its lifecycle
    # is governed alongside User Hub and organization identity until a
    # dedicated Billing Flux domain is introduced.
    "identity-access": {"supabase-billing"},
    "observability-metering": {
        "ai-operations",
        "artchais",
        "ledger",
        "observability-ai",
        "opentelemetry-operator",
        "re8ch-anysite",
        "re8ch-secretary",
        "toolhive-system",
    },
    "networking": {"cilium-secrets"},
}

# Helm charts may own cluster-scoped objects that are absent from the compiled
# domain bundle. Grant the Helm controller only the exact existing objects each
# product release is allowed to upgrade. CRD creation/deletion remains a main
# Flux guardrail responsibility.
NETWORK_HELM_PRODUCT_AUTHORITY = {
    "advanced-fabric": {
        "crds": {
            "advancedfabrics.networking.advfab.org",
            "networkinterventions.networking.advfab.org",
            "networkobservationepisodes.networking.advfab.org",
            "networkpathassessments.networking.advfab.org",
            "networktradeoffestimates.networking.advfab.org",
            "routerosnodes.networking.advfab.org",
            "tradeofftriangles.networking.advfab.org",
            "trafficpolicies.networking.advfab.org",
        },
        "apiGroup": "networking.advfab.org",
        "resources": {
            "advancedfabrics", "networkinterventions", "networkobservationepisodes",
            "networkpathassessments", "networktradeoffestimates", "routerosnodes",
            "tradeofftriangles", "trafficpolicies",
        },
        "clusterroles": {"advanced-fabric", "advanced-fabric-dns"},
        "clusterrolebindings": {"advanced-fabric", "advanced-fabric-dns"},
        "extraRules": [{
            "apiGroups": ["networking.k8s.io"],
            "resources": ["ingressclasses"],
            "resourceNames": ["advanced-fabric"],
            "verbs": ["get", "update", "patch"],
        }],
    },
    "among-clusters": {
        "crds": {
            "authenticatedlinks.peering.re8ch.com", "clusteridentities.peering.re8ch.com",
            "importedservices.peering.re8ch.com", "managedaccessgrants.peering.re8ch.com",
            "peerpolicies.peering.re8ch.com", "peers.peering.re8ch.com",
            "serviceadvertisements.peering.re8ch.com",
        },
        "crdLookups": {
            "collaborationclusters.collaboration.re8ch.com",
            "collaborationevents.collaboration.re8ch.com",
            "collaborationrelationships.collaboration.re8ch.com",
            "publishedservices.collaboration.re8ch.com",
        },
        "apiGroup": "peering.re8ch.com",
        "resources": {
            "authenticatedlinks", "clusteridentities", "importedservices",
            "managedaccessgrants", "peerpolicies", "peers", "serviceadvertisements",
        },
        "clusterroles": {
            "among-clusters-agent-observer", "among-clusters-credential-broker-auth-review",
            "among-clusters-hub", "among-clusters-view",
        },
        "clusterrolebindings": {
            "among-clusters-agent-observer", "among-clusters-credential-broker-auth-review",
            "among-clusters-hub",
        },
    },
    "cilium": {
        # These cluster-scoped objects already exist. Network Flux may adopt
        # and upgrade the release, but it cannot create or delete global
        # policy, RBAC, namespaces, or GatewayClasses.
        "clusterroles": {"cilium", "cilium-operator", "hubble-ui"},
        "clusterrolebindings": {"cilium", "cilium-operator", "hubble-ui"},
        "extraRules": [
            {
                "apiGroups": ["admissionregistration.k8s.io"],
                "resources": ["validatingadmissionpolicies"],
                "resourceNames": ["gateway.cilium.io"],
                "verbs": ["get", "update", "patch"],
            },
            {
                "apiGroups": ["admissionregistration.k8s.io"],
                "resources": ["validatingadmissionpolicybindings"],
                "resourceNames": ["gateway.cilium.io"],
                "verbs": ["get", "update", "patch"],
            },
            {
                "apiGroups": ["gateway.networking.k8s.io"],
                "resources": ["gatewayclasses"],
                "resourceNames": ["cilium"],
                "verbs": ["get", "update", "patch"],
            },
            {
                "apiGroups": [""],
                "resources": ["namespaces"],
                "resourceNames": ["cilium-secrets"],
                "verbs": ["get", "update", "patch"],
            },
        ],
    },
    "public-edge": {
        "crds": {"publicedges.networking.re8ch.com"},
        "apiGroup": "networking.re8ch.com",
        "resources": {"publicedges"},
        "clusterroles": {"public-edge-manager", "public-edge-manager-cli-read"},
        "clusterrolebindings": {"public-edge-manager", "public-edge-manager-cli-read"},
    },
}

DOMAIN_HELM_PRODUCT_AUTHORITY = {
    "agent-runtime": {
        "toolhive": {
            "clusterroles": {"toolhive-operator-manager-role"},
            "clusterrolebindings": {"toolhive-operator-manager-rolebinding"},
        },
    },
    "observability-metering": {
        "birfucate-metering": {
            "crds": {
                "bifurcationpaths.finops.re8ch.com", "clusterconnections.finops.re8ch.com",
                "clusterregistrations.finops.re8ch.com", "consumables.finops.re8ch.com",
                "consumptionbindings.finops.re8ch.com", "serviceshards.finops.re8ch.com",
                "tenantidentitybindings.tenancy.re8ch.com", "tenantnamespaces.tenancy.re8ch.com",
                "tenantresourceclaims.tenancy.re8ch.com", "tenantserviceinstances.tenancy.re8ch.com",
                "tenants.finops.re8ch.com", "tenants.tenancy.re8ch.com",
            },
            "clusterResources": {
                "finops.re8ch.com": {
                    "consumables": {
                        "ai-model-gateway", "compute-workload", "database-postgresql-shared",
                        "network-among-clusters", "network-tenant", "observability-managed",
                        "registry-harbor", "sandbox-runtime", "storage-bucket", "storage-volume",
                    },
                    "tenants": {
                        "admin", "artchais", "personal-6a68c4a3-96e6-44ef-a0fd-c39fadd4ee38",
                        "personal-standard", "re8ch", "tenant-ci",
                    },
                },
                "tenancy.re8ch.com": {
                    "tenants": {
                        "admin", "artchais", "personal-6a68c4a3-96e6-44ef-a0fd-c39fadd4ee38",
                        "personal-standard", "re8ch", "tenant-ci",
                    },
                    "tenantidentitybindings": {
                        "admin-github-k8s-admins", "admin-github-k8s-admins-auth",
                        "admin-vmcp-service", "admin-vmcp-service-auth",
                        "artchais-dex-external-operator-v2", "artchais-github-admins",
                        "artchais-github-members", "artchais-github-operators",
                        "artchais-github-viewers", "re8ch-auth-admins", "re8ch-auth-billing",
                        "re8ch-auth-operators", "re8ch-auth-woomou-subject-operator",
                        "re8ch-dex-woomou-subject-operator",
                        "re8ch-github-k8s-admins-as-operators",
                        "re8ch-github-k8s-admins-as-operators-auth",
                        "re8ch-tenant-admins-as-operators",
                        "re8ch-tenant-admins-as-operators-auth", "tenant-ci-github-actions",
                    },
                    "tenantnamespaces": {
                        "artchais-5fc63c403b8e8eff",
                        "personal-6a68c4a3-96e6-44ef-a0fd-c39fadd4ee38-9d95c3998f7a3adb",
                        "personal-standard-8c4ce4f0e48a9db1", "re8ch-051dc66f5b3b5e90",
                        "re8ch-0ebcf50c181340ba", "re8ch-2417027baeb6d005",
                        "re8ch-2a0d2434fd138800", "re8ch-316ffc6fb7c4a822",
                        "re8ch-40b6628f0a5e08ef", "re8ch-66da3fb26238b2cb",
                        "re8ch-867c561a96f9de2f", "re8ch-a63905b113ca9169",
                        "re8ch-dbd837096de449de", "tenant-ci-4d8e6b2e153260b9",
                    },
                },
            },
        },
        "kube-state-metrics": {
            "clusterroles": {"re8ch-kube-state-metrics"},
            "clusterrolebindings": {"re8ch-kube-state-metrics"},
        },
        "opentelemetry": {
            "crds": {
                "instrumentations.opentelemetry.io", "opampbridges.opentelemetry.io",
                "opentelemetrycollectors.opentelemetry.io", "targetallocators.opentelemetry.io",
            },
            "clusterroles": {
                "otel-operator-opentelemetry-operator-manager",
                "otel-operator-opentelemetry-operator-metrics",
            },
            "clusterrolebindings": {"otel-operator-opentelemetry-operator-manager"},
        },
        "victoria-metrics": {
            "crds": {
                "vlagents.operator.victoriametrics.com", "vlclusters.operator.victoriametrics.com",
                "vldistributed.operator.victoriametrics.com", "vlogs.operator.victoriametrics.com",
                "vlsingles.operator.victoriametrics.com", "vmagents.operator.victoriametrics.com",
                "vmalertmanagerconfigs.operator.victoriametrics.com", "vmalertmanagers.operator.victoriametrics.com",
                "vmalerts.operator.victoriametrics.com", "vmanomalies.operator.victoriametrics.com",
                "vmanomalyconfigs.operator.victoriametrics.com", "vmauths.operator.victoriametrics.com",
                "vmclusters.operator.victoriametrics.com", "vmdistributed.operator.victoriametrics.com",
                "vmnodescrapes.operator.victoriametrics.com", "vmpodscrapes.operator.victoriametrics.com",
                "vmprobes.operator.victoriametrics.com", "vmrules.operator.victoriametrics.com",
                "vmscrapeconfigs.operator.victoriametrics.com", "vmservicescrapes.operator.victoriametrics.com",
                "vmsingles.operator.victoriametrics.com", "vmstaticscrapes.operator.victoriametrics.com",
                "vmusers.operator.victoriametrics.com", "vtclusters.operator.victoriametrics.com",
                "vtsingles.operator.victoriametrics.com",
            },
            "clusterroles": {
                "observability-system-victoria-metrics-operator",
                "observability-system-victoria-metrics-operator-victoriametrics-admin",
                "observability-system-victoria-metrics-operator-victoriametrics-view",
            },
            "clusterrolebindings": {"observability-system-victoria-metrics-operator"},
        },
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
            local_endpoints = R640_LOCAL_ENDPOINTS.get(domain)
            if local_endpoints and name == "source-controller":
                container["args"] = [
                    f"--storage-adv-addr=192.0.2.179:{local_endpoints['artifacts']}"
                    if arg.startswith("--storage-adv-addr=") else
                    f"--events-addr=http://192.0.2.179:{local_endpoints['events']}/"
                    if arg.startswith("--events-addr=") else arg
                    for arg in container["args"]
                ]
                next(port for port in container["ports"] if port["name"] == "http")["hostPort"] = local_endpoints["artifacts"]
            if local_endpoints and name == "notification-controller":
                next(port for port in container["ports"] if port["name"] == "http-webhook")["hostPort"] = local_endpoints["events"]
            container.setdefault("env", []).append({"name": "CONTROLLER_DOMAIN", "value": domain})
            if domain in {"agent-runtime", "observability-metering"}:
                container["env"].extend([
                    {
                        "name": "KUBERNETES_SERVICE_HOST",
                        "valueFrom": {"fieldRef": {"fieldPath": "status.hostIP"}},
                    },
                    {"name": "KUBERNETES_SERVICE_PORT", "value": "6443"},
                ])
            obj["spec"]["replicas"] = 1
            pod_spec = obj["spec"]["template"]["spec"]
            if domain in {"agent-runtime", "observability-metering"}:
                pod_spec.setdefault("nodeSelector", {})["kubernetes.io/hostname"] = "restricted-node-6dc3f140f9"
                if name == "source-controller":
                    pod_spec["dnsPolicy"] = "None"
                    pod_spec["dnsConfig"] = {
                        "nameservers": ["1.1.1.1", "8.8.8.8"],
                        "options": [{"name": "ndots", "value": "1"}],
                    }
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
    for group, resource_names in EXTERNAL_NAMESPACED_AUTHORITY.get(domain, {}).items():
        namespaced.setdefault(group, set()).update(resource_names)
    target_namespaces.update(EXTERNAL_TARGET_NAMESPACES.get(domain, set()))
    rules = lambda groups: [
        {"apiGroups": [group], "resources": sorted(values), "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]}
        for group, values in sorted(groups.items())
    ]
    namespaced_rules = rules(namespaced)
    for api_group_name, resources in EXTERNAL_NAMESPACED_NAMED_AUTHORITY.get(domain, {}).items():
        for resource, names in resources.items():
            namespaced_rules.append({
                "apiGroups": [api_group_name], "resources": [resource],
                "resourceNames": sorted(names),
                "verbs": ["get", "update", "patch"],
            })
    # Kubernetes rejects creation or mutation of an RBAC role when the caller
    # does not already hold every permission that role grants. Give the domain
    # reconciler escalation and binding rights only for the exact Role names in
    # its compiled bundle, rather than broad workload permissions or wildcard
    # RBAC authority.
    managed_roles = sorted({
        item["metadata"]["name"]
        for item in documents
        if item["apiVersion"] == "rbac.authorization.k8s.io/v1" and item["kind"] == "Role"
    } | EXTERNAL_NAMESPACED_ROLE_AUTHORITY.get(domain, set()))
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


def network_helm_product_rbac(namespace: str) -> list[dict]:
    """Render exact cluster authority for Network-owned Helm products."""
    result = []
    for product, authority in NETWORK_HELM_PRODUCT_AUTHORITY.items():
        role_name = f"flux-networking-helm-{product}"
        rules = [
            *([{
                "apiGroups": ["apiextensions.k8s.io"],
                "resources": ["customresourcedefinitions"],
                "resourceNames": sorted(authority["crds"]),
                "verbs": ["get", "update", "patch"],
            }] if authority.get("crds") else []),
            *([{
                "apiGroups": ["apiextensions.k8s.io"],
                "resources": ["customresourcedefinitions"],
                "resourceNames": sorted(authority["crdLookups"]),
                "verbs": ["get"],
            }] if authority.get("crdLookups") else []),
            *([{
                "apiGroups": [authority["apiGroup"]],
                "resources": sorted(authority["resources"]),
                "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"],
            }] if authority.get("resources") else []),
            *authority.get("extraRules", []),
            {
                "apiGroups": ["rbac.authorization.k8s.io"],
                "resources": ["clusterroles"],
                "resourceNames": sorted(authority["clusterroles"]),
                "verbs": ["get", "update", "patch", "escalate", "bind"],
            },
            {
                "apiGroups": ["rbac.authorization.k8s.io"],
                "resources": ["clusterrolebindings"],
                "resourceNames": sorted(authority["clusterrolebindings"]),
                "verbs": ["get", "update", "patch"],
            },
        ]
        result.extend([
            {
                "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
                "metadata": {"name": role_name, "labels": {"gitops.re8ch.com/domain": "networking"}},
                "rules": rules,
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
                "metadata": {"name": role_name, "labels": {"gitops.re8ch.com/domain": "networking"}},
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role_name},
                "subjects": [{"kind": "ServiceAccount", "name": "helm-controller", "namespace": namespace}],
            },
        ])
    return result


def domain_helm_product_rbac(domain: str, namespace: str) -> list[dict]:
    """Grant a domain Helm controller mutation rights only to named chart objects."""
    result = []
    for product, authority in DOMAIN_HELM_PRODUCT_AUTHORITY.get(domain, {}).items():
        role_name = f"flux-{domain}-helm-{product}"
        rules = []
        if authority.get("crds"):
            rules.append({
                "apiGroups": ["apiextensions.k8s.io"], "resources": ["customresourcedefinitions"],
                "resourceNames": sorted(authority["crds"]), "verbs": ["get", "update", "patch"],
            })
        for api_group_name, resources in authority.get("clusterResources", {}).items():
            for resource, names in resources.items():
                rules.append({
                    "apiGroups": [api_group_name], "resources": [resource],
                    "resourceNames": sorted(names), "verbs": ["get", "update", "patch"],
                })
        for resource, names in (("clusterroles", authority.get("clusterroles")),
                                ("clusterrolebindings", authority.get("clusterrolebindings"))):
            if names:
                rules.append({
                    "apiGroups": ["rbac.authorization.k8s.io"], "resources": [resource],
                    "resourceNames": sorted(names),
                    "verbs": ["get", "update", "patch", "escalate", "bind"] if resource == "clusterroles" else ["get", "update", "patch"],
                })
        result.extend([{
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
            "metadata": {"name": role_name, "labels": {"gitops.re8ch.com/domain": domain}}, "rules": rules,
        }, {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
            "metadata": {"name": role_name, "labels": {"gitops.re8ch.com/domain": domain}},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role_name},
            "subjects": [{"kind": "ServiceAccount", "name": "helm-controller", "namespace": namespace}],
        }])
    return result


def agent_runtime_scoped_rbac(namespace: str) -> list[dict]:
    """Grant Agent controllers only the namespaced authority their charts render."""
    labels = {"gitops.re8ch.com/domain": "agent-runtime"}
    return [
        {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
            "metadata": {
                "name": "flux-agent-runtime-helm-toolhive-leader-election",
                "namespace": "toolhive-system", "labels": labels,
            },
            "rules": [
                {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
                {"apiGroups": ["coordination.k8s.io"], "resources": ["leases"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
                {"apiGroups": ["events.k8s.io"], "resources": ["events"], "verbs": ["create", "patch"]},
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
            "metadata": {
                "name": "flux-agent-runtime-helm-toolhive-leader-election",
                "namespace": "toolhive-system", "labels": labels,
            },
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io", "kind": "Role",
                "name": "flux-agent-runtime-helm-toolhive-leader-election",
            },
            "subjects": [{"kind": "ServiceAccount", "name": "helm-controller", "namespace": namespace}],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
            "metadata": {
                "name": "flux-agent-runtime-kustomize-api-conformance",
                "namespace": "api-conformance-runners", "labels": labels,
            },
            "rules": [
                {"apiGroups": [""], "resources": ["configmaps", "secrets", "pods", "pods/log"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
                {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
                {"apiGroups": ["cilium.io"], "resources": ["ciliumnetworkpolicies"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
                {"apiGroups": ["rbac.authorization.k8s.io"], "resources": ["roles", "rolebindings"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete", "escalate", "bind"]},
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
            "metadata": {
                "name": "flux-agent-runtime-kustomize-api-conformance",
                "namespace": "api-conformance-runners", "labels": labels,
            },
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io", "kind": "Role",
                "name": "flux-agent-runtime-kustomize-api-conformance",
            },
            "subjects": [{"kind": "ServiceAccount", "name": "kustomize-controller", "namespace": namespace}],
        },
    ]


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
        controllers.extend(domain_helm_product_rbac(domain, namespace))
        if domain == "agent-runtime":
            controllers.extend(agent_runtime_scoped_rbac(namespace))
        if domain == "networking":
            controllers.extend(network_helm_product_rbac(namespace))
    dump_documents(args.root / "clusters/re8ch/flux-federation/controllers.yaml", controllers)
    guardrail_root = args.root / "clusters/re8ch/flux-federation/guardrails"
    for domain, documents in guardrails.items():
        dump_documents(guardrail_root / f"{domain}.yaml", documents)
    legacy = args.root / "clusters/re8ch/flux-federation/global-guardrails.yaml"
    legacy.unlink(missing_ok=True)
    print({"controllers": len(controllers), "guardrails": {domain: len(items) for domain, items in guardrails.items()}})


if __name__ == "__main__":
    main()
