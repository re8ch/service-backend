"""Request-scoped Kubernetes transport for the existing bounded SSA executor."""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

from runtime import OperationError
from cluster_bindings import no_redirect_open

PROOF_HEADER = "X-Re8ch-Execution-Proof"
BINDING_HEADER = "X-Re8ch-Cluster-Binding"
RELEASE_HEADER = "X-Re8ch-Release-Id"


def caller_identity(proof, open_url=no_redirect_open):
    """Native self-review authenticates the original caller without review RBAC.

    The broker subsequently performs its existing TokenReview and exact named
    grant-use SAR for this same proof on every forwarded request.
    """
    if not proof.startswith("Bearer ") or not proof[7:].strip():
        raise OperationError("AUTHENTICATION_REQUIRED", "execution caller proof is required")
    host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    request = urllib.request.Request(f"https://{host}:{port}/apis/authentication.k8s.io/v1/selfsubjectreviews",
        data=json.dumps({"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"}).encode(),
        method="POST", headers={"Authorization": proof, "Content-Type": "application/json"})
    context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    try:
        with open_url(request, context=context, timeout=30) as response:
            identity = json.load(response).get("status", {}).get("userInfo", {})
    except Exception:
        # Exception strings can contain headers; never return or log the proof.
        raise OperationError("AUTHENTICATION_REQUIRED", "execution caller authentication failed") from None
    if not identity.get("username") or identity["username"] == "system:anonymous":
        raise OperationError("AUTHENTICATION_REQUIRED", "execution caller authentication failed")
    return identity


def proxy_request(service, method, target, headers, body, *, authenticate=caller_identity, open_url=no_redirect_open):
    parsed = urllib.parse.urlsplit(target)
    parts = parsed.path.split("/", 3)
    if len(parts) != 4 or parts[1] != "k8s" or not parts[2]:
        raise OperationError("INVALID_ARGUMENT", "a reviewed cluster transport path is required")
    cluster_ref, path = parts[2], "/" + parts[3]
    decoded = urllib.parse.unquote(path)
    if (method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            or not (path.startswith(("/api/", "/apis/", "/openapi/")) or path in {"/api", "/apis", "/version", "/readyz", "/livez"})
            or ".." in decoded.split("/") or "\r" in decoded or "\n" in decoded):
        raise OperationError("INVALID_ARGUMENT", "only Kubernetes API paths are accepted")
    proof = headers.get(PROOF_HEADER, "")
    identity = authenticate(proof)
    release_id = headers.get(RELEASE_HEADER, "")
    if not release_id:
        raise OperationError("INVALID_ARGUMENT", "scoped release identity is required")
    binding = service.cluster_bindings().resolve_execution(cluster_ref, release_id)
    if binding["adapter"] != "among-broker":
        raise OperationError("INVALID_ARGUMENT", "self cluster uses its existing direct API transport")
    if headers.get(BINDING_HEADER) != binding["bindingDigest"]:
        raise OperationError("CLUSTER_BINDING_CHANGED", "execution binding differs from the reviewed cluster")
    url = binding["brokerURL"] + path + (("?" + parsed.query) if parsed.query else "")
    forwarded = {key: headers[key] for key in ("Content-Type", "Accept", "Accept-Encoding") if headers.get(key)}
    forwarded["Authorization"] = proof
    request = urllib.request.Request(url, data=body, method=method, headers=forwarded)
    try:
        response = open_url(request, timeout=120)
    except urllib.error.HTTPError as error:
        response = error
    except Exception:
        raise OperationError("CLUSTER_TRANSPORT_UNAVAILABLE", "managed cluster transport failed", retryable=True) from None
    # No local or remote credential material is included in this audit envelope.
    audit = {"clusterRef": cluster_ref, "clusterBindingDigest": binding["bindingDigest"],
             "releaseId": release_id,
             "caller": identity["username"], "method": method, "status": response.status}
    return response, audit
