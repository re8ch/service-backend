"""Resolve reviewed cluster identities; remote credentials stay in Among's broker."""
from __future__ import annotations

import datetime as dt
import copy
import hashlib
import json
import os
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

from runtime import OperationError

GROUP = "peering.re8ch.com"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def no_redirect_open(request, *, context=None, timeout=30):
    handlers = [NoRedirect()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)


class BoundKubeconfig(str):
    """An explicitly selected target must ignore the local API override."""
    server_override = ""


class ProjectedMetadataReader:
    def __init__(self):
        self.context = None
        self.server = "https://" + os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc") + ":" + os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")

    def __call__(self, resource, namespace, name):
        plural, group = resource.split(".", 1)
        path = "/apis/" + group + "/v1alpha1/namespaces/" + quote(namespace, safe="") + "/" + plural + "/" + quote(name, safe="")
        try:
            if self.context is None:
                self.context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
            token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
            request = urllib.request.Request(self.server + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
            with no_redirect_open(request, context=self.context, timeout=30) as response:
                return json.load(response)
        except Exception:
            raise OperationError("CLUSTER_BINDING_UNAVAILABLE", "cluster authority read failed", retryable=True) from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def binding_digest(binding):
    return hashlib.sha256(canonical(binding).encode()).hexdigest()


def reject(message):
    raise OperationError("CLUSTER_BINDING_UNAVAILABLE", message)


def timestamp(value):
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError):
        reject("cluster authority has an invalid timestamp")


