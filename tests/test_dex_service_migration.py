"""The new issuer must be additive and preserve the canonical Dex credentials."""

import base64
import copy
import time
import urllib.error
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_parallel_dex_config_preserves_old_issuer_and_client_credentials():
    resources = list(yaml.safe_load_all((ROOT / "clusters/re8ch/components/auth/dex/dex-service-config-reconciler.yaml").read_text()))
    script = next(item for item in resources if item["kind"] == "ConfigMap")["data"]["reconcile.py"]
    body = script[script.index("source = request"):]
    config = {
        "issuer": "https://dex.re8ch.com",
        "connectors": [{"type": "github", "config": {"clientSecret": "test-only-secret",
                          "redirectURI": "https://dex.re8ch.com/callback"}}],
        "staticClients": [{"id": "headlamp", "secret": "another-test-only-secret",
                           "redirectURIs": ["https://headlamp.re8ch.com/oidc-callback"]}],
    }
    old = {"data": {"config.yaml": base64.b64encode(yaml.safe_dump(config).encode()).decode()}}
    created = []
    target = None
    broker_secret = None
    identity_secret = None

    def request(path, method="GET", body=None, content_type="application/json"):
        nonlocal target
        if method == "GET" and path.endswith("/dex-config"):
            return copy.deepcopy(old)
        if method == "GET" and path.endswith("/dex-config-service"):
            if target is None:
                raise urllib.error.HTTPError(path, 404, "missing", {}, None)
            return copy.deepcopy(target)
        if method == "GET" and path.endswith("/re8ch-auth-broker-oidc"):
            if broker_secret is None:
                raise urllib.error.HTTPError(path, 404, "missing", {}, None)
            return copy.deepcopy(broker_secret)
        if method == "GET" and path.endswith("/supabase-identity-oidc"):
            if identity_secret is None:
                raise urllib.error.HTTPError(path, 404, "missing", {}, None)
            return copy.deepcopy(identity_secret)
        created.append((method, path))
        if method == "PATCH" and path.endswith("/dex-config"):
            old["data"].update(body["data"])
        if method == "POST" and path.endswith("/secrets"):
            target = body
        if method == "PATCH" and path.endswith("/dex-config-service"):
            target["data"].update(body["data"])
        return {}

    scope = dict(base64=base64, copy=copy, yaml=yaml, time=time, urllib=urllib, request=request)
    exec(compile(body, "dex-service-reconciler-test", "exec"), scope)
    derived = yaml.safe_load(base64.b64decode(target["data"]["config.yaml"]))
    canonical = yaml.safe_load(base64.b64decode(old["data"]["config.yaml"]))
    assert canonical["issuer"] == config["issuer"]
    assert canonical["connectors"] == config["connectors"]
    assert canonical["staticClients"][0]["secret"] == "another-test-only-secret"
    assert canonical["staticClients"][0]["redirectURIs"] == [
        "https://headlamp.re8ch.com/oidc-callback",
        "https://headlamp.service.re8ch.com/oidc-callback",
    ]
    assert derived["issuer"] == "https://dex.service.re8ch.com"
    assert derived["connectors"][0]["config"]["redirectURI"] == "https://dex.service.re8ch.com/callback"
    assert derived["connectors"][0]["config"]["clientSecret"] == "test-only-secret"
    assert derived["staticClients"][0]["secret"] == "another-test-only-secret"
    assert derived["staticClients"][0]["redirectURIs"] == [
        "https://headlamp.re8ch.com/oidc-callback",
        "https://headlamp.service.re8ch.com/oidc-callback",
    ]
    stalwart = next(item for item in derived["staticClients"] if item["id"] == "stalwart-webui")
    assert stalwart["public"] is True
    assert "secret" not in stalwart
    assert stalwart["redirectURIs"] == [
        "https://id-stalwart.tools.re8ch.com/account/oauth/callback",
        "https://id-stalwart.tools.re8ch.com/admin/oauth/callback",
    ]
    assert any(path.endswith("/deployments/dex-service") for _, path in created)
    assert any(path.endswith("/deployments/dex") for _, path in created)
    created.clear()
    with pytest.raises(SystemExit) as stopped:
        exec(compile(body, "dex-service-reconciler-test", "exec"), scope)
    assert stopped.value.code == 0
    assert created == []

    broker_secret = {"data": {
        "client-id": base64.b64encode(b"broker-client").decode(),
        "client-secret": base64.b64encode(b"broker-secret").decode(),
    }}
    with pytest.raises(SystemExit) as disabled:
        exec(compile(body, "dex-service-reconciler-test", "exec"), scope)
    assert disabled.value.code == 0
    broker_secret["data"]["enabled"] = base64.b64encode(b"true").decode()
    exec(compile(body, "dex-service-reconciler-test", "exec"), scope)
    derived = yaml.safe_load(base64.b64decode(target["data"]["config.yaml"]))
    assert canonical["connectors"] == config["connectors"]
    broker = next(item for item in derived["connectors"] if item.get("id") == "re8ch-auth")
    assert broker["config"]["issuer"] == "https://api.re8ch.com/auth"
    assert broker["config"]["redirectURI"] == "https://dex.service.re8ch.com/callback"
    assert broker["config"]["clientID"] == "broker-client"
    assert broker["config"]["insecureEnableGroups"] is True
    assert any(path.endswith("/deployments/dex-service") for _, path in created)

    identity_secret = {"data": {
        "client-id": base64.b64encode(b"supabase-identity").decode(),
        "client-secret": base64.b64encode(b"test-identity-secret").decode(),
    }}
    exec(compile(body, "dex-service-reconciler-test", "exec"), scope)
    derived = yaml.safe_load(base64.b64decode(target["data"]["config.yaml"]))
    identity_client = next(item for item in derived["staticClients"] if item["id"] == "supabase-identity")
    assert identity_client["allowedConnectors"] == ["github-re8ch-identity"]
    assert identity_client["redirectURIs"] == ["https://auth.re8ch.com/auth/v1/callback"]
    restricted = next(item for item in derived["connectors"] if item.get("id") == "github-re8ch-identity")
    assert restricted["config"]["orgs"] == [{"name": "re8ch"}]
    assert restricted["config"]["clientSecret"] == "test-only-secret"


def test_new_hostname_targets_only_parallel_dex_service():
    routes = list(yaml.safe_load_all((ROOT / "clusters/re8ch/components/cilium/service-zone-wave-15.yaml").read_text()))
    dex_routes = [item for item in routes if item and item.get("spec", {}).get("hostnames") == ["dex.service.re8ch.com"]]
    backends = [backend["name"] for item in dex_routes for rule in item["spec"]["rules"]
                for backend in rule.get("backendRefs", [])]
    assert backends == ["dex-service", "dex-service"]
    old = list(yaml.safe_load_all((ROOT / "clusters/re8ch/components/auth/dex/dex-ha-deployment.yaml").read_text()))
    assert next(item for item in old if item["kind"] == "Deployment")["metadata"]["name"] == "dex"
    new = list(yaml.safe_load_all((ROOT / "clusters/re8ch/components/auth/dex/dex-service-deployment.yaml").read_text()))
    assert next(item for item in new if item["kind"] == "Deployment")["metadata"]["name"] == "dex-service"
