"""Fail-closed provider invocation token authority.

Private keys are read from a mounted runtime key ring.  Identity and binding
facts are resolved by their owning authorities; request fields are assertions,
never authorization inputs.
"""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

REF = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s]{1,512}$")
SCOPE = re.compile(r"^[a-z][a-z0-9:._/-]{0,127}$")
ORG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


class AuthorityUnavailable(RuntimeError):
    pass


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _json_request(url, *, body=None, headers=None):
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                     headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
        raise AuthorityUnavailable("invocation authority dependency unavailable") from exc


class KeyRing:
    """Atomically reload a JSON key ring so current/retiring kids can overlap."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            raw = json.loads(self.path.read_text())
            current = raw["currentKid"]
            entries = raw["keys"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 3:
                raise ValueError()
            keys = {}
            for entry in entries:
                kid = entry["kid"]
                if not isinstance(kid, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", kid) or kid in keys:
                    raise ValueError()
                key = serialization.load_pem_private_key(entry["privateKeyPem"].encode(), password=None)
                if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
                    raise ValueError()
                keys[kid] = key
            if current not in keys:
                raise ValueError()
            return current, keys
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            raise AuthorityUnavailable("invocation signing key ring unavailable") from exc

    def jwks(self):
        _, keys = self.load()
        result = []
        for kid, key in keys.items():
            numbers = key.public_key().public_numbers()
            result.append({"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                           "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                           "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))})
        return {"keys": result}


class Issuer:
    def __init__(self, *, issuer, key_ring, identity_user_url, identity_contexts_url,
                 binding_authority_url, binding_authority_token, now=time.time):
        self.issuer, self.key_ring, self.identity_user_url = issuer.rstrip("/"), key_ring, identity_user_url
        self.identity_contexts_url, self.binding_authority_url = identity_contexts_url, binding_authority_url
        self.binding_authority_token, self.now = binding_authority_token, now

    def issue(self, request):
        token = request.get("subjectToken")
        if not isinstance(token, str) or not token or len(token) > 16384:
            raise PermissionError("verified subject token required")
        requested_scopes = request.get("scopes")
        if (not isinstance(requested_scopes, list) or not 1 <= len(requested_scopes) <= 16
                or len(set(requested_scopes)) != len(requested_scopes)
                or any(not isinstance(v, str) or not SCOPE.fullmatch(v) for v in requested_scopes)):
            raise ValueError("invalid scopes")
        for name in ("toolRef", "bindingRef"):
            if not isinstance(request.get(name), str) or not REF.fullmatch(request[name]):
                raise ValueError("invalid invocation reference")
        if not all((self.issuer, self.identity_user_url, self.identity_contexts_url,
                    self.binding_authority_url, self.binding_authority_token)):
            raise AuthorityUnavailable("invocation authority is not configured")

        auth = {"Authorization": "Bearer " + token}
        user = _json_request(self.identity_user_url, headers=auth)
        subject = user.get("id") if isinstance(user, dict) else None
        contexts = _json_request(self.identity_contexts_url, body={}, headers=auth)
        active = [row for row in contexts if isinstance(row, dict) and row.get("is_active") is True]
        if not isinstance(subject, str) or len(active) != 1 or not ORG.fullmatch(str(active[0].get("tenant_ref", ""))):
            raise PermissionError("active organization unavailable")
        active_org = active[0]["tenant_ref"]

        decision = _json_request(self.binding_authority_url, body={"p_binding_ref": request["bindingRef"], "p_active_org_ref": active_org},
                                 headers={"Authorization": "Bearer " + self.binding_authority_token})
        if isinstance(decision, list) and len(decision) == 1 and isinstance(decision[0], dict):
            row = decision[0]
            decision = {"bindingRef": row.get("binding_ref"), "toolRef": row.get("tool_ref"),
                        "activeOrgRef": row.get("active_org_ref"), "ownerOrgRef": row.get("owner_org_ref"),
                        "providerAudience": row.get("provider_audience"), "allowedScopes": row.get("allowed_scopes"),
                        "maxTtlSeconds": row.get("max_ttl_seconds")}
        required = ("bindingRef", "toolRef", "activeOrgRef", "ownerOrgRef", "providerAudience", "allowedScopes")
        if not isinstance(decision, dict) or any(key not in decision for key in required):
            raise AuthorityUnavailable("binding authority response invalid")
        if (decision["bindingRef"] != request["bindingRef"] or decision["toolRef"] != request["toolRef"]
                or decision["activeOrgRef"] != active_org or not ORG.fullmatch(str(decision["ownerOrgRef"]))
                or not isinstance(decision["providerAudience"], str) or not decision["providerAudience"]):
            raise PermissionError("binding is not authorized for this invocation")
        allowed = decision["allowedScopes"]
        if not isinstance(allowed, list) or any(scope not in allowed for scope in requested_scopes):
            raise PermissionError("requested scope exceeds binding authority")

        now = int(self.now())
        ttl = min(300, max(1, int(decision.get("maxTtlSeconds", 300))))
        current, keys = self.key_ring.load()
        header = _b64(json.dumps({"alg": "RS256", "kid": current, "typ": "JWT"}, separators=(",", ":")).encode())
        claims = {"iss": self.issuer, "sub": subject, "aud": decision["providerAudience"], "iat": now,
                  "exp": now + ttl, "jti": str(uuid.uuid4()), "active_org_ref": active_org,
                  "owner_org_ref": decision["ownerOrgRef"], "tool_ref": decision["toolRef"],
                  "binding_ref": decision["bindingRef"], "scopes": requested_scopes}
        payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        signing_input = (header + "." + payload).encode()
        signature = keys[current].sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return {"accessToken": header + "." + payload + "." + _b64(signature), "expiresIn": ttl,
                "scopes": requested_scopes, "claims": claims}


def from_environment():
    return Issuer(issuer=os.getenv("INVOCATION_TOKEN_ISSUER", ""),
                  key_ring=KeyRing(os.getenv("INVOCATION_SIGNING_KEY_RING", "/var/run/re8ch-invocation-signing/key-ring.json")),
                  identity_user_url=os.getenv("IDENTITY_USER_URL", ""),
                  identity_contexts_url=os.getenv("IDENTITY_CONTEXTS_URL", ""),
                  binding_authority_url=os.getenv("BINDING_AUTHORITY_URL", ""),
                  binding_authority_token=os.getenv("BINDING_AUTHORITY_TOKEN", ""))
