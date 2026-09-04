"""Tenant-safe client for the isolated BuildKit and OCI supply-chain worker."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable


class SupplyChainError(RuntimeError):
    pass


class SupplyChainClient:
    """Calls a worker API; registry credentials remain behind opaque references."""

    def __init__(self, base_url: str, service_token: str,
                 opener: Callable[..., Any] = urllib.request.urlopen):
        if not base_url.startswith(("http://", "https://")):
            raise SupplyChainError("supply-chain worker URL is unavailable")
        if not service_token:
            raise SupplyChainError("supply-chain service credential is unavailable")
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.opener = opener

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        raw = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(self.base_url + path, data=raw, method=method, headers={
            "Authorization": f"Bearer {self.service_token}", "Accept": "application/json",
            "Content-Type": "application/json",
        })
        try:
            with self.opener(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise SupplyChainError(f"supply-chain worker returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise SupplyChainError("supply-chain worker is unreachable") from exc
        return json.loads(body) if body else {}

    @staticmethod
    def validate_image(project: str, repository: str, tag: str) -> str:
        name = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$")
        if not name.fullmatch(repository) or not re.fullmatch(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", tag):
            raise ValueError("invalid OCI repository or tag")
        return f"{project}/{repository}:{tag}"

    def start_build(self, tenant: str, project: str, spec: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        repository, tag = str(spec.get("repository", "")), str(spec.get("tag", ""))
        target = self.validate_image(project, repository, tag)
        dockerfile = str(spec.get("dockerfile", "Dockerfile"))
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,256}", dockerfile) or ".." in dockerfile.split("/"):
            raise ValueError("invalid Dockerfile path")
        context = str(spec.get("context", ""))
        if not context.startswith("https://"):
            raise ValueError("build context must be an HTTPS source reference")
        payload = {
            "tenant": tenant, "project": project, "target": target, "context": context,
            "revision": str(spec.get("revision", "")), "dockerfile": dockerfile,
            "platforms": spec.get("platforms") or ["linux/amd64"],
            "buildArgs": spec.get("build_args") or {}, "credentialRef": spec.get("credential_ref"),
            "attestations": {"sbom": True, "provenance": "mode=max"},
            "sign": bool(spec.get("sign", True)), "scan": bool(spec.get("scan", True)),
            "idempotencyKey": idempotency_key,
        }
        return self.request("POST", "/v1/builds", payload)

    def get_build(self, tenant: str, operation_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/tenants/{tenant}/builds/{operation_id}")

    def list_builds(self, tenant: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/tenants/{tenant}/builds")

    def cancel_build(self, tenant: str, operation_id: str) -> dict[str, Any]:
        return self.request("POST", f"/v1/tenants/{tenant}/builds/{operation_id}/cancel", {})
