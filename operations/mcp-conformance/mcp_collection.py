#!/usr/bin/env python3
"""Run a versioned MCP truth collection without persisting credentials or response bodies."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def jwt_aud(token: str) -> set[str]:
    try:
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        aud = payload.get("aud", [])
        return {aud} if isinstance(aud, str) else set(aud)
    except Exception as exc:
        raise RuntimeError("credential broker returned a malformed JWT") from exc


class Client:
    def __init__(self, endpoint: str, token: str, timeout: int):
        self.endpoint, self.token, self.timeout, self.request_id = endpoint, token, timeout, 0

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.request_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params or {}}).encode()
        request = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "Accept": "application/json", "MCP-Protocol-Version": "2025-06-18",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.load(response)
        except urllib.error.HTTPError as exc:
            raw = exc.read(32768)
            try: value = json.loads(raw)
            except Exception: value = {"httpStatus": exc.code, "error": "non-json response"}
        if ("result" in value) == ("error" in value):
            raise AssertionError(f"{method}: JSON-RPC result/error must be mutually exclusive")
        if "error" in value:
            raise RuntimeError(f"{method}: {value['error'].get('code')} {value['error'].get('message')}")
        return value["result"]


def fetch_json(url: str, timeout: int) -> Any:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
        return json.load(response)


def run(path: Path, output: Path) -> dict[str, Any]:
    collection = json.loads(path.read_text())
    if collection.get("apiVersion") != "testing.re8ch.com/v1alpha1" or collection.get("kind") != "MCPCollection":
        raise ValueError("unsupported MCPCollection")
    spec = collection["spec"]
    variables = {key: os.path.expandvars(str(value)) for key, value in spec.get("variables", {}).items()}
    endpoint = spec["endpoint"].format(**variables)
    token_env = spec["oauth"]["tokenEnv"]
    token = os.getenv(token_env, "")
    if not token:
        raise RuntimeError(f"credential broker environment reference {token_env} is unavailable")
    audience = spec["oauth"]["audience"]
    if audience not in jwt_aud(token):
        raise AssertionError("JWT audience does not contain the protected MCP resource")
    timeout = int(spec.get("timeoutSeconds", 20))
    metadata = fetch_json(spec["oauth"]["protectedResourceMetadata"].format(**variables), timeout)
    if metadata.get("resource") != audience or not metadata.get("authorization_servers"):
        raise AssertionError("protected resource metadata is incomplete or has the wrong resource")
    authorization_server = metadata["authorization_servers"][0].rstrip("/")
    discovery = fetch_json(authorization_server + "/.well-known/openid-configuration", timeout)
    for field in ("authorization_endpoint", "token_endpoint", "code_challenge_methods_supported"):
        if field not in discovery:
            raise AssertionError(f"authorization discovery is missing {field}")
    if "S256" not in discovery["code_challenge_methods_supported"]:
        raise AssertionError("authorization server does not advertise PKCE S256")

    client = Client(endpoint, token, timeout)
    initialized = client.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "re8ch-mcp-collection", "version": "0.1.0"}})
    if initialized.get("protocolVersion") != "2025-06-18":
        raise AssertionError("MCP protocol negotiation failed")
    tools = client.rpc("tools/list").get("tools", [])
    tool_names = {item["name"] for item in tools}
    resources, pages, cursor = [], 0, None
    while True:
        page = client.rpc("resources/list", {"cursor": cursor} if cursor else {})
        pages += 1; resources.extend(page.get("resources", [])); cursor = page.get("nextCursor")
        if not cursor: break
        if pages > 10000: raise AssertionError("resource pagination did not terminate")
    resource_hashes = []
    for item in resources:
        value = client.rpc("resources/read", {"uri": item["uri"]})
        resource_hashes.append({"uri": item["uri"], "responseHash": digest(value)})
    calls = []
    for case in spec.get("calls", []):
        if case["tool"] not in tool_names: raise AssertionError(f"tool is absent: {case['tool']}")
        value = client.rpc("tools/call", {"name": case["tool"], "arguments": case.get("arguments", {})})
        calls.append({"name": case["name"], "tool": case["tool"], "responseHash": digest(value)})
    manifest = {
        "apiVersion": "evidence.re8ch.com/v1alpha1", "kind": "MCPEvidence", "collection": collection["metadata"]["name"],
        "startedAt": int(time.time()), "endpoint": endpoint, "audience": audience,
        "serverIdentity": initialized.get("serverInfo"), "toolCount": len(tools), "toolSchemaHash": digest(tools),
        "resourceCount": len(resources), "resourcePages": pages, "resources": resource_hashes, "calls": calls,
        "imageDigest": os.getenv("MCP_SERVER_IMAGE_DIGEST", "unknown"), "gitRevision": os.getenv("CI_COMMIT_SHA", "unknown"),
        "sensitiveMaterialPersisted": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    cosign_key = os.getenv("MCP_EVIDENCE_COSIGN_KEY")
    if cosign_key:
        subprocess.run(["cosign", "sign-blob", "--yes", "--key", cosign_key, "--bundle", str(output) + ".bundle", str(output)], check=True)
    elif spec.get("requireSignedEvidence", True):
        raise RuntimeError("evidence was written but MCP_EVIDENCE_COSIGN_KEY is required for signing")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("collection", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.collection, args.output)
    print(json.dumps({key: result[key] for key in ("collection", "toolCount", "resourceCount", "resourcePages")}, sort_keys=True))


if __name__ == "__main__": main()