class ClusterBindings:
    def __init__(self, document, read, now=lambda: dt.datetime.now(dt.timezone.utc)):
        self.bindings = document.get("clusters", {})
        self.read, self.now = read, now
        if not isinstance(self.bindings, dict) or "re8ch" not in self.bindings:
            reject("reviewed self cluster binding is required")

    @classmethod
    def load(cls, path, read):
        try:
            return cls(json.loads(Path(path).read_text()), read)
        except (OSError, ValueError, TypeError):
            reject("reviewed cluster binding configuration is unavailable")

    def resolve(self, cluster_ref):
        if cluster_ref not in self.bindings:
            reject("clusterRef has no reviewed binding")
        binding = self.bindings[cluster_ref]
        if binding.get("clusterRef") != cluster_ref:
            reject("cluster binding identity mismatch")
        result = {"clusterRef": cluster_ref, "bindingDigest": binding_digest(binding),
                  "adapter": binding.get("adapter"), "ownership": "re8ch-admin"}
        if binding.get("adapter") == "local":
            if cluster_ref != "re8ch":
                reject("only the existing self cluster can use the local adapter")
            return result
        if binding.get("adapter") != "among-broker":
            reject("unsupported cluster adapter")
        namespace = binding["namespace"]
        objects = {}
        for field, resource in (("advertisementRef", "serviceadvertisements"),
                                ("importedServiceRef", "importedservices"),
                                ("grantRef", "managedaccessgrants"), ("peerRef", "peers")):
            ref = binding[field]
            obj = self.read(resource + "." + GROUP, namespace, ref["name"])
            meta = obj.get("metadata", {})
            if (meta.get("namespace") != namespace or meta.get("name") != ref["name"]
                    or meta.get("uid") != ref["uid"] or meta.get("deletionTimestamp")):
                reject("cluster authority object was replaced or withdrawn")
            objects[field] = obj
        ad, imp, grant, peer = (objects[k] for k in
                               ("advertisementRef", "importedServiceRef", "grantRef", "peerRef"))
        spec, imps, gs = ad.get("spec", {}), imp.get("spec", {}), grant.get("spec", {})
        now = self.now()
        published = timestamp(ad.get("metadata", {}).get("annotations", {}).get("peering.re8ch.com/last-published-at"))
        ttl = spec.get("ttlSeconds")
        if (type(ttl) is not int or not 30 <= ttl <= 86400 or not published <= now
                or now >= published + dt.timedelta(seconds=ttl)):
            reject("cluster advertisement is expired or not yet valid")
        if (spec.get("publisherRef") != binding["publisherRef"]
                or spec.get("serviceIdentity") != binding["serviceIdentity"]
                or spec.get("protocol") != "kubernetes-api" or spec.get("revoked") is not False):
            reject("cluster advertisement identity or protocol mismatch")
        peer_name, ad_name = binding["peerRef"]["name"], binding["advertisementRef"]["name"]
        status = imp.get("status", {})
        if (imps.get("advertisementRef") != ad_name or imps.get("peerRef") != peer_name
                or imps.get("suspended") is not False or peer_name not in spec.get("targetPeers", [])
                or status.get("state") != "Ready"
                or status.get("observedGeneration") != imp["metadata"].get("generation")
                or status.get("advertisementUID") != ad["metadata"]["uid"]
                or status.get("advertisementGeneration") != ad["metadata"].get("generation")):
            reject("cluster import does not observe the current advertisement")
        if (peer.get("spec", {}).get("suspended") is not False
                or peer.get("spec", {}).get("localIdentityRef") != "re8ch"
                or peer.get("spec", {}).get("remoteIdentityRef") != binding["publisherRef"]
                or peer.get("status", {}).get("state") != "Ready"
                or peer.get("status", {}).get("observedGeneration") != peer["metadata"].get("generation")):
            reject("cluster peer is not Ready")
        if (gs.get("advertisementRef") != ad_name or gs.get("peerRef") != peer_name
                or gs.get("approved") is not True or gs.get("revoked") is not False
                or timestamp(gs.get("expiresAt")) <= now):
            reject("cluster access grant is unavailable")
        # Scope is reviewed intent, not inferred from an opaque locator or namespace.
        for key in ("scope", "namespaces", "rules"):
            if gs.get(key, [] if key != "scope" else "Namespaced") != binding["grantScope"].get(key, [] if key != "scope" else "Namespaced"):
                reject("cluster access grant scope differs from the reviewed binding")
        endpoint = urlsplit(binding["brokerURL"])
        if (endpoint.scheme not in ("http", "https") or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.path not in ("", "/")
                or endpoint.query or endpoint.fragment):
            reject("invalid reviewed broker endpoint")
        result.update(namespace=namespace, grantRef=binding["grantRef"],
                      brokerURL=binding["brokerURL"].rstrip("/") + "/k8s/" + quote(namespace, safe="") + "/" + quote(binding["grantRef"]["name"], safe=""),
                      scope=binding["grantScope"])
        return result

    def resolve_execution(self, cluster_ref, release_id):
        """Actions can only select a separately reviewed scoped release grant."""
        if cluster_ref not in self.bindings:
            reject("clusterRef has no reviewed binding")
        source = self.bindings[cluster_ref]
        execution = source.get("executionBindings", {}).get(release_id)
        if not execution:
            reject("release has no reviewed scoped cluster execution grant")
        execution_ref, interactive_ref = execution.get("grantRef", {}), source.get("grantRef", {})
        if execution_ref.get("name") == interactive_ref.get("name") or execution_ref.get("uid") == interactive_ref.get("uid"):
            reject("release execution cannot inherit the interactive Admin grant")
        scope = execution.get("grantScope", {})
        if scope.get("scope") != "Namespaced" or not scope.get("namespaces"):
            reject("release execution requires an explicit namespace scope")
        for rule in scope.get("rules", []):
            if not rule.get("resourceNames") or "*" in rule["resourceNames"] or "*" in rule.get("resources", []) or "*" in rule.get("verbs", []):
                reject("release execution requires exact objects and verbs")
        if not scope.get("rules"):
            reject("release execution has no reviewed resource rules")
        narrowed = copy.deepcopy(source)
        narrowed["grantRef"], narrowed["grantScope"] = execution["grantRef"], execution["grantScope"]
        resolver = ClusterBindings({"clusters": {"re8ch": self.bindings["re8ch"], cluster_ref: narrowed}}, self.read, self.now)
        result = resolver.resolve(cluster_ref)
        result["bindingDigest"] = binding_digest(source)
        return result

    def list(self):
        result = []
        for ref in sorted(self.bindings):
            try:
                resolved = self.resolve(ref)
                result.append({k: v for k, v in resolved.items() if k != "brokerURL"} |
                              {"state": "Configured" if resolved["adapter"] == "local" else "Ready",
                               "providerHealth": "Unverified", "callable": True,
                               "isSelf": resolved["adapter"] == "local",
                               "source": "platform-self" if resolved["adapter"] == "local" else "admin-managed-byoc",
                               "executionReleases": sorted(self.bindings[ref].get("executionBindings", {}))})
            except OperationError:
                result.append({"clusterRef": ref, "state": "Unavailable", "ownership": "re8ch-admin"})
        return {"clusters": result, "registrationPolicyIndependent": True}
