import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from cluster_bindings import ClusterBindings, binding_digest
from runtime import OperationError

NOW = dt.datetime(2026, 11, 23, tzinfo=dt.timezone.utc)


@pytest.fixture
def authority():
    document = json.loads((Path(__file__).parents[1] / "platform/cloud-platform/cluster-bindings.json").read_text())
    binding = document["clusters"]["re8ch-atlantic"]
    self_binding = document["clusters"]["re8ch"]
    objects = {
        "clusteridentities": {"metadata": dict(self_binding["clusterIdentityRef"]), "spec": {"clusterID": "re8ch", "trustDomain": "re8ch.internal"}},
        "services": {"metadata": dict(self_binding["apiServiceRef"]), "spec": {"ports": [{"port": 443}]}}
    }
    for key, resource in (("advertisementRef", "serviceadvertisements"), ("importedServiceRef", "importedservices"),
                          ("grantRef", "managedaccessgrants"), ("peerRef", "peers")):
        ref = binding[key]
        objects[resource] = {"metadata": {**ref, "namespace": "byoc-pilot", "generation": 1}, "spec": {}, "status": {}}
    ad, imp, grant, peer = [objects[x] for x in ("serviceadvertisements", "importedservices", "managedaccessgrants", "peers")]
    ad["metadata"]["annotations"] = {"peering.re8ch.com/last-published-at": NOW.isoformat()}
    ad["spec"] = {"publisherRef": binding["publisherRef"], "serviceIdentity": binding["serviceIdentity"],
                  "protocol": "kubernetes-api", "ttlSeconds": 300, "revoked": False, "targetPeers": [binding["peerRef"]["name"]]}
    imp["spec"] = {"advertisementRef": ad["metadata"]["name"], "peerRef": peer["metadata"]["name"], "suspended": False}
    imp["status"] = {"state": "Ready", "observedGeneration": 1, "advertisementUID": ad["metadata"]["uid"], "advertisementGeneration": 1}
    grant["spec"] = {**copy.deepcopy(binding["grantScope"]), "approved": True, "revoked": False,
                     "expiresAt": (NOW + dt.timedelta(days=1)).isoformat(),
                     "advertisementRef": ad["metadata"]["name"], "peerRef": peer["metadata"]["name"]}
    peer["spec"] = {"suspended": False, "localIdentityRef": "re8ch", "remoteIdentityRef": binding["publisherRef"]}
    peer["status"] = {"state": "Ready", "observedGeneration": 1}
    def read(resource, namespace, name):
        obj = objects[resource.split(".")[0]]
        assert namespace == obj["metadata"]["namespace"]
        assert name == obj["metadata"]["name"]
        return obj
    return ClusterBindings(document, read, lambda: NOW), objects


def test_self_and_managed_cluster_share_callable_binding_model(authority):
    resolver, _ = authority
    result = resolver.list()
    assert [c["clusterRef"] for c in result["clusters"]] == ["re8ch", "re8ch-atlantic"]
    assert result["clusters"][0]["state"] == "Ready"
    assert result["clusters"][0]["providerHealth"] == "Unverified"
    assert result["clusters"][1]["state"] == "Ready"
    assert result["registrationPolicyIndependent"]
    assert "brokerURL" not in json.dumps(result)
    assert resolver.resolve("re8ch")["adapter"] == "local"
    with pytest.raises(OperationError):
        resolver.resolve("unknown")


@pytest.mark.parametrize("resource", ["serviceadvertisements", "importedservices", "managedaccessgrants", "peers"])
def test_same_name_replacement_is_rejected(authority, resource):
    resolver, objects = authority
    objects[resource]["metadata"]["uid"] = "replacement"
    with pytest.raises(OperationError):
        resolver.resolve("re8ch-atlantic")


