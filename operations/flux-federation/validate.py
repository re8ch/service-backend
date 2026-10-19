#!/usr/bin/env python3
"""Static safety checks for the staged Flux federation bootstrap."""

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
FEDERATION = ROOT / "clusters/re8ch/flux-federation"
NAMESPACES = {
    "flux-agent-system",
    "flux-identity-system",
    "flux-observability-system",
    "flux-network-system",
}
DOMAIN_BY_NAMESPACE = {
    "flux-agent-system": "agent-runtime",
    "flux-identity-system": "identity-access",
    "flux-observability-system": "observability-metering",
    "flux-network-system": "networking",
}
CONTROLLERS = {
    "source-controller",
    "kustomize-controller",
    "helm-controller",
    "notification-controller",
}


def documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    controller_docs = documents(FEDERATION / "controllers.yaml")
    deployments = [doc for doc in controller_docs if doc["kind"] == "Deployment"]
    require(len(deployments) == 16, "expected four controller Deployments per domain")
    for item in deployments:
        metadata = item["metadata"]
        require(metadata["namespace"] in NAMESPACES, f"unexpected controller namespace: {metadata}")
        require(metadata["name"] in CONTROLLERS, f"unexpected controller: {metadata}")
        args = item["spec"]["template"]["spec"]["containers"][0].get("args", [])
        require("--watch-all-namespaces=false" in args, f"controller watches outside its namespace: {metadata}")
        require(all("watch-all-namespaces=true" not in arg for arg in args), f"all-namespace watch enabled: {metadata}")
        env = {
            entry["name"]: entry
            for entry in item["spec"]["template"]["spec"]["containers"][0].get("env", [])
        }
        if metadata["namespace"] in {"flux-agent-system", "flux-observability-system"}:
            require(
                item["spec"]["template"]["spec"].get("nodeSelector", {}).get("kubernetes.io/hostname") == "restricted-node-6dc3f140f9",
                f"controller is not pinned to the restricted-node-6dc3f140f9 service plane: {metadata}",
            )
            require(
                env.get("KUBERNETES_SERVICE_HOST", {}).get("valueFrom", {}).get("fieldRef", {}).get("fieldPath")
                == "status.hostIP",
                f"controller does not use its node-local API endpoint: {metadata}",
            )
            require(env.get("KUBERNETES_SERVICE_PORT", {}).get("value") == "6443",
                    f"controller local API port is not explicit: {metadata}")

    serialized = (FEDERATION / "controllers.yaml").read_text()
    require("cluster-admin" not in serialized, "federated controller references cluster-admin")

    for domain in DOMAIN_BY_NAMESPACE.values():
        namespaced_role = next(
            doc for doc in controller_docs
            if doc["kind"] == "ClusterRole" and doc["metadata"]["name"] == f"flux-{domain}-namespaced"
        )
        role_authority = next(
            rule for rule in namespaced_role["rules"]
            if rule.get("resources") == ["roles"] and set(rule.get("verbs", [])) == {"escalate", "bind"}
        )
        require(role_authority.get("resourceNames"), f"{domain} RBAC role authority must be name-scoped")
        require("*" not in role_authority["resourceNames"], f"{domain} RBAC role authority contains a wildcard")
    agent_role_authority = next(
        rule for rule in next(
            doc for doc in controller_docs
            if doc["kind"] == "ClusterRole" and doc["metadata"]["name"] == "flux-agent-runtime-namespaced"
        )["rules"]
        if rule.get("resources") == ["roles"] and set(rule.get("verbs", [])) == {"escalate", "bind"}
    )
    require("api-conformance-controller" in agent_role_authority["resourceNames"], "Agent RBAC omits api-conformance role authority")
    agent_namespaced_role = next(
        doc for doc in controller_docs
        if doc["kind"] == "ClusterRole" and doc["metadata"]["name"] == "flux-agent-runtime-namespaced"
    )
    agent_apps_rule = next(rule for rule in agent_namespaced_role["rules"] if rule.get("apiGroups") == ["apps"])
    require("replicasets" in agent_apps_rule["resources"],
            "Agent Helm RBAC omits ReplicaSet inspection authority")
    agent_runner_role = next(
        doc for doc in controller_docs
        if doc["kind"] == "Role"
        and doc["metadata"]["name"] == "flux-agent-runtime-kustomize-api-conformance"
    )
    require(agent_runner_role["metadata"]["namespace"] == "api-conformance-runners",
            "Agent runner reconciliation authority escaped its namespace")
    require(
        any(
            doc["kind"] == "RoleBinding"
            and doc["metadata"]["name"] == "flux-agent-runtime-kustomize-api-conformance"
            and doc["metadata"]["namespace"] == "api-conformance-runners"
            and doc["subjects"] == [{
                "kind": "ServiceAccount", "name": "kustomize-controller",
                "namespace": "flux-agent-system",
            }]
            for doc in controller_docs
        ),
        "Agent runner authority is not bound to the autonomous kustomize-controller",
    )

    network_namespaced_role = next(
        doc for doc in controller_docs
        if doc["kind"] == "ClusterRole" and doc["metadata"]["name"] == "flux-networking-namespaced"
    )
    network_role_authority = next(
        rule for rule in network_namespaced_role["rules"]
        if rule.get("resources") == ["roles"] and set(rule.get("verbs", [])) == {"escalate", "bind"}
    )
    require("advanced-fabric-controller-lease" in network_role_authority["resourceNames"],
            "Network Helm RBAC omits Advanced Fabric lease role authority")
    require("among-clusters-agent-identity" in network_role_authority["resourceNames"],
            "Network Helm RBAC omits Among Clusters agent identity role authority")
    require("among-clusters-credential-broker" in network_role_authority["resourceNames"],
            "Network Helm RBAC omits Among Clusters credential broker role authority")
    require(
        {
            "cilium-bgp-control-plane-secrets",
            "cilium-config-agent",
            "cilium-gateway-secrets",
            "cilium-operator-gateway-secrets",
            "cilium-operator-tlsinterception-secrets",
            "cilium-operator-ztunnel",
            "cilium-tlsinterception-secrets",
        }.issubset(set(network_role_authority["resourceNames"])),
        "Network Helm RBAC omits exact roles rendered by the adopted Cilium chart",
    )
    require("public-edge-manager-cloudflare" in network_role_authority["resourceNames"],
            "Network Helm RBAC omits Public Edge Cloudflare role authority")
    require("public-edge-manager-cli" in network_role_authority["resourceNames"],
            "Network Helm RBAC omits Public Edge CLI role authority")
    require(
        any(
            doc["kind"] == "RoleBinding"
            and doc["metadata"]["name"] == "flux-networking-namespaced"
            and doc["metadata"].get("namespace") == "cilium-secrets"
            and doc["roleRef"]["name"] == "flux-networking-namespaced"
            for doc in controller_docs
        ),
        "Network Helm namespaced authority is not bound in cilium-secrets",
    )
    network_apps_rule = next(
        rule for rule in network_namespaced_role["rules"]
        if rule.get("apiGroups") == ["apps"]
    )
    require("replicasets" in network_apps_rule["resources"],
            "Network Helm RBAC omits ReplicaSet inspection authority")
    network_gateway_rule = next(
        rule for rule in network_namespaced_role["rules"]
        if rule.get("apiGroups") == ["gateway.networking.k8s.io"]
    )
    require("referencegrants" in network_gateway_rule["resources"],
            "Network Helm RBAC omits cross-namespace ReferenceGrant authority")

    network_cluster_role = next(
        doc for doc in controller_docs
        if doc["kind"] == "ClusterRole" and doc["metadata"]["name"] == "flux-networking-cluster"
    )
    network_cluster_rules = {
        (tuple(rule.get("apiGroups", [])), tuple(rule.get("resources", [])), tuple(rule.get("verbs", [])))
        for rule in network_cluster_role["rules"]
    }
    require(
        (("cilium.io",), ("ciliumpodippools",), ("get", "list", "watch", "create", "update", "patch", "delete"))
        in network_cluster_rules,
        "Network RBAC omits CiliumPodIPPool authority",
    )
    require(
        any(
            doc["kind"] == "ClusterRoleBinding"
            and doc["metadata"]["name"] == "flux-networking-cluster"
            and doc["roleRef"]["name"] == "flux-networking-cluster"
            and {subject["namespace"] for subject in doc["subjects"]} == {"flux-network-system"}
            for doc in controller_docs
        ),
        "Network cluster authority is not bound to the autonomous controller namespace",
    )
    advanced_fabric_role = next(
        doc for doc in controller_docs
        if doc["kind"] == "ClusterRole" and doc["metadata"]["name"] == "flux-networking-helm-advanced-fabric"
    )
    crd_rule = next(
        rule for rule in advanced_fabric_role["rules"]
        if rule.get("apiGroups") == ["apiextensions.k8s.io"]
        and rule.get("resources") == ["customresourcedefinitions"]
    )
    require(
        set(crd_rule.get("resourceNames", [])) == {
            "advancedfabrics.networking.advfab.org",
            "networkinterventions.networking.advfab.org",
            "networkobservationepisodes.networking.advfab.org",
            "networkpathassessments.networking.advfab.org",
            "networktradeoffestimates.networking.advfab.org",
            "routerosnodes.networking.advfab.org",
            "tradeofftriangles.networking.advfab.org",
            "trafficpolicies.networking.advfab.org",
        },
        "Network Helm CRD authority must be limited to the Advanced Fabric API",
    )
    require("create" not in crd_rule["verbs"] and "delete" not in crd_rule["verbs"],
            "Network Helm must not create or delete cluster CRDs")

    require(
        any(
            doc["kind"] == "RoleBinding"
            and doc["metadata"]["name"] == "flux-observability-metering-namespaced"
            and doc["metadata"].get("namespace") == "observability-ai"
            for doc in controller_docs
        ),
        "Observability Helm release storage is not bound in observability-ai",
    )
    require(
        any(
            doc["kind"] == "RoleBinding"
            and doc["metadata"]["name"] == "flux-observability-metering-namespaced"
            and doc["metadata"].get("namespace") == "opentelemetry-operator"
            for doc in controller_docs
        ),
        "OpenTelemetry Helm target namespace is not bound",
    )
    victoria_role = next(
        doc for doc in controller_docs
        if doc["kind"] == "ClusterRole"
        and doc["metadata"]["name"] == "flux-observability-metering-helm-victoria-metrics"
    )
    victoria_rbac = {
        name
        for rule in victoria_role["rules"]
        if rule.get("apiGroups") == ["rbac.authorization.k8s.io"]
        for name in rule.get("resourceNames", [])
    }
    require(
        "victoria-metrics-operator" in victoria_rbac,
        "VictoriaMetrics Helm RBAC does not match the rendered release name",
    )
    require(
        any(
            doc["kind"] == "ClusterRoleBinding"
            and doc["metadata"]["name"] == "flux-networking-helm-advanced-fabric"
            and doc["roleRef"]["name"] == "flux-networking-helm-advanced-fabric"
            and doc["subjects"] == [{
                "kind": "ServiceAccount", "name": "helm-controller", "namespace": "flux-network-system"
            }]
            for doc in controller_docs
        ),
        "Advanced Fabric cluster authority must be bound only to the Network Helm controller",
    )

    product_helm_roles = {
        "flux-networking-helm-among-clusters": {
            "crds": {
                "authenticatedlinks.peering.re8ch.com",
                "clusteridentities.peering.re8ch.com",
                "importedservices.peering.re8ch.com",
                "managedaccessgrants.peering.re8ch.com",
                "peerpolicies.peering.re8ch.com",
                "peers.peering.re8ch.com",
                "serviceadvertisements.peering.re8ch.com",
            },
            "clusterroles": {
                "among-clusters-agent-observer",
                "among-clusters-credential-broker-auth-review",
                "among-clusters-hub",
                "among-clusters-view",
            },
            "clusterrolebindings": {
                "among-clusters-agent-observer",
                "among-clusters-credential-broker-auth-review",
                "among-clusters-hub",
            },
            "crdLookups": {
                "collaborationclusters.collaboration.re8ch.com",
                "collaborationevents.collaboration.re8ch.com",
                "collaborationrelationships.collaboration.re8ch.com",
                "publishedservices.collaboration.re8ch.com",
            },
        },
        "flux-networking-helm-public-edge": {
            "crds": {"publicedges.networking.re8ch.com"},
            "clusterroles": {"public-edge-manager", "public-edge-manager-cli-read"},
            "clusterrolebindings": {"public-edge-manager", "public-edge-manager-cli-read"},
        },
    }
    for role_name, expected in product_helm_roles.items():
        product_role = next(
            doc for doc in controller_docs
            if doc["kind"] == "ClusterRole" and doc["metadata"]["name"] == role_name
        )
        product_crd_rule = next(
            rule for rule in product_role["rules"]
            if rule.get("apiGroups") == ["apiextensions.k8s.io"]
            and rule.get("resources") == ["customresourcedefinitions"]
        )
        require(set(product_crd_rule.get("resourceNames", [])) == expected["crds"],
                f"{role_name} CRD authority is not exactly product-scoped")
        require("create" not in product_crd_rule["verbs"] and "delete" not in product_crd_rule["verbs"],
                f"{role_name} must not create or delete cluster CRDs")
        if expected.get("crdLookups"):
            lookup_rule = next(
                rule for rule in product_role["rules"]
                if rule.get("apiGroups") == ["apiextensions.k8s.io"]
                and rule.get("resources") == ["customresourcedefinitions"]
                and rule.get("verbs") == ["get"]
            )
            require(set(lookup_rule.get("resourceNames", [])) == expected["crdLookups"],
                    f"{role_name} legacy CRD lookup authority is not exact or read-only")
        for resource_kind, expected_names in (
            ("clusterroles", expected["clusterroles"]),
            ("clusterrolebindings", expected["clusterrolebindings"]),
        ):
            product_rbac_rule = next(
                rule for rule in product_role["rules"]
                if rule.get("apiGroups") == ["rbac.authorization.k8s.io"]
                and rule.get("resources") == [resource_kind]
            )
            require(set(product_rbac_rule.get("resourceNames", [])) == expected_names,
                    f"{role_name} {resource_kind} authority is not exactly product-scoped")
        require(
            any(
                doc["kind"] == "ClusterRoleBinding"
                and doc["metadata"]["name"] == role_name
                and doc["roleRef"]["name"] == role_name
                and doc["subjects"] == [{
                    "kind": "ServiceAccount", "name": "helm-controller", "namespace": "flux-network-system"
                }]
                for doc in controller_docs
            ),
            f"{role_name} authority must be bound only to the Network Helm controller",
        )

    cilium_role = next(
        doc for doc in controller_docs
        if doc["kind"] == "ClusterRole" and doc["metadata"]["name"] == "flux-networking-helm-cilium"
    )
    cilium_rules = {
        (tuple(rule.get("apiGroups", [])), tuple(rule.get("resources", []))): rule
        for rule in cilium_role["rules"]
    }
    expected_cilium_names = {
        (("admissionregistration.k8s.io",), ("validatingadmissionpolicies",)): {"gateway.cilium.io"},
        (("admissionregistration.k8s.io",), ("validatingadmissionpolicybindings",)): {"gateway.cilium.io"},
        (("gateway.networking.k8s.io",), ("gatewayclasses",)): {"cilium"},
        (("",), ("namespaces",)): {"cilium-secrets"},
        (("rbac.authorization.k8s.io",), ("clusterroles",)): {"cilium", "cilium-operator", "hubble-ui"},
        (("rbac.authorization.k8s.io",), ("clusterrolebindings",)): {"cilium", "cilium-operator", "hubble-ui"},
    }
    for key, names in expected_cilium_names.items():
        rule = cilium_rules[key]
        require(set(rule.get("resourceNames", [])) == names,
                f"Network Cilium Helm authority is not exact for {key}")
        require("create" not in rule["verbs"] and "delete" not in rule["verbs"],
                f"Network Cilium Helm authority may not create or delete {key}")
    require(
        any(
            doc["kind"] == "ClusterRoleBinding"
            and doc["metadata"]["name"] == "flux-networking-helm-cilium"
            and doc["roleRef"]["name"] == "flux-networking-helm-cilium"
            and doc["subjects"] == [{
                "kind": "ServiceAccount", "name": "helm-controller", "namespace": "flux-network-system"
            }]
            for doc in controller_docs
        ),
        "Cilium cluster authority must be bound only to the Network Helm controller",
    )

    registrations = documents(FEDERATION / "registrations.yaml")
    sources = [doc for doc in registrations if doc["kind"] == "GitRepository"]
    reconciliations = [doc for doc in registrations if doc["kind"] == "Kustomization"]
    require(len(sources) == len(reconciliations) == 4, "expected four sources and four Kustomizations")
    authority = yaml.safe_load((ROOT / "operations/flux-federation/authority.yaml").read_text())
    domains = authority["spec"]["domains"]
    for item in sources + reconciliations:
        require(item["metadata"]["namespace"] in NAMESPACES, "registration outside autonomous namespace")
    for item in sources:
        require(item["spec"].get("suspend") is False, "bootstrap source must be active for read-only verification")
    for item in reconciliations:
        domain = DOMAIN_BY_NAMESPACE[item["metadata"]["namespace"]]
        state = domains[domain]["state"]
        require(state in {"staged", "cutover-verification", "autonomous"}, f"invalid authority state: {state}")
        require(item["spec"].get("suspend") is (state == "staged"), f"Kustomization suspension disagrees with {domain} state")
        require(item["spec"].get("prune") is (state == "autonomous"), f"Kustomization pruning disagrees with {domain} state")

    kustomization = yaml.safe_load((FEDERATION / "kustomization.yaml").read_text())
    resources = set(kustomization["resources"])
    active_guardrails = {f"guardrails/{domain}.yaml" for domain, entry in domains.items() if entry["state"] != "staged"}
    require({path for path in resources if path.startswith("guardrails/")} == active_guardrails, "guardrail resources disagree with authority states")
    require(len(list((FEDERATION / "guardrails").glob("*.yaml"))) == 4, "expected one staged guardrail file per domain")

    for path in (FEDERATION / "secrets").glob("*.sops.yaml"):
        payload = path.read_text()
        require("sops:" in payload and "ENC[AES256_GCM" in payload, f"unencrypted deploy key: {path}")
        require("PRIVATE KEY-----" not in payload, f"plaintext private key marker: {path}")

    require(set(authority["spec"]["domains"]) == {"agent-runtime", "identity-access", "observability-metering", "networking"}, "authority domain mismatch")
    print("Flux federation bootstrap validation passed")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
