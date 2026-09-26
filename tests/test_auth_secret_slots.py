from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
AUTH_PATH = "secrets/re8ch-auth-broker-client-auth.sops.yaml"
DEX_PATH = "secrets/re8ch-auth-broker-client-dex.sops.yaml"


def test_broker_client_secrets_are_encrypted_and_connector_stays_disabled():
    kustomization = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    for path in (AUTH_PATH, DEX_PATH):
        assert path in kustomization["resources"]

    auth = yaml.safe_load((ROOT / "clusters/re8ch" / AUTH_PATH).read_text())
    dex = yaml.safe_load((ROOT / "clusters/re8ch" / DEX_PATH).read_text())
    assert (auth["metadata"]["namespace"], auth["metadata"]["name"]) == (
        "auth", "re8ch-auth-broker-client"
    )
    assert (dex["metadata"]["namespace"], dex["metadata"]["name"]) == (
        "dex", "re8ch-auth-broker-oidc"
    )
    assert set(auth["stringData"]) == {
        "AUTH_OIDC_CLIENT_ID", "AUTH_OIDC_CLIENT_SECRET", "AUTH_OIDC_SIGNING_KEY_PEM"
    }
    assert set(dex["stringData"]) == {"client-id", "client-secret"}
    for secret in (auth, dex):
        assert secret["sops"]["encrypted_regex"] == "^(data|stringData)$"
        assert all(value.startswith("ENC[") for value in secret["stringData"].values())


def test_gateway_reads_dedicated_broker_secret():
    deployment = yaml.safe_load((ROOT / "clusters/re8ch/components/auth/deployments/re8ch-auth-gateway.yaml").read_text())
    env = {item["name"]: item for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["AUTH_OIDC_CLIENT_ID"]["value"] == "stalwart-idp"
    assert env["AUTH_OIDC_PUBLIC_CLIENT_IDS"]["value"] == (
        "re8ch-cloud-platform-tool,re8ch-platform-admin,re8ch-tenant"
    )
    assert "AUTH_OIDC_CLIENT_SECRET" not in env
    assert env["AUTH_OIDC_SIGNING_KEY_PEM"]["valueFrom"]["secretKeyRef"] == {
        "name": "re8ch-auth-broker-client", "key": "AUTH_OIDC_SIGNING_KEY_PEM"
    }


def test_dex_github_credentials_are_migrated_without_exposing_values():
    path = ROOT / "clusters/re8ch/components/auth/migrate-dex-github-credentials.yaml"
    documents = list(yaml.safe_load_all(path.read_text()))
    by_kind = {document["kind"]: document for document in documents}

    role = by_kind["Role"]
    assert role["metadata"] == {"name": "migrate-dex-github-credentials", "namespace": "auth"}
    assert role["rules"] == [
        {
            "apiGroups": [""],
            "resources": ["secrets"],
            "resourceNames": ["re8ch-auth-gateway-secrets"],
            "verbs": ["get", "patch"],
        },
        {
            "apiGroups": ["apps"],
            "resources": ["deployments"],
            "resourceNames": ["re8ch-auth-gateway"],
            "verbs": ["get", "patch"],
        },
    ]

    job = by_kind["Job"]
    pod_spec = job["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "migrate-dex-github-credentials"
    assert "nodeSelector" not in pod_spec
    assert pod_spec["volumes"][0]["secret"] == {
        "secretName": "dex-github",
        "items": [
            {"key": "client-id", "path": "client-id"},
            {"key": "client-secret", "path": "client-secret"},
        ],
    }
    script = pod_spec["containers"][0]["args"][0]
    assert pod_spec["containers"][0]["command"] == ["python3", "-c"]
    assert 'open("/source/client-id", "rb")' in script
    assert 'open("/source/client-secret", "rb")' in script
    assert '/data/GITHUB_OAUTH_CLIENT_ID' in script
    assert '/data/GITHUB_OAUTH_CLIENT_SECRET' in script
    assert '/apis/apps/v1/namespaces/auth/deployments/re8ch-auth-gateway' in script