@pytest.mark.parametrize("case", ["expired-ad", "revoked-ad", "future-ad", "stale-import", "failed-import", "expired-grant", "changed-scope", "suspended-peer"])
def test_current_authority_required_and_list_does_not_claim_ready(authority, case):
    resolver, objects = authority
    if case == "expired-ad":
        objects["serviceadvertisements"]["metadata"]["annotations"]["peering.re8ch.com/last-published-at"] = (NOW - dt.timedelta(seconds=300)).isoformat()
    elif case == "future-ad":
        objects["serviceadvertisements"]["metadata"]["annotations"]["peering.re8ch.com/last-published-at"] = (NOW + dt.timedelta(seconds=1)).isoformat()
    elif case == "revoked-ad":
        objects["serviceadvertisements"]["spec"]["revoked"] = True
    elif case == "stale-import":
        objects["importedservices"]["metadata"]["generation"] = 2
    elif case == "failed-import":
        objects["importedservices"]["status"] = {"state": "Failed"}
    elif case == "expired-grant":
        objects["managedaccessgrants"]["spec"]["expiresAt"] = NOW.isoformat()
    elif case == "changed-scope":
        objects["managedaccessgrants"]["spec"]["scope"] = "Namespaced"
    else:
        objects["peers"]["spec"]["suspended"] = True
    with pytest.raises(OperationError):
        resolver.resolve("re8ch-atlantic")
    assert resolver.list()["clusters"][1]["state"] == "Unavailable"


def test_read_failure_never_falls_back_to_self(authority):
    resolver, _ = authority
    resolver.read = lambda *_: (_ for _ in ()).throw(OperationError("BACKEND_TIMEOUT", "unavailable"))
    with pytest.raises(OperationError):
        resolver.resolve("re8ch-atlantic")
    assert resolver.list()["clusters"][0]["state"] == "Unavailable"


@pytest.mark.parametrize("resource", ["clusteridentities", "services"])
def test_self_binding_requires_live_original_identity(authority, resource):
    resolver, objects = authority
    objects[resource]["metadata"]["uid"] = "replacement"
    with pytest.raises(OperationError): resolver.resolve("re8ch")
    assert resolver.list()["clusters"][0]["state"] == "Unavailable"


def test_release_grant_is_distinct_scoped_and_uid_bound(authority):
    resolver, objects = authority
    source = resolver.bindings["re8ch-atlantic"]
    scope = {"scope": "Namespaced", "namespaces": ["default"], "rules": [{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "patch"], "resourceNames": ["reviewed-probe"]}]}
    grant = copy.deepcopy(objects["managedaccessgrants"])
    grant["metadata"].update(name="scoped-release-grant", uid="release-grant-uid")
    grant["spec"].update(scope)
    source["executionBindings"] = {"scoped-release": {"grantRef": {"name": "scoped-release-grant", "uid": "release-grant-uid"}, "grantScope": copy.deepcopy(scope)}}
    original_read = resolver.read
    resolver.read = lambda resource, namespace, name: grant if name == "scoped-release-grant" else original_read(resource, namespace, name)
    result = resolver.resolve_execution("re8ch-atlantic", "scoped-release")
    assert result["grantRef"]["name"] == "scoped-release-grant"
    assert result["scope"] == scope
    assert result["bindingDigest"] == binding_digest(source)
    with pytest.raises(OperationError): resolver.resolve_execution("re8ch-atlantic", "unreviewed")
    grant["metadata"]["uid"] = "replaced"
    with pytest.raises(OperationError): resolver.resolve_execution("re8ch-atlantic", "scoped-release")


def test_release_cannot_inherit_admin_grant_or_wildcard_scope(authority):
    resolver, _ = authority
    source = resolver.bindings["re8ch-atlantic"]
    source["executionBindings"] = {"unsafe": {"grantRef": source["grantRef"], "grantScope": source["grantScope"]}}
    with pytest.raises(OperationError): resolver.resolve_execution("re8ch-atlantic", "unsafe")
    source["executionBindings"]["unsafe"]["grantRef"] = {"name": "other", "uid": "other"}
    with pytest.raises(OperationError): resolver.resolve_execution("re8ch-atlantic", "unsafe")
