#!/usr/bin/env python3
"""Tenant identity resolver and CRD status controller.

Kubernetes CRDs are authoritative. This service never accepts a client supplied
tenant and never returns the OIDC subject. Runtime callers send its SHA-256 hash
over a cluster-internal authenticated channel.
"""

import calendar
import hashlib
import hmac
import importlib.util
import json
import os
import ssl
import socket
import sys
import threading
import time
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from tenant_claim_contracts import ContractError, normalize_claim, plan, validate_update
    from tenant_claims import binding_status, render_resources, resource_name
except ModuleNotFoundError:  # repository-local unit tests; the image uses /app modules
    component_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(component_dir))
    from tenant_claims import binding_status, render_resources, resource_name
    contract_path = Path(__file__).resolve().parents[4] / "operations/tenant-broker/contracts.py"
    contract_spec = importlib.util.spec_from_file_location("tenant_claim_contracts", contract_path)
    contract_module = importlib.util.module_from_spec(contract_spec)
    contract_spec.loader.exec_module(contract_module)
    ContractError, normalize_claim, plan, validate_update = contract_module.ContractError, contract_module.normalize_claim, contract_module.plan, contract_module.validate_update

GROUP = "tenancy.re8ch.com"
VERSION = "v1alpha1"
PORT = int(os.getenv("LISTEN_PORT", "19447"))
NAMESPACE = os.getenv("POD_NAMESPACE", "tenant-infrastructure")
IDENTITY = os.getenv("POD_NAME", socket.gethostname())
LEASE_NAME = "tenant-control-controller"
IMAGE_POLICY_URL = os.getenv("IMAGE_POLICY_URL", "").rstrip("/")
IMAGE_POLICY_TOKEN = os.getenv("IMAGE_POLICY_TOKEN", os.getenv("HARBOR_SUPPLY_CHAIN_TOKEN", ""))
CLAIM_SERVICE_TOKEN = os.getenv("TENANT_CLAIM_SERVICE_TOKEN", "")
ALLOWED_CALLERS = {"admin", "tenant"}
CLAIM_MUTATION_LOCK = threading.Lock()
LOG = logging.getLogger("tenant-control-controller")
RESERVED_NAMESPACES = {"default", "kube-system", "kube-public", "kube-node-lease", "flux-system", "tenant-infrastructure", "toolhive-system"}


