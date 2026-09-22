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
    for key in ("AUTH_OIDC_CLIENT_ID", "AUTH_OIDC_CLIENT_SECRET", "AUTH_OIDC_SIGNING_KEY_PEM"):
        assert env[key]["valueFrom"]["secretKeyRef"] == {
            "name": "re8ch-auth-broker-client", "key": key
        }
