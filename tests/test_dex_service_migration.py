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

    def request(path, method="GET", body=None, content_type="application/json"):
        nonlocal target
        if method == "GET" and path.endswith("/dex-config"):
            return copy.deepcopy(old)
        if method == "GET" and path.endswith("/dex-config-service"):
            if target is None:
                raise urllib.error.HTTPError(path, 404, "missing", {}, None)
            return copy.deepcopy(target)
        created.append((method, path))
        if method == "PATCH" and path.endswith("/dex-config"):
            old["data"].update(body["data"])
        if method == "POST" and path.endswith("/secrets"):
            target = body
        return {}

    scope = dict(base64=base64, yaml=yaml, time=time, urllib=urllib, request=request)
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
    assert any(path.endswith("/deployments/dex-service") for _, path in created)
    assert any(path.endswith("/deployments/dex") for _, path in created)
    created.clear()
    with pytest.raises(SystemExit) as stopped:
        exec(compile(body, "dex-service-reconciler-test", "exec"), scope)
    assert stopped.value.code == 0
    assert created == []


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
