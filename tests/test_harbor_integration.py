import importlib.util
import io
import json
import tempfile
import urllib.error
from pathlib import Path

import pytest
import yaml


MODULE = Path(__file__).parents[1] / "clusters/re8ch/components/ai-operations/harbor_integration.py"
spec = importlib.util.spec_from_file_location("harbor_integration", MODULE)
harbor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harbor)


class Response:
    def __init__(self, value, status=200):
        self.status = status
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.value).encode() if self.value is not None else b""


def test_pagination_and_sensitive_fields_are_redacted():
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if "page=1" in request.full_url:
            return Response([{"id": index, "token": "secret"} for index in range(100)])
        return Response([{"id": 101, "password": "secret"}])

    client = harbor.HarborClient("https://registry.example", "admin", "pw", opener)
    users = client.users()
    assert len(users) == 101
    assert users[0]["token"] == "[REDACTED]"
    assert users[-1]["password"] == "[REDACTED]"
    assert len(calls) == 2


def test_harbor_http_error_is_normalized_without_body_leak():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, io.BytesIO(b'{"errors":[{"message":"denied"}]}'))

    client = harbor.HarborClient("https://registry.example", "admin", "pw", opener)
    with pytest.raises(harbor.HarborError, match="denied") as error:
        client.projects()
    assert error.value.status == 403


def test_catalog_defaults_and_qwen_override():
    assert harbor.desired_tenant("ledger") == {"tenant": "ledger", "project": "ledger", "username": "ledger", "email": "ledger@re8ch.com", "role": "projectAdmin"}
    qwen = harbor.desired_tenant("qwen", {"harbor": {"project": "qianwen", "webUsername": "tianruoyou2022", "accountEmail": "85153954@qq.com"}})
    assert qwen["project"] == "qianwen"
    assert qwen["username"] == "tianruoyou2022"


def test_credential_reference_rejects_cross_tenant():
    store = harbor.KubernetesCredentialStore("credential-broker")
    reference = "credential://harbor/qwen/" + "a" * 32
    with pytest.raises(PermissionError, match="cross-tenant"):
        store.get(reference, "ledger")


def test_redemption_is_short_lived_and_single_use():
    with tempfile.TemporaryDirectory() as directory:
        registry = harbor.RedemptionRegistry(Path(directory) / "broker.db", "https://tools.example", ttl_seconds=600)
        issued = registry.issue("credential://harbor/qwen/" + "b" * 32, "qwen")
        token = issued["redemption_url"].split("#", 1)[1]
        assert registry.consume(token) == ("credential://harbor/qwen/" + "b" * 32, "qwen")
        assert registry.consume(token) is None


def test_adoption_is_short_lived_and_single_use():
    with tempfile.TemporaryDirectory() as directory:
        registry = harbor.RedemptionRegistry(Path(directory) / "broker.db", "https://tools.example", ttl_seconds=600)
        issued = registry.issue_adoption("qwen")
        token = issued["adoption_url"].split("#", 1)[1]
        assert issued["adoption_url"].startswith("https://tools.example/credentials/adopt#")
        assert registry.consume_adoption(token) == "qwen"
        assert registry.consume_adoption(token) is None


def test_robot_templates_reject_unknown_permissions():
    client = harbor.HarborClient("https://registry.example", "admin", "pw", lambda *_a, **_k: Response({}))
    with pytest.raises(ValueError, match="permission"):
        client.create_robot("qwen", "bad", "system-admin", 30)


def test_harbor_catalog_covers_every_product_catalog_with_qwen_canonical_name():
    root = Path(__file__).parents[1]
    configured = json.loads((root / "clusters/re8ch/components/ai-operations/harbor-tenants.json").read_text())["tenants"]
    catalogs = {path.name for path in (root / "catalogs").iterdir() if path.is_dir() and path.name != "_shared"}
    catalogs.remove("qianwen")
    catalogs.add("qwen")
    assert set(configured) == catalogs
    assert configured["qwen"]["harbor"]["webUsername"] == "tianruoyou2022"


def test_broker_manifest_is_digest_pinned_and_admin_secret_is_runtime_only():
    root = Path(__file__).parents[1]
    documents = list(yaml.safe_load_all((root / "clusters/re8ch/components/ai-operations/platform.yaml").read_text()))
    deployments = {item["metadata"]["name"]: item for item in documents if item and item.get("kind") == "Deployment"}
    broker = deployments["harbor-credential-broker"]
    assert "@sha256:" in broker["spec"]["template"]["spec"]["containers"][0]["image"]
    assert broker["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"] is True
    infra_env = deployments["cluster-infra-tool"]["spec"]["template"]["spec"]["containers"][0]["env"]
    password = next(item for item in infra_env if item["name"] == "HARBOR_ADMIN_PASSWORD")
    assert password["valueFrom"]["secretKeyRef"] == {"name": "cluster-infra-harbor", "key": "password", "optional": True}
    infra_spec = deployments["cluster-infra-tool"]["spec"]["template"]["spec"]
    assert infra_spec["imagePullSecrets"] == [{"name": "harbor-registry-pull"}]
    runtime = next(item for item in infra_spec["volumes"] if item["name"] == "runtime")
    assert runtime["secret"]["secretName"] == "cloud-platform-runtime"
    pvc = next(item for item in documents if item and item.get("kind") == "PersistentVolumeClaim" and item["metadata"]["name"] == "credential-broker-registry")
    assert pvc["spec"]["storageClassName"] == "rbd-r640"
