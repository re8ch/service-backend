#!/usr/bin/env python3
"""Render allow-listed Kubernetes resources from a normalized tenant claim."""

from __future__ import annotations

import hashlib
from typing import Any

PRIVATE_RANGES = [
    "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4",
]


def resource_name(claim_name: str) -> str:
    base = f"trc-{claim_name}"
    if len(base) <= 63:
        return base
    return f"{base[:54].rstrip('-')}-{hashlib.sha256(base.encode()).hexdigest()[:8]}"


def public_hostname(tenant: str, claim_name: str) -> str:
    label = f"{tenant}-{claim_name}"
    if len(label) > 63:
        label = f"{label[:54].rstrip('-')}-{hashlib.sha256(label.encode()).hexdigest()[:8]}"
    return f"{label}.apps.re8ch.com"


def _labels(claim: dict[str, Any]) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "tenant-control-controller",
        "tenancy.re8ch.com/tenant": claim["spec"]["tenantRef"],
        "tenancy.re8ch.com/claim": claim["metadata"]["name"],
        "tenancy.re8ch.com/claim-uid": claim["metadata"].get("uid", "pending"),
        "birfucate.re8ch.com/metered": "true",
        "tenant": claim["spec"]["tenantRef"],
        "resource_ref": claim["metadata"].get("uid", "pending"),
    }


def _owner(claim: dict[str, Any]) -> list[dict[str, Any]]:
    uid = claim["metadata"].get("uid")
    if not uid:
        return []
    return [{"apiVersion": "tenancy.re8ch.com/v1alpha1", "kind": "TenantResourceClaim", "name": claim["metadata"]["name"], "uid": uid, "controller": True, "blockOwnerDeletion": True}]


