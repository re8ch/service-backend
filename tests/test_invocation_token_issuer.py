import base64
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import hashes

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "clusters/re8ch/components/ai-operations/invocation_token_issuer.py"
SPEC = importlib.util.spec_from_file_location("invocation_token_issuer_under_test", PATH)
issuer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issuer)


def key_entry(kid):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return key, {"kid": kid, "privateKeyPem": pem}


def fixture(tmp_path, monkeypatch, kids=("new", "old")):
    pairs = [key_entry(kid) for kid in kids]
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"currentKid": kids[0], "keys": [entry for _, entry in pairs]}))
    def request(url, **kwargs):
        if url == "user": return {"id": "00000000-0000-0000-0000-000000000001"}
        if url == "contexts": return [{"tenant_ref": "consumer", "is_active": True}]
        if url == "binding":
            return {"bindingRef": "tool://binding/one", "toolRef": "tool://publisher/search",
                    "activeOrgRef": "consumer", "ownerOrgRef": "publisher",
                    "providerAudience": "https://provider.example", "allowedScopes": ["tools:invoke"]}
        raise AssertionError(url)
    monkeypatch.setattr(issuer, "_json_request", request)
    authority = issuer.Issuer(issuer="https://tools.example/invocation", key_ring=issuer.KeyRing(path),
        identity_user_url="user", identity_contexts_url="contexts", binding_authority_url="binding",
        binding_authority_token="service-secret", now=lambda: 1000)
    return authority, {entry["kid"]: key for key, entry in pairs}, path


def request(**overrides):
    value = {"subjectToken": "real-user-token", "subject": "caller-assertion",
             "activeOrgRef": "attacker", "audience": "attacker-audience",
             "toolRef": "tool://publisher/search", "bindingRef": "tool://binding/one",
             "scopes": ["tools:invoke"], "maxTtlSeconds": 9999}
    value.update(overrides)
    return value


def decode(token):
    header, payload, signature = token.split(".")
    pad = lambda value: value + "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(pad(header))), json.loads(base64.urlsafe_b64decode(pad(payload))), base64.urlsafe_b64decode(pad(signature)), (header + "." + payload).encode()


def test_issuer_uses_authorities_and_real_rs256(tmp_path, monkeypatch):
    authority, keys, _ = fixture(tmp_path, monkeypatch)
    result = authority.issue(request())
    header, claims, signature, signed = decode(result["accessToken"])
    assert header == {"alg": "RS256", "kid": "new", "typ": "JWT"}
    assert claims["active_org_ref"] == "consumer" and claims["owner_org_ref"] == "publisher"
    assert claims["aud"] == "https://provider.example" and claims["exp"] - claims["iat"] <= 300
    assert claims["sub"] != "caller-assertion" and claims["jti"]
    keys["new"].public_key().verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())


def test_jwks_rotation_publishes_only_public_material(tmp_path, monkeypatch):
    authority, _, path = fixture(tmp_path, monkeypatch)
    jwks = authority.key_ring.jwks()
    assert {key["kid"] for key in jwks["keys"]} == {"new", "old"}
    assert all(set(key) == {"kty", "use", "alg", "kid", "n", "e"} for key in jwks["keys"])
    raw = json.loads(path.read_text()); raw["currentKid"] = "old"; path.write_text(json.dumps(raw))
    assert decode(authority.issue(request())["accessToken"])[0]["kid"] == "old"


def test_retiring_public_only_key_remains_in_jwks(tmp_path):
    current_key, current = key_entry("current")
    previous_key, _ = key_entry("previous")
    public = previous_key.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"currentKid": "current", "keys": [current,
        {"kid": "previous", "publicKeyPem": public}]}))
    ring = issuer.KeyRing(path)
    assert {key["kid"] for key in ring.jwks()["keys"]} == {"current", "previous"}
    assert ring.load()[0] == "current"


@pytest.mark.parametrize("change", [
    {"toolRef": "tool://attacker/wrong"}, {"bindingRef": "tool://binding/wrong"},
    {"scopes": ["admin:all"]}, {"subjectToken": ""},
])
def test_overrides_and_escalation_rejected(tmp_path, monkeypatch, change):
    authority, _, _ = fixture(tmp_path, monkeypatch)
    with pytest.raises((PermissionError, ValueError)):
        authority.issue(request(**change))


def test_missing_or_malformed_keys_fail_closed(tmp_path, monkeypatch):
    authority, _, path = fixture(tmp_path, monkeypatch)
    path.unlink()
    with pytest.raises(issuer.AuthorityUnavailable): authority.issue(request())
    path.write_text(json.dumps({"currentKid": "none", "keys": []}))
    with pytest.raises(issuer.AuthorityUnavailable): authority.key_ring.jwks()