class Kubernetes:
    def __init__(self):
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.base = f"https://{host}:{port}"
        self.token = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read().strip()
        self.ssl = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")

    def request(self, method, path, body=None, content_type="application/json"):
        payload = json.dumps(body).encode() if body is not None else None
        last_error = None
        for attempt in range(3):
            req = urllib.request.Request(
                self.base + path, data=payload, method=method,
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json", "Content-Type": content_type},
            )
            try:
                with urllib.request.urlopen(req, context=self.ssl, timeout=10) as response:
                    return json.load(response)
            except urllib.error.HTTPError:
                raise
            except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.25 * (2 ** attempt))
        raise RuntimeError("Kubernetes API remained unavailable after retries") from last_error

    def list(self, plural, namespace=None, api=f"apis/{GROUP}/{VERSION}"):
        prefix = f"/{api}"
        path = f"{prefix}/namespaces/{namespace}/{plural}" if namespace else f"{prefix}/{plural}"
        return self.request("GET", path).get("items", [])

    def get(self, plural, name, namespace=None, api=f"apis/{GROUP}/{VERSION}"):
        prefix = f"/{api}"
        path = f"{prefix}/namespaces/{namespace}/{plural}/{name}" if namespace else f"{prefix}/{plural}/{name}"
        return self.request("GET", path)

    def create(self, plural, body, namespace=None, api=f"apis/{GROUP}/{VERSION}"):
        prefix = f"/{api}"
        path = f"{prefix}/namespaces/{namespace}/{plural}" if namespace else f"{prefix}/{plural}"
        return self.request("POST", path, body)

    def patch(self, plural, name, body, namespace=None, api=f"apis/{GROUP}/{VERSION}"):
        prefix = f"/{api}"
        path = f"{prefix}/namespaces/{namespace}/{plural}/{name}" if namespace else f"{prefix}/{plural}/{name}"
        return self.request("PATCH", path, body, "application/merge-patch+json")

    def delete(self, plural, name, namespace=None, api=f"apis/{GROUP}/{VERSION}"):
        prefix = f"/{api}"
        path = f"{prefix}/namespaces/{namespace}/{plural}/{name}" if namespace else f"{prefix}/{plural}/{name}"
        try:
            return self.request("DELETE", path, {"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Background"})
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            return {}

    def apply(self, value):
        mapping = {
            ("v1", "Namespace"): ("api/v1", "namespaces"),
            ("v1", "ConfigMap"): ("api/v1", "configmaps"),
            ("v1", "Secret"): ("api/v1", "secrets"),
            ("v1", "ServiceAccount"): ("api/v1", "serviceaccounts"),
            ("v1", "ResourceQuota"): ("api/v1", "resourcequotas"),
            ("v1", "LimitRange"): ("api/v1", "limitranges"),
            ("v1", "PersistentVolumeClaim"): ("api/v1", "persistentvolumeclaims"),
            ("v1", "Service"): ("api/v1", "services"),
            ("rbac.authorization.k8s.io/v1", "Role"): ("apis/rbac.authorization.k8s.io/v1", "roles"),
            ("rbac.authorization.k8s.io/v1", "RoleBinding"): ("apis/rbac.authorization.k8s.io/v1", "rolebindings"),
            ("apps/v1", "Deployment"): ("apis/apps/v1", "deployments"),
            ("batch/v1", "Job"): ("apis/batch/v1", "jobs"),
            ("networking.k8s.io/v1", "NetworkPolicy"): ("apis/networking.k8s.io/v1", "networkpolicies"),
            ("gateway.networking.k8s.io/v1", "HTTPRoute"): ("apis/gateway.networking.k8s.io/v1", "httproutes"),
        }
        api, plural = mapping[(value["apiVersion"], value["kind"])]
        meta = value["metadata"]
        scope = f"/namespaces/{meta['namespace']}" if meta.get("namespace") else ""
        path = f"/{api}{scope}/{plural}/{meta['name']}?fieldManager=tenant-control-controller&force=true"
        return self.request("PATCH", path, value, "application/apply-patch+yaml")

    def patch_status(self, plural, name, status, namespace=None):
        base = f"/apis/{GROUP}/{VERSION}"
        path = f"{base}/namespaces/{namespace}/{plural}/{name}/status" if namespace else f"{base}/{plural}/{name}/status"
        return self.request("PATCH", path, {"status": status}, "application/merge-patch+json")

    def claim_lease(self):
        path = f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases/{LEASE_NAME}"
        # LeaseSpec uses Kubernetes MicroTime, which requires fractional seconds.
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            lease = self.request("GET", path)
            holder = lease.get("spec", {}).get("holderIdentity")
            renew = lease.get("spec", {}).get("renewTime", "1970-01-01T00:00:00Z")
            renewed = calendar.timegm(time.strptime(renew[:19] + "Z", "%Y-%m-%dT%H:%M:%SZ"))
            if holder not in (None, IDENTITY) and time.time() - renewed < 20:
                return False
            body = {
                "apiVersion": "coordination.k8s.io/v1",
                "kind": "Lease",
                "metadata": {
                    "name": LEASE_NAME,
                    "namespace": NAMESPACE,
                    "resourceVersion": lease["metadata"]["resourceVersion"],
                },
                "spec": {
                    "holderIdentity": IDENTITY,
                    "leaseDurationSeconds": 20,
                    "acquireTime": lease.get("spec", {}).get("acquireTime", now),
                    "renewTime": now,
                },
            }
            self.request("PUT", path, body)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                LOG.warning("leader lease request failed with HTTP %s", exc.code)
                return False
            self.request("POST", f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases", {"apiVersion": "coordination.k8s.io/v1", "kind": "Lease", "metadata": {"name": LEASE_NAME, "namespace": NAMESPACE}, "spec": {"holderIdentity": IDENTITY, "leaseDurationSeconds": 20, "acquireTime": now, "renewTime": now}})
        return True


KUBE = None


def kube():
    if KUBE is None:
        raise RuntimeError("Kubernetes client is not initialized")
    return KUBE


def condition(kind, ok, reason, message=""):
    return {"type": kind, "status": "True" if ok else "False", "reason": reason,
            "message": message[:1024], "lastTransitionTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


ROLE_ORDER = {"viewer": 1, "operator": 2, "admin": 3}


def resolve(issuer, subject_hash, groups=None, caller="tenant"):
    subject_matches = []
    group_matches = []
    groups = {str(group) for group in (groups or []) if isinstance(group, str)}
    for binding in kube().list("tenantidentitybindings"):
        spec = binding.get("spec", {})
        if spec.get("revoked") or spec.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
            continue
        subject_match = bool(subject_hash and spec.get("subjectHash") and hmac.compare_digest(spec["subjectHash"], subject_hash))
        group_match = bool(spec.get("group") and spec["group"] in groups)
        tenant_ref = spec.get("tenantRef")
        # Admin and ordinary tenant publications are separate trust domains.
        # A subject may belong to both, but each publication resolves only the
        # bindings appropriate for its own audience.
        if caller == "admin" and tenant_ref != "admin":
            continue
        if caller == "tenant" and tenant_ref == "admin":
            continue
        match = (spec.get("tenantRef"), spec.get("role", "viewer"))
        if subject_match:
            subject_matches.append(match)
        elif group_match:
            group_matches.append(match)
    # An explicit, issuer-scoped subject binding is more specific than broad
    # organization/team membership. This lets a multi-organization identity
    # have one deterministic tenant without accepting a client-selected tenant.
    # Multiple explicit subject bindings remain ambiguous and are denied below.
    matches = subject_matches or group_matches
    tenants_matched = sorted(set(tenant for tenant, _ in matches if tenant))
    if len(tenants_matched) != 1:
        raise PermissionError("identity must resolve to exactly one active tenant")
    tenants = {item["metadata"]["name"]: item for item in kube().list("tenants")}
    tenant = tenants.get(tenants_matched[0])
    if not tenant or tenant.get("spec", {}).get("lifecycle") != "Active":
        raise PermissionError("tenant is not active")
    role = max((role for tenant_ref, role in matches if tenant_ref == tenants_matched[0]), key=lambda value: ROLE_ORDER.get(value, 0))
    if role not in ROLE_ORDER:
        raise PermissionError("identity binding has an invalid role")
    policy = tenant.get("spec", {}).get("namespacePolicy", {})
    default_namespace = policy.get("defaultNamespace") or tenant.get("spec", {}).get("runtimeNamespaceRef") or tenants_matched[0]
    return {
        "tenantRef": tenants_matched[0],
        "role": role,
        "defaultNamespace": default_namespace,
        "runtimeNamespaceRef": default_namespace,
    }


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tenant_definition(tenant_ref):
    for tenant in kube().list("tenants"):
        if tenant.get("metadata", {}).get("name") == tenant_ref and tenant.get("spec", {}).get("lifecycle") == "Active":
            return tenant
    raise PermissionError("active tenant definition is unavailable")


def runtime_namespace(tenant_ref):
    spec = tenant_definition(tenant_ref).get("spec", {})
    value = spec.get("namespacePolicy", {}).get("defaultNamespace") or spec.get("runtimeNamespaceRef")
    if value:
        return value
    raise PermissionError("active tenant runtime namespace is unavailable")


def namespace_policy(tenant_ref):
    spec = tenant_definition(tenant_ref).get("spec", {})
    policy = spec.get("namespacePolicy") or {}
    return {
        "defaultNamespace": policy.get("defaultNamespace") or spec.get("runtimeNamespaceRef"),
        "allowedPrefixes": policy.get("allowedPrefixes") or [tenant_ref + "-"],
        "maxNamespaces": int(policy.get("maxNamespaces", 20)),
        "allowCreate": bool(policy.get("allowCreate", False)),
        "allowDelete": bool(policy.get("allowDelete", False)),
    }


def validate_tenant_namespace(tenant_ref, namespace, operation):
    policy = namespace_policy(tenant_ref)
    if namespace in RESERVED_NAMESPACES or not any(namespace.startswith(prefix) for prefix in policy["allowedPrefixes"]):
        raise PermissionError("namespace is outside the authenticated tenant prefix")
    if operation == "EnsurePresent" and not policy["allowCreate"]:
        raise PermissionError("tenant namespace creation is disabled")
    if operation == "EnsureAbsent" and not policy["allowDelete"]:
        raise PermissionError("tenant namespace deletion is disabled")
    return policy


def tenant_namespace_name(tenant_ref, namespace):
    return f"{tenant_ref}-{hashlib.sha256(namespace.encode()).hexdigest()[:16]}"


def tenant_groups(tenant_ref):
    groups = []
    for binding in kube().list("tenantidentitybindings"):
        spec = binding.get("spec", {})
        if spec.get("tenantRef") == tenant_ref and not spec.get("revoked") and spec.get("group") and spec.get("role") in {"operator", "admin"}:
            groups.append(spec["group"])
    return sorted(set(groups))


def tenant_namespace_resources(tenant, namespace):
    tenant_ref = tenant["metadata"]["name"]
    tenant_uid = tenant["metadata"]["uid"]
    labels = {
        "tenancy.re8ch.com/tenant": tenant_ref,
        "tenancy.re8ch.com/managed": "true",
        "tenancy.re8ch.com/managed-by": "tenant-control-controller",
    }
    annotation = {"tenancy.re8ch.com/tenant-uid": tenant_uid}
    owner_rules = [
        {"apiGroups": [""], "resources": ["pods", "pods/log", "pods/exec", "services", "endpoints", "persistentvolumeclaims", "configmaps", "secrets", "serviceaccounts", "events"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
        {"apiGroups": ["apps"], "resources": ["deployments", "deployments/scale", "statefulsets", "statefulsets/scale", "daemonsets", "replicasets"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
        {"apiGroups": ["batch"], "resources": ["jobs", "cronjobs"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
        {"apiGroups": ["autoscaling"], "resources": ["horizontalpodautoscalers"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
        {"apiGroups": ["policy"], "resources": ["poddisruptionbudgets"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
        {"apiGroups": ["rbac.authorization.k8s.io"], "resources": ["roles", "rolebindings"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]},
    ]
    subjects = [{"kind": "ServiceAccount", "name": "tenant-api", "namespace": "tenant-infrastructure"}]
    subjects += [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": group} for group in tenant_groups(tenant_ref)]
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace, "labels": labels, "annotations": annotation}},
        {"apiVersion": "v1", "kind": "ResourceQuota", "metadata": {"name": "tenant-boundary", "namespace": namespace, "labels": labels}, "spec": {"hard": {"pods": "100", "requests.cpu": "20", "requests.memory": "64Gi", "requests.storage": "1Ti"}}},
        {"apiVersion": "v1", "kind": "LimitRange", "metadata": {"name": "tenant-defaults", "namespace": namespace, "labels": labels}, "spec": {"limits": [{"type": "Container", "defaultRequest": {"cpu": "25m", "memory": "64Mi"}, "default": {"cpu": "2", "memory": "4Gi"}}]}},
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "tenant-native-operator", "namespace": namespace, "labels": labels}, "automountServiceAccountToken": False},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "tenant-native-owner", "namespace": namespace, "labels": labels}, "rules": owner_rules},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "tenant-native-owner", "namespace": namespace, "labels": labels}, "subjects": subjects, "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "tenant-native-owner"}},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "tenant-resource-provisioner", "namespace": namespace, "labels": labels}, "subjects": [{"kind": "ServiceAccount", "name": "tenant-control-controller", "namespace": "tenant-infrastructure"}], "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "tenant-resource-provisioner"}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "tenant-default-deny-cross-tenant", "namespace": namespace, "labels": labels}, "spec": {"podSelector": {}, "policyTypes": ["Ingress"], "ingress": [{"from": [{"namespaceSelector": {"matchLabels": {"tenancy.re8ch.com/tenant": tenant_ref}}}]}]}},
    ]


def reconcile_tenant_namespace(resource, tenants):
    meta, spec = resource["metadata"], resource["spec"]
    tenant_ref, namespace = spec["tenantRef"], spec["namespaceName"]
    tenant = tenants.get(tenant_ref)
    if not tenant or tenant.get("spec", {}).get("lifecycle") != "Active":
        raise PermissionError("tenant is not active")
    validate_tenant_namespace(tenant_ref, namespace, spec["operation"])
    operation_id = "op_" + hashlib.sha256(meta["uid"].encode()).hexdigest()[:24]
    if spec["operation"] == "EnsureAbsent":
        kube().delete("namespaces", namespace, api="api/v1")
        try:
            live = kube().get("namespaces", namespace, api="api/v1")
            phase, uid = "Terminating", live.get("metadata", {}).get("uid")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            phase, uid = "Ready", None
        status = {"observedGeneration": meta.get("generation", 1), "operationId": operation_id, "phase": phase, "namespaceUID": uid, "reason": "NamespaceAbsent" if not uid else "NamespaceTerminating", "conditions": [condition("Ready", not uid, "NamespaceAbsent" if not uid else "NamespaceTerminating")]}
    else:
        current = None
        try:
            current = kube().get("namespaces", namespace, api="api/v1")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        if current:
            annotations = current.get("metadata", {}).get("annotations", {})
            labels = current.get("metadata", {}).get("labels", {})
            if annotations.get("tenancy.re8ch.com/tenant-uid") not in {None, tenant["metadata"]["uid"]} or labels.get("tenancy.re8ch.com/tenant") not in {None, tenant_ref}:
                raise PermissionError("existing namespace is owned by another tenant")
        for value in tenant_namespace_resources(tenant, namespace):
            kube().apply(value)
        live = kube().get("namespaces", namespace, api="api/v1")
        status = {"observedGeneration": meta.get("generation", 1), "operationId": operation_id, "phase": "Ready", "namespaceUID": live["metadata"]["uid"], "reason": "NamespaceReady", "conditions": [condition("Ready", True, "NamespaceReady")]}
    kube().patch_status("tenantnamespaces", meta["name"], status)


def image_pull_secret(tenant_ref):
    value = tenant_definition(tenant_ref).get("spec", {}).get("imagePullSecretRef")
    if not value:
        raise PermissionError("tenant Harbor pull reference is unavailable")
    return value


def harbor_project(tenant_ref):
    return tenant_definition(tenant_ref).get("spec", {}).get("harborProjectRef") or tenant_ref


def active_grant(tenant_ref, capability):
    now = utcnow()
    matches = []
    for grant in kube().list("tenantgrants"):
        spec = grant.get("spec", {})
        if spec.get("tenantRef") != tenant_ref or spec.get("lifecycle") != "Active":
            continue
        if spec.get("consumableRef") not in {"tenant", capability}:
            continue
        if spec.get("notBefore") and datetime.fromisoformat(spec["notBefore"].replace("Z", "+00:00")) > now:
            continue
        if spec.get("expiresAt") and datetime.fromisoformat(spec["expiresAt"].replace("Z", "+00:00")) <= now:
            continue
        matches.append(grant)
    if len(matches) != 1:
        raise PermissionError("exactly one active TenantGrant is required")
    return matches[0]


def cpu_milli(value):
    value = str(value)
    return int(value[:-1]) if value.endswith("m") else int(value) * 1000


def binary_gib(value):
    value = str(value)
    factors = {"Ki": 1 / (1024 * 1024), "Mi": 1 / 1024, "Gi": 1, "Ti": 1024}
    for suffix, factor in factors.items():
        if value.endswith(suffix):
            return int(value[:-len(suffix)]) * factor
    raise PermissionError("memory and ephemeral storage quota require binary units")


def enforce_quota(tenant_ref, normalized, grant, exclude_name=None):
    constraints = grant.get("spec", {}).get("constraints", {})
    limits = constraints.get("capabilities", {}).get(normalized["spec"]["capability"], constraints)
    claims = [item for item in kube().list("tenantresourceclaims", runtime_namespace(tenant_ref)) if item.get("spec", {}).get("tenantRef") == tenant_ref and item.get("spec", {}).get("desiredState", "Active") == "Active" and item.get("metadata", {}).get("name") != exclude_name]
    capability_claims = [item for item in claims if item.get("spec", {}).get("capability") == normalized["spec"]["capability"]]
    if len(capability_claims) + 1 > int(limits.get("maxActiveClaims", 20)):
        raise PermissionError("TenantGrant maxActiveClaims quota exceeded")
    params = normalized["spec"]["parameters"]
    if normalized["spec"]["capability"] == "compute.workload.v1":
        units = params.get("replicas", params.get("parallelism", 1))
        if units > int(limits.get("maxPodsPerClaim", 20)):
            raise PermissionError("TenantGrant maxPodsPerClaim quota exceeded")
        public_count = sum(1 for item in claims if item.get("spec", {}).get("parameters", {}).get("exposure", {}).get("enabled"))
        if params.get("exposure", {}).get("enabled") and public_count + 1 > int(limits.get("maxPublicEndpoints", 5)):
            raise PermissionError("TenantGrant maxPublicEndpoints quota exceeded")
        total_cpu, total_memory, total_ephemeral = units * cpu_milli(params["resources"]["cpu"]), units * binary_gib(params["resources"]["memory"]), units * binary_gib(params["resources"]["ephemeralStorage"])
        for item in claims:
            if item.get("spec", {}).get("capability") != "compute.workload.v1":
                continue
            current = item["spec"]["parameters"]
            current_units = current.get("replicas", current.get("parallelism", 1))
            total_cpu += current_units * cpu_milli(current["resources"]["cpu"])
            total_memory += current_units * binary_gib(current["resources"]["memory"])
            total_ephemeral += current_units * binary_gib(current["resources"]["ephemeralStorage"])
        if total_cpu > int(limits.get("maxCpuMilli", 4000)):
            raise PermissionError("TenantGrant maxCpuMilli quota exceeded")
        if total_memory > int(limits.get("maxMemoryGiB", 16)):
            raise PermissionError("TenantGrant maxMemoryGiB quota exceeded")
        if total_ephemeral > int(limits.get("maxEphemeralStorageGiB", 40)):
            raise PermissionError("TenantGrant maxEphemeralStorageGiB quota exceeded")
    else:
        used = sum(int(item.get("spec", {}).get("parameters", {}).get("sizeGiB", 0)) for item in claims if item.get("spec", {}).get("capability") == "storage.volume.v1")
        if used + params["sizeGiB"] > int(limits.get("maxStorageGiB", 100)):
            raise PermissionError("TenantGrant maxStorageGiB quota exceeded")


def validate_volume_mounts(normalized, namespace):
    if normalized["spec"]["capability"] != "compute.workload.v1":
        return
    tenant = normalized["spec"]["tenantRef"]
    for mount in normalized["spec"]["parameters"].get("volumeMounts", []):
        try:
            volume = kube().get("tenantresourceclaims", mount["claimName"], namespace)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise PermissionError(f"volume claim {mount['claimName']} is unavailable") from None
            raise
        if volume.get("spec", {}).get("tenantRef") != tenant or volume.get("spec", {}).get("capability") != "storage.volume.v1" or volume.get("status", {}).get("phase") != "Ready":
            raise PermissionError(f"volume claim {mount['claimName']} must be a Ready same-tenant volume")


def operation_annotations(current, action, body):
    key = body.get("idempotencyKey")
    if not isinstance(key, str) or not 8 <= len(key) <= 128:
        raise ContractError("idempotencyKey must contain 8 to 128 characters")
    payload = {name: value for name, value in body.items() if name != "idempotencyKey"}
    digest = "sha256:" + hashlib.sha256(json.dumps([action, payload], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    annotations = current.get("metadata", {}).get("annotations", {})
    if annotations.get("tenancy.re8ch.com/last-operation-key") == key:
        if not hmac.compare_digest(annotations.get("tenancy.re8ch.com/last-operation-hash", ""), digest):
            raise PermissionError("idempotency key cannot be reused for a different operation")
        return None
    return {**annotations, "tenancy.re8ch.com/last-operation-key": key, "tenancy.re8ch.com/last-operation-hash": digest}


def verify_image_policy(image):
    if not IMAGE_POLICY_URL or not IMAGE_POLICY_TOKEN:
        raise PermissionError("image policy verifier is unavailable")
    request = urllib.request.Request(IMAGE_POLICY_URL + "/v1/verify", data=json.dumps({"imageDigest": image}).encode(), method="POST", headers={"Authorization": f"Bearer {IMAGE_POLICY_TOKEN}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
    except (urllib.error.URLError, ValueError) as exc:
        raise PermissionError("image policy verification failed") from exc
    policy = {key: bool(result.get(key)) for key in ("digestVerified", "signatureVerified", "scanPassed", "sbomPresent", "provenancePresent")}
    if not all(policy.values()):
        raise PermissionError("image signature, scan, SBOM and provenance are required")
    return {"verifiedImageDigest": image, **policy}


def claim_ready(claim, namespace):
    params = claim["spec"]["parameters"]
    name = resource_name(claim["metadata"]["name"])
    try:
        if claim["spec"]["capability"] == "storage.volume.v1":
            return kube().get("persistentvolumeclaims", name, namespace, "api/v1").get("status", {}).get("phase") == "Bound"
        if params["kind"] == "Deployment":
            value = kube().get("deployments", name, namespace, "apis/apps/v1")
            return value.get("status", {}).get("availableReplicas", 0) >= params["replicas"]
        value = kube().get("jobs", name, namespace, "apis/batch/v1")
        return value.get("status", {}).get("succeeded", 0) >= params["completions"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def release_claim(claim, namespace):
    if claim["spec"]["capability"] == "compute.workload.v1":
        name = resource_name(claim["metadata"]["name"])
        params = claim["spec"]["parameters"]
        if params["kind"] == "Deployment":
            kube().delete("deployments", name, namespace, "apis/apps/v1")
        else:
            kube().delete("jobs", name, namespace, "apis/batch/v1")
        kube().delete("networkpolicies", name, namespace, "apis/networking.k8s.io/v1")
        if params.get("exposure", {}).get("enabled"):
            kube().delete("services", name, namespace, "api/v1")
            kube().delete("httproutes", name, namespace, "apis/gateway.networking.k8s.io/v1")


def reconcile_claim(claim):
    meta, spec = claim["metadata"], claim["spec"]
    namespace = meta["namespace"]
    existing = claim.get("status", {})
    normalized = normalize_claim(claim, authenticated_tenant=spec["tenantRef"], harbor_project=harbor_project(spec["tenantRef"]))
    if normalized["spec"]["parameters"] != spec["parameters"]:
        raise ContractError("claim must contain the controller-normalized parameters")
    expires_at = existing.get("expiresAt")
    if not expires_at:
        created = datetime.fromisoformat(meta["creationTimestamp"].replace("Z", "+00:00"))
        expires_at = iso(created + timedelta(seconds=spec["ttlSeconds"]))
    expired = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= utcnow()
    if spec.get("desiredState", "Active") == "Released" or expired:
        release_claim(claim, namespace)
        status = {**existing, "observedGeneration": meta.get("generation", 1), "phase": "Released", "expiresAt": expires_at, **binding_status(claim, namespace), "conditions": [condition("Ready", False, "ClaimReleased")]}
        status["endpoint"] = None
        kube().patch_status("tenantresourceclaims", meta["name"], status, namespace)
        return
    grant = active_grant(spec["tenantRef"], spec["capability"])
    enforce_quota(spec["tenantRef"], normalized, grant, meta["name"])
    validate_volume_mounts(normalized, namespace)
    image_policy = existing.get("imagePolicy", {})
    if spec["capability"] == "compute.workload.v1" and image_policy.get("verifiedImageDigest") != spec["parameters"]["imageDigest"]:
        image_policy = verify_image_policy(spec["parameters"]["imageDigest"])
    pull_secret = image_pull_secret(spec["tenantRef"]) if spec["capability"] == "compute.workload.v1" else "tenant-harbor-pull"
    for resource in render_resources(claim, namespace, pull_secret):
        kube().apply(resource)
    if spec["capability"] == "compute.workload.v1" and not spec["parameters"].get("exposure", {}).get("enabled"):
        name = resource_name(meta["name"])
        kube().delete("services", name, namespace, "api/v1")
        kube().delete("httproutes", name, namespace, "apis/gateway.networking.k8s.io/v1")
    ready = claim_ready(claim, namespace)
    expiring = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) - utcnow() <= timedelta(seconds=min(300, max(60, spec["ttlSeconds"] // 10)))
    phase = "Expiring" if ready and expiring else "Ready" if ready else "Provisioning"
    status = {**existing, "observedGeneration": meta.get("generation", 1), "phase": phase, "expiresAt": expires_at, "runtimeNamespaceRef": namespace, "imagePolicy": image_policy, **binding_status(claim, namespace), "conditions": [condition("Ready", ready, "ClaimExpiring" if ready and expiring else "ChildrenReady" if ready else "ChildrenProvisioning")]}
    if spec["capability"] == "compute.workload.v1" and not spec["parameters"].get("exposure", {}).get("enabled"):
        status["endpoint"] = None
    kube().patch_status("tenantresourceclaims", meta["name"], status, namespace)


def reconcile_loop():
    while True:
        try:
            if not kube().claim_lease():
                time.sleep(5)
                continue
            for plural in ("tenantidentitybindings", "tenantgrants"):
                for item in kube().list(plural):
                    meta = item["metadata"]
                    status = {"observedGeneration": meta.get("generation", 1), "operationId": "op_" + hashlib.sha256(meta["uid"].encode()).hexdigest()[:24],
                              "conditions": [condition("Ready", True, "DeclarationAccepted")]}
                    if item.get("status", {}).get("observedGeneration") != status["observedGeneration"]:
                        kube().patch_status(plural, meta["name"], status)
            tenants = {item["metadata"]["name"]: item for item in kube().list("tenants") if item.get("spec", {}).get("lifecycle") == "Active"}
            for item in kube().list("tenantnamespaces"):
                try:
                    reconcile_tenant_namespace(item, tenants)
                except Exception as exc:
                    status = {"observedGeneration": item["metadata"].get("generation", 1), "operationId": "op_" + hashlib.sha256(item["metadata"]["uid"].encode()).hexdigest()[:24], "phase": "Failed", "reason": str(exc)[:1024], "conditions": [condition("Ready", False, "ReconcileFailed", str(exc))]}
                    kube().patch_status("tenantnamespaces", item["metadata"]["name"], status)
            namespace_items = kube().list("tenantnamespaces")
            for tenant_ref, tenant in tenants.items():
                managed = sorted(item["spec"]["namespaceName"] for item in namespace_items if item.get("spec", {}).get("tenantRef") == tenant_ref and item.get("spec", {}).get("operation") == "EnsurePresent")
                meta = tenant["metadata"]
                policy = namespace_policy(tenant_ref)
                namespaces_ready = not policy["allowCreate"] or policy["defaultNamespace"] in managed
                status = {"observedGeneration": meta.get("generation", 1), "operationId": "op_" + hashlib.sha256(meta["uid"].encode()).hexdigest()[:24], "managedNamespaces": managed, "conditions": [condition("Ready", namespaces_ready, "NamespacesReady" if namespaces_ready else "DefaultNamespacePending")]}
                if tenant.get("status", {}).get("observedGeneration") != status["observedGeneration"] or tenant.get("status", {}).get("managedNamespaces") != managed:
                    kube().patch_status("tenants", tenant_ref, status)
                claim_namespaces = set(managed)
                legacy = tenant.get("spec", {}).get("runtimeNamespaceRef")
                if legacy:
                    claim_namespaces.add(legacy)
                for namespace in claim_namespaces:
                    for claim in kube().list("tenantresourceclaims", namespace):
                        try:
                            reconcile_claim(claim)
                        except Exception as exc:
                            status = {**claim.get("status", {}), "observedGeneration": claim["metadata"].get("generation", 1), "phase": "Failed", "conditions": [condition("Ready", False, "ReconcileFailed", str(exc))]}
                            kube().patch_status("tenantresourceclaims", claim["metadata"]["name"], status, namespace)
        except Exception as exc:
            LOG.exception("reconcile iteration failed: %s", exc)
        time.sleep(10)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/livez": self.send_json(200, {"status": "ok"}); return
        if path == "/readyz":
            try:
                kube().list("tenants")
                kube().get("leases", LEASE_NAME, NAMESPACE, "apis/coordination.k8s.io/v1")
                self.send_json(200, {"status": "ready"})
            except Exception: self.send_json(503, {"status": "not-ready"})
            return
        service_token = self.headers.get("X-Tenant-Service-Token", "")
        caller = self.headers.get("X-Tenant-Caller", "")
        if path == "/v1/tenants":
            if caller != "admin" or self.headers.get("X-Tenant-Ref", "") != "admin" or not CLAIM_SERVICE_TOKEN or not hmac.compare_digest(service_token, CLAIM_SERVICE_TOKEN):
                self.send_json(403, {"error": "protected admin tenant is required"}); return
            try:
                items = []
                for item in kube().list("tenants"):
                    meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
                    ready = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), None)
                    items.append({
                        "tenantRef": meta.get("name"), "displayName": spec.get("displayName"),
                        "visibility": spec.get("visibility", "Private"), "state": spec.get("lifecycle"),
                        "defaultNamespace": (spec.get("namespacePolicy") or {}).get("defaultNamespace") or spec.get("runtimeNamespaceRef"),
                        "managedNamespaces": status.get("managedNamespaces", []),
                        "runtimeNamespaceRef": spec.get("runtimeNamespaceRef"),
                        "ready": bool(ready and ready.get("status") == "True"),
                        "observedGeneration": status.get("observedGeneration"),
                    })
                self.send_json(200, {"items": items}); return
            except Exception as exc:
                LOG.exception("tenant inventory read failed: %s", exc)
                self.send_json(503, {"error": "tenant inventory is unavailable"}); return
        if caller == "tenant" and path == "/v1/namespaces" and CLAIM_SERVICE_TOKEN and hmac.compare_digest(service_token, CLAIM_SERVICE_TOKEN):
            tenant = self.headers.get("X-Tenant-Ref", "")
            items = [item for item in kube().list("tenantnamespaces") if item.get("spec", {}).get("tenantRef") == tenant]
            self.send_json(200, {"items": items}); return
        if not path.startswith("/v1/claims") or caller != "tenant" or not CLAIM_SERVICE_TOKEN or not hmac.compare_digest(service_token, CLAIM_SERVICE_TOKEN):
            self.send_json(404, {"error": "not_found"}); return
        try:
            tenant = self.headers["X-Tenant-Ref"]
            namespace = runtime_namespace(tenant)
            suffix = path.removeprefix("/v1/claims").strip("/")
            if suffix:
                value = kube().get("tenantresourceclaims", suffix, namespace)
                if value.get("spec", {}).get("tenantRef") != tenant:
                    raise PermissionError("claim is outside the authenticated tenant")
                self.send_json(200, value)
            else:
                values = [item for item in kube().list("tenantresourceclaims", namespace) if item.get("spec", {}).get("tenantRef") == tenant]
                self.send_json(200, {"items": values})
        except KeyError:
            self.send_json(400, {"error": "tenant context is required"})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except urllib.error.HTTPError as exc:
            self.send_json(exc.code, {"error": "claim_not_found" if exc.code == 404 else "cluster_api_error"})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})
        return

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        caller = self.headers.get("X-Tenant-Caller", "")
        if caller not in ALLOWED_CALLERS:
            self.send_json(401, {"error": "unauthorized"}); return
        if path != "/v1/resolve" and caller != "tenant":
            self.send_json(404, {"error": "not_found"}); return
        if path != "/v1/resolve" and (not CLAIM_SERVICE_TOKEN or not hmac.compare_digest(self.headers.get("X-Tenant-Service-Token", ""), CLAIM_SERVICE_TOKEN)):
            self.send_json(401, {"error": "unauthorized"}); return
        mutation_locked = path.startswith("/v1/claims") and path != "/v1/claims/plan"
        if mutation_locked:
            CLAIM_MUTATION_LOCK.acquire()
        try:
            size = min(int(self.headers.get("Content-Length", "0")), 131072)
            body = json.loads(self.rfile.read(size))
            if path == "/v1/resolve":
                issuer = str(body["issuer"])
                subject_hash = str(body.get("subjectHash") or "")
                groups = body.get("groups") or []
                if subject_hash and not subject_hash.startswith("sha256:"): raise ValueError()
                if not isinstance(groups, list) or len(groups) > 128 or any(not isinstance(group, str) or len(group) > 256 for group in groups): raise ValueError()
                if not subject_hash and not groups: raise ValueError()
                self.send_json(200, resolve(issuer, subject_hash, groups, caller=caller))
                return
            tenant = self.headers["X-Tenant-Ref"]
            namespace = runtime_namespace(tenant)
            if path == "/v1/namespaces":
                namespace = str(body["namespaceName"])
                policy = validate_tenant_namespace(tenant, namespace, "EnsurePresent")
                active = [item for item in kube().list("tenantnamespaces") if item.get("spec", {}).get("tenantRef") == tenant and item.get("spec", {}).get("operation") == "EnsurePresent"]
                if namespace not in {item["spec"]["namespaceName"] for item in active} and len(active) >= policy["maxNamespaces"]:
                    raise PermissionError("tenant namespace quota exceeded")
                name = tenant_namespace_name(tenant, namespace)
                desired = {"apiVersion": f"{GROUP}/{VERSION}", "kind": "TenantNamespace", "metadata": {"name": name, "labels": {"tenancy.re8ch.com/tenant": tenant}}, "spec": {"tenantRef": tenant, "namespaceName": namespace, "operation": "EnsurePresent"}}
                try:
                    value = kube().create("tenantnamespaces", desired)
                except urllib.error.HTTPError as exc:
                    if exc.code != 409:
                        raise
                    value = kube().patch("tenantnamespaces", name, {"spec": {"operation": "EnsurePresent"}})
                self.send_json(202, value)
                return
            if path == "/v1/claims/plan":
                grant = active_grant(tenant, body["claim"]["spec"]["capability"])
                result = plan(body["claim"], authenticated_tenant=tenant, harbor_project=harbor_project(tenant))
                enforce_quota(tenant, result["normalizedClaim"], grant, result["normalizedClaim"]["metadata"]["name"])
                validate_volume_mounts(result["normalizedClaim"], namespace)
                self.send_json(200, result)
                return
            if path == "/v1/claims":
                result = plan(body["claim"], authenticated_tenant=tenant, harbor_project=harbor_project(tenant))
                if not hmac.compare_digest(str(body.get("claimHash", "")), result["claimHash"]):
                    raise PermissionError("claimHash does not match the normalized claim")
                normalized = result["normalizedClaim"]
                grant = active_grant(tenant, normalized["spec"]["capability"])
                enforce_quota(tenant, normalized, grant, normalized["metadata"]["name"])
                validate_volume_mounts(normalized, namespace)
                image_policy = {}
                if normalized["spec"]["capability"] == "compute.workload.v1":
                    image_policy = verify_image_policy(normalized["spec"]["parameters"]["imageDigest"])
                normalized["metadata"]["namespace"] = namespace
                normalized["metadata"]["labels"] = {"tenancy.re8ch.com/tenant": tenant, "birfucate.re8ch.com/metered": "true"}
                normalized["spec"]["desiredState"] = "Active"
                created_now = True
                try:
                    created = kube().create("tenantresourceclaims", normalized, namespace)
                except urllib.error.HTTPError as exc:
                    if exc.code != 409:
                        raise
                    created = kube().get("tenantresourceclaims", normalized["metadata"]["name"], namespace)
                    if created.get("spec", {}).get("idempotencyKey") != normalized["spec"]["idempotencyKey"]:
                        raise PermissionError("claim name is already bound to another idempotency key")
                    comparable = {key: value for key, value in created.get("spec", {}).items() if key != "desiredState"}
                    planned = {key: value for key, value in normalized["spec"].items() if key != "desiredState"}
                    if comparable != planned:
                        raise PermissionError("idempotency key cannot be reused for a different claim")
                    created_now = False
                if not created_now:
                    self.send_json(200, created)
                    return
                status = {"phase": "Pending", "claimHash": result["claimHash"], "expiresAt": iso(utcnow() + timedelta(seconds=normalized["spec"]["ttlSeconds"])), "runtimeNamespaceRef": namespace, "imagePolicy": image_policy, "conditions": [condition("Ready", False, "Accepted")]}
                kube().patch_status("tenantresourceclaims", normalized["metadata"]["name"], status, namespace)
                self.send_json(201, {**created, "status": status})
                return
            suffix = path.removeprefix("/v1/claims/").split("/")
            if len(suffix) != 2 or suffix[1] not in {"update", "renew", "cancel"}:
                self.send_json(404, {"error": "not_found"}); return
            name, action = suffix
            current = kube().get("tenantresourceclaims", name, namespace)
            if current.get("spec", {}).get("tenantRef") != tenant:
                raise PermissionError("claim is outside the authenticated tenant")
            annotations = operation_annotations(current, action, body)
            if annotations is None:
                self.send_json(200, current)
                return
            if action == "cancel":
                value = kube().patch("tenantresourceclaims", name, {"metadata": {"annotations": annotations}, "spec": {"desiredState": "Released"}}, namespace)
            elif action == "renew":
                ttl = int(body.get("ttlSeconds", current["spec"]["ttlSeconds"]))
                if not 300 <= ttl <= 2_592_000:
                    raise ContractError("ttlSeconds is outside the renewable range")
                value = kube().patch("tenantresourceclaims", name, {"metadata": {"annotations": annotations}, "spec": {"ttlSeconds": ttl}}, namespace)
                kube().patch_status("tenantresourceclaims", name, {**current.get("status", {}), "expiresAt": iso(utcnow() + timedelta(seconds=ttl)), "phase": "Updating"}, namespace)
            else:
                project = harbor_project(tenant)
                proposed = validate_update(current, body["claim"], authenticated_tenant=tenant, harbor_project=project)
                result = plan(proposed, authenticated_tenant=tenant, harbor_project=project)
                if not hmac.compare_digest(str(body.get("claimHash", "")), result["claimHash"]):
                    raise PermissionError("claimHash does not match the normalized claim")
                grant = active_grant(tenant, proposed["spec"]["capability"])
                enforce_quota(tenant, proposed, grant, name)
                validate_volume_mounts(proposed, namespace)
                image_policy = current.get("status", {}).get("imagePolicy", {})
                if proposed["spec"]["capability"] == "compute.workload.v1" and proposed["spec"]["parameters"]["imageDigest"] != current["spec"]["parameters"]["imageDigest"]:
                    image_policy = verify_image_policy(proposed["spec"]["parameters"]["imageDigest"])
                value = kube().patch("tenantresourceclaims", name, {"metadata": {"annotations": annotations}, "spec": proposed["spec"]}, namespace)
                kube().patch_status("tenantresourceclaims", name, {**current.get("status", {}), "phase": "Updating", "claimHash": result["claimHash"], "imagePolicy": image_policy}, namespace)
            self.send_json(200, value)
        except PermissionError as exc: self.send_json(403, {"error": str(exc)})
        except ContractError as exc: self.send_json(422, {"error": str(exc)})
        except urllib.error.HTTPError as exc: self.send_json(exc.code, {"error": "cluster_api_error"})
        except Exception as exc: self.send_json(400, {"error": str(exc) or "invalid_request"})
        finally:
            if mutation_locked:
                CLAIM_MUTATION_LOCK.release()

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        service_token = self.headers.get("X-Tenant-Service-Token", "")
        if not path.startswith("/v1/namespaces/") or self.headers.get("X-Tenant-Caller") != "tenant" or not CLAIM_SERVICE_TOKEN or not hmac.compare_digest(service_token, CLAIM_SERVICE_TOKEN):
            self.send_json(404, {"error": "not_found"}); return
        try:
            tenant = self.headers["X-Tenant-Ref"]
            namespace = urllib.parse.unquote(path.removeprefix("/v1/namespaces/"))
            policy = validate_tenant_namespace(tenant, namespace, "EnsureAbsent")
            if namespace == policy["defaultNamespace"]:
                raise PermissionError("the tenant default namespace cannot be deleted")
            name = tenant_namespace_name(tenant, namespace)
            current = kube().get("tenantnamespaces", name)
            if current.get("spec", {}).get("tenantRef") != tenant or current.get("spec", {}).get("namespaceName") != namespace:
                raise PermissionError("namespace is outside the authenticated tenant")
            value = kube().patch("tenantnamespaces", name, {"spec": {"operation": "EnsureAbsent"}})
            self.send_json(202, value)
        except KeyError:
            self.send_json(400, {"error": "tenant context is required"})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except urllib.error.HTTPError as exc:
            self.send_json(exc.code, {"error": "namespace_not_found" if exc.code == 404 else "cluster_api_error"})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def log_message(self, *_): pass


if __name__ == "__main__":
    KUBE = Kubernetes()
    threading.Thread(target=reconcile_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