def _pod_template(claim: dict[str, Any], name: str, image_pull_secret: str) -> dict[str, Any]:
    params = claim["spec"]["parameters"]
    labels = {**_labels(claim), "app.kubernetes.io/name": name, "networking.re8ch.com/egress-class": "metered-public"}
    mounts = [{"name": f"volume-{index}", "mountPath": item["mountPath"], "readOnly": item.get("readOnly", False)} for index, item in enumerate(params.get("volumeMounts", []))]
    volumes = [{"name": f"volume-{index}", "persistentVolumeClaim": {"claimName": resource_name(item["claimName"]), "readOnly": item.get("readOnly", False)}} for index, item in enumerate(params.get("volumeMounts", []))]
    container: dict[str, Any] = {
        "name": "workload", "image": params["imageDigest"], "imagePullPolicy": "IfNotPresent",
        "env": [{"name": key, "value": value} for key, value in sorted(params.get("env", {}).items())],
        "resources": {"requests": {"cpu": params["resources"]["cpu"], "memory": params["resources"]["memory"], "ephemeral-storage": params["resources"]["ephemeralStorage"]}, "limits": {"cpu": params["resources"]["cpu"], "memory": params["resources"]["memory"], "ephemeral-storage": params["resources"]["ephemeralStorage"]}},
        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "runAsNonRoot": True, "capabilities": {"drop": ["ALL"]}},
        "volumeMounts": mounts,
    }
    if params.get("command"):
        container["command"] = params["command"]
    if params.get("args"):
        container["args"] = params["args"]
    if params.get("exposure", {}).get("enabled"):
        container["ports"] = [{"name": "http", "containerPort": params["exposure"]["port"], "protocol": "TCP"}]
        container["readinessProbe"] = {"httpGet": {"path": params["exposure"]["healthPath"], "port": "http"}, "periodSeconds": 10}
    return {"metadata": {"labels": labels}, "spec": {"serviceAccountName": "tenant-workload", "automountServiceAccountToken": False, "enableServiceLinks": False, "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}, "runAsNonRoot": True}, "containers": [container], "volumes": volumes, "imagePullSecrets": [{"name": image_pull_secret}], "terminationGracePeriodSeconds": 30}}


def _network_policy(namespace: str, claim: dict[str, Any], name: str) -> dict[str, Any]:
    labels = _labels(claim)
    exposure = claim["spec"]["parameters"].get("exposure", {})
    ingress = []
    if exposure.get("enabled"):
        ingress = [{"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}], "ports": [{"protocol": "TCP", "port": exposure["port"]}]}]
    return {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": name, "namespace": namespace, "labels": labels, "ownerReferences": _owner(claim)},
        "spec": {
            "podSelector": {"matchLabels": {"tenancy.re8ch.com/claim-uid": labels["tenancy.re8ch.com/claim-uid"]}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": ingress,
            "egress": [
                {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}], "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]},
                {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": PRIVATE_RANGES}}]},
            ],
        },
    }


def render_resources(claim: dict[str, Any], namespace: str, image_pull_secret: str = "tenant-harbor-pull") -> list[dict[str, Any]]:
    """Return the complete desired children for one accepted normalized claim."""
    params = claim["spec"]["parameters"]
    name = resource_name(claim["metadata"]["name"])
    labels = _labels(claim)
    if claim["spec"]["capability"] == "storage.volume.v1":
        # No ownerReference: volume lifetime is independent from compute and the
        # claim object. Explicit release can archive/delete it under tenant policy.
        return [{
            "apiVersion": "v1", "kind": "PersistentVolumeClaim",
            "metadata": {"name": name, "namespace": namespace, "labels": {**labels, "storage.re8ch.com/replicated-approved": "true"}, "annotations": {"tenancy.re8ch.com/retention-policy": "Retain"}},
            "spec": {"accessModes": [params["accessMode"]], "storageClassName": params["storageClass"], "resources": {"requests": {"storage": f'{params["sizeGiB"]}Gi'}}},
        }]
    template = _pod_template(claim, name, image_pull_secret)
    metadata = {"name": name, "namespace": namespace, "labels": labels, "ownerReferences": _owner(claim)}
    if params["kind"] == "Deployment":
        workload = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata, "spec": {"replicas": params["replicas"], "revisionHistoryLimit": 2, "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}}, "selector": {"matchLabels": {"tenancy.re8ch.com/claim-uid": labels["tenancy.re8ch.com/claim-uid"]}}, "template": template}}
    else:
        workload = {"apiVersion": "batch/v1", "kind": "Job", "metadata": metadata, "spec": {"parallelism": params["parallelism"], "completions": params["completions"], "backoffLimit": 1, "template": template}}
    resources = [workload, _network_policy(namespace, claim, name)]
    if params.get("exposure", {}).get("enabled"):
        hostname = public_hostname(claim["spec"]["tenantRef"], claim["metadata"]["name"])
        service = {"apiVersion": "v1", "kind": "Service", "metadata": metadata, "spec": {"selector": {"tenancy.re8ch.com/claim-uid": labels["tenancy.re8ch.com/claim-uid"]}, "ports": [{"name": "http", "port": params["exposure"]["port"], "targetPort": "http"}]}}
        route = {"apiVersion": "gateway.networking.k8s.io/v1", "kind": "HTTPRoute", "metadata": metadata, "spec": {"parentRefs": [{"name": "re8ch-gateway-canary", "namespace": "kube-system", "sectionName": "apps-wildcard-https"}], "hostnames": [hostname], "rules": [{"backendRefs": [{"name": name, "port": params["exposure"]["port"]}]}]}}
        resources.extend([service, route])
    return resources


def binding_status(claim: dict[str, Any], namespace: str) -> dict[str, Any]:
    name = resource_name(claim["metadata"]["name"])
    if claim["spec"]["capability"] == "storage.volume.v1":
        return {"volumeRef": {"name": name, "namespace": namespace}}
    params = claim["spec"]["parameters"]
    result: dict[str, Any] = {"workloadRef": {"apiVersion": "apps/v1" if params["kind"] == "Deployment" else "batch/v1", "kind": params["kind"], "name": name, "namespace": namespace}}
    if params.get("exposure", {}).get("enabled"):
        result["endpoint"] = {"url": "https://" + public_hostname(claim["spec"]["tenantRef"], claim["metadata"]["name"]), "anonymous": True}
    return result
