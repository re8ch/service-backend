#!/usr/bin/env python3
"""Live Harbor/Grafana tenant acceptance. Never prints credentials or tokens."""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


class AcceptanceError(RuntimeError):
    pass


def request(url, *, method="GET", payload=None, headers=None, timeout=15):
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(url, data=body, method=method, headers={"Accept": "application/json", **(headers or {})})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return response.status, dict(response.headers), json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), None
    except urllib.error.URLError as exc:
        raise AcceptanceError(f"endpoint unavailable: {urllib.parse.urlparse(url).netloc}") from exc


def require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def authoritative_route_preflight(hostname, path, expected_status, *, accept="application/json"):
    require(shutil.which("dig") and shutil.which("curl"), "dig and curl are required for authoritative route checks")

    def dig(*arguments):
        for attempt in range(3):
            try:
                result = subprocess.run(["dig", "+short", *arguments], capture_output=True, text=True, timeout=8)
            except subprocess.TimeoutExpired:
                result = None
            if result and result.returncode == 0:
                answers = [line.strip().rstrip(".") for line in result.stdout.splitlines() if line.strip()]
                if answers:
                    return answers
            if attempt < 2:
                time.sleep(1)
        raise AcceptanceError(f"DNS query failed for {hostname} after 3 attempts")

    zone = ".".join(hostname.split(".")[-3:])
    nameservers = dig("NS", zone)
    require(nameservers, f"no authoritative nameservers for {zone}")
    for nameserver in nameservers:
        addresses = []
        for candidate in dig("@" + nameserver, hostname, "A"):
            try:
                ipaddress.IPv4Address(candidate)
            except ipaddress.AddressValueError:
                continue
            addresses.append(candidate)
        require(addresses, f"{nameserver} returned no A address for {hostname}")
        for address in addresses:
            result = subprocess.run([
                "curl", "--silent", "--show-error", "--output", os.devnull,
                "--write-out", "%{http_code}", "--max-time", "10",
                "--resolve", f"{hostname}:443:{address}", "--header", f"Accept: {accept}",
                f"https://{hostname}{path}",
            ], capture_output=True, text=True, timeout=12)
            require(result.returncode == 0 and result.stdout.strip() == str(expected_status),
                    f"{nameserver} directs {hostname} to unavailable {address} (HTTP {result.stdout.strip() or '000'})")
        print(f"PASS {hostname} via {nameserver}")


def preflight(harbor_url, grafana_url, identity_url, mcp_url):
    mcp = urllib.parse.urlparse(mcp_url)
    grafana = urllib.parse.urlparse(grafana_url)
    native = mcp.path.endswith("/tenant-native/mcp")
    authoritative_route_preflight(mcp.hostname, mcp.path, 405 if native else 401,
                                  accept="application/json, text/event-stream")
    authoritative_route_preflight(grafana.hostname, grafana.path + "/api/health", 200)
    checks = [
        ("Harbor db_auth", harbor_url + "/api/v2.0/systeminfo", lambda status, data: status == 200 and data.get("auth_mode") == "db_auth"),
        ("Harbor registry challenge", harbor_url + "/v2/", lambda status, _data: status == 401),
        ("Grafana database", grafana_url + "/api/health", lambda status, data: status == 200 and data.get("database") == "ok"),
        ("Supabase identity PostgREST", identity_url + "/", lambda status, data: status == 200 and data.get("swagger") == "2.0"),
    ]
    for label, url, accepts in checks:
        headers = {"Accept": "application/json, text/event-stream"} if label == "Tenant MCP auth challenge" else None
        status, _, data = request(url, headers=headers)
        require(accepts(status, data or {}), f"{label} failed (HTTP {status})")
        print(f"PASS {label}")
    status, challenge_headers, _ = request(mcp_url, method="POST", payload={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                           headers={"Accept": "application/json, text/event-stream"})
    require(status == 401, f"Tenant MCP auth challenge failed (HTTP {status})")
    if native:
        metadata_url = f"{mcp.scheme}://{mcp.netloc}/.well-known/oauth-protected-resource{mcp.path}"
        challenge = challenge_headers.get("www-authenticate") or challenge_headers.get("WWW-Authenticate") or ""
        require('realm="https://db-rw.re8ch.com/auth/v1"' in challenge and f'resource_metadata="{metadata_url}"' in challenge,
                "native tenant MCP advertises an incorrect OAuth challenge")
        metadata_status, _, metadata = request(metadata_url)
        require(metadata_status == 200 and metadata.get("resource") == mcp_url and
                metadata.get("authorization_servers") == ["https://db-rw.re8ch.com/auth/v1"],
                "native tenant MCP advertises an incorrect protected resource")
        print("PASS native tenant Supabase OAuth discovery")
    print("PASS Tenant MCP auth challenge")


class MCP:
    def __init__(self, url, token, tenant):
        self.url = url
        self.headers = {"Authorization": f"Bearer {token}", "X-RE8CH-Tenant": tenant, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.session = None
        self.next_id = 0
        self.tool_names = None
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "re8ch-tenant-provider-acceptance", "version": "1"}})
        self.call("notifications/initialized", {}, notification=True)

    def call(self, method, params, notification=False, timeout=30):
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.next_id
        headers = dict(self.headers)
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        body = json.dumps(payload, separators=(",", ":")).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                raw = response.read().decode()
        except urllib.error.HTTPError as exc:
            raise AcceptanceError(f"tenant MCP {method} returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise AcceptanceError(f"tenant MCP {method} endpoint unavailable") from exc
        if notification:
            return None
        if raw.startswith("data:") or "\ndata:" in raw:
            messages = [json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith("data:") and line[5:].strip() != "[DONE]"]
            require(messages, f"tenant MCP {method} returned no JSON-RPC data")
            result = messages[-1]
        else:
            result = json.loads(raw)
        require("error" not in result, f"tenant MCP {method} returned a JSON-RPC error")
        return result.get("result")

    def tool(self, name, arguments):
        if self.tool_names is None:
            listed = self.call("tools/list", {})
            self.tool_names = {item["name"] for item in listed.get("tools", [])}
        actual = name if name in self.tool_names else f"tenant-api-backend_{name}"
        require(actual in self.tool_names, f"{name} is not published by tenant vMCP")
        result = self.call("tools/call", {"name": actual, "arguments": arguments})
        require(isinstance(result, dict) and not result.get("isError"), f"{name} failed")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for item in result.get("content") or []:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except ValueError:
                    pass
        raise AcceptanceError(f"{name} returned no structured result")


def await_phase(mcp, resource_ref, expected, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = mcp.tool("service_instance_get", {"resource_ref": resource_ref})
        phase = (value.get("status") or {}).get("phase")
        if phase == expected:
            return value
        if phase == "Failed":
            raise AcceptanceError(f"{resource_ref} reconciler failed: {(value.get('status') or {}).get('reason', 'unknown')}")
        time.sleep(5)
    raise AcceptanceError(f"{resource_ref} did not reach {expected} within {timeout}s")


def redeem(mcp, tenant, reference):
    issued = mcp.tool("tenant_human_access_issue", {"credential_ref": reference})
    link = (issued.get("credential") or {}).get("redemption_url")
    require(link and "#" in link, "one-time credential link missing")
    url, token = link.split("#", 1)
    status, _, material = request(url, method="POST", payload={"token": token})
    require(status == 200 and material.get("tenant") == tenant and material.get("username") and material.get("password"), "one-time credential redemption failed")
    return material


def native_identity(url, username, password, path):
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    return request(url + path, headers={"Authorization": "Basic " + auth})


def grafana_web_login(url, username, password, org_id):
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    payload = json.dumps({"user": username, "password": password}).encode()
    login = urllib.request.Request(url + "/login", data=payload, method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with opener.open(login, timeout=15) as response:
            require(response.status == 200, f"Grafana Web login returned HTTP {response.status}")
        require(bool(list(cookies)), "Grafana Web login did not issue a session")
        with opener.open(url + "/api/user/orgs", timeout=15) as response:
            orgs = json.load(response)
        require(any(int(item.get("orgId") or 0) == int(org_id) for item in orgs), "Grafana Web session lacks tenant Org")
    except urllib.error.HTTPError as exc:
        raise AcceptanceError(f"Grafana Web session returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise AcceptanceError("Grafana Web session is unavailable") from exc


def harbor_cli_round_trip(harbor_url, project, username, password, suffix):
    require(shutil.which("docker"), "Docker CLI is required for Harbor push/pull acceptance")
    registry = urllib.parse.urlparse(harbor_url).netloc
    require(registry and "/" not in project, "invalid Harbor registry or project")
    image_ref = f"{registry}/{project}/tenant-acceptance:{suffix}"
    with tempfile.TemporaryDirectory(prefix="tenant-harbor-acceptance-") as directory:
        config = os.path.join(directory, "docker-config")
        context = os.path.join(directory, "context")
        os.mkdir(config, 0o700)
        os.mkdir(context)
        source_config = os.path.join(os.environ.get("DOCKER_CONFIG", os.path.expanduser("~/.docker")), "config.json")
        source_settings = {}
        if os.path.isfile(source_config):
            with open(source_config, encoding="utf-8") as stream:
                source_settings = json.load(stream)
        plugin_dirs = [path for path in source_settings.get("cliPluginsExtraDirs", []) if os.path.isdir(path)]
        default_plugins = os.path.join(os.path.dirname(source_config), "cli-plugins")
        if os.path.isdir(default_plugins):
            plugin_dirs.append(default_plugins)
        isolated_settings = {"cliPluginsExtraDirs": sorted(set(plugin_dirs))}
        with open(os.path.join(config, "config.json"), "w", encoding="utf-8") as stream:
            json.dump(isolated_settings, stream)
        with open(os.path.join(context, "Dockerfile"), "w", encoding="utf-8") as stream:
            stream.write(f"FROM scratch\nLABEL re8ch.acceptance={suffix}\nCOPY marker /marker\n")
        with open(os.path.join(context, "marker"), "w", encoding="utf-8") as stream:
            stream.write("tenant registry acceptance\n")
        environment = {**os.environ, "DOCKER_CONFIG": config}
        environment.setdefault("DOCKER_CONTEXT", "default")

        def run(stage, argv, *, input_data=None):
            result = subprocess.run(argv, input=input_data, text=True, capture_output=True, env=environment, timeout=180)
            detail = (result.stderr or result.stdout).replace(password, "<redacted>").strip()[-500:]
            require(result.returncode == 0, f"Harbor Docker {stage} failed (exit {result.returncode}): {detail}")
            return result.stdout

        try:
            run("login", ["docker", "login", registry, "--username", username, "--password-stdin"], input_data=password + "\n")
            run("build", ["docker", "build", "--platform", "linux/amd64", "-t", image_ref, context])
            run("push", ["docker", "push", image_ref])
            run("remove local image", ["docker", "image", "rm", image_ref])
            run("pull", ["docker", "pull", "--platform", "linux/amd64", image_ref])
            label = run("inspect", ["docker", "image", "inspect", image_ref, "--format", '{{index .Config.Labels "re8ch.acceptance"}}']).strip()
            require(label == suffix, "Harbor Docker pull returned the wrong artifact")
        finally:
            try:
                subprocess.run(["docker", "image", "rm", "-f", image_ref], capture_output=True, text=True, env=environment, timeout=30)
            except subprocess.TimeoutExpired:
                pass
    return image_ref


def require_identity_config(identity_url, user_token, tenant, service_class, name, phase):
    status, _, rows = request(identity_url + "/rpc/my_tenant_service_configs", method="POST", payload={}, headers={"Authorization": "Bearer " + user_token})
    require(status == 200 and isinstance(rows, list), f"Supabase user config read failed (HTTP {status})")
    matches = [row for row in rows if row.get("tenant_ref") == tenant and row.get("service_class") == service_class and row.get("instance_name") == name]
    require(len(matches) == 1 and matches[0].get("observed_phase") == phase, f"Supabase user config missing {service_class} {phase}")
    require(not any(key in matches[0] for key in ("credential_ref", "provider_user_id", "identity_user_id")), "Supabase user config exposes private identity fields")
    return matches[0]


def require_revoked(provider, base, material, path, grafana_org_id, lifecycle):
    status, _, data = native_identity(base, material["username"], material["password"], path)
    if provider == "harbor":
        require(status in {401, 403, 404}, f"Harbor {lifecycle} account still accesses private project (HTTP {status})")
    else:
        require(status in {401, 403} or (status == 200 and not any(
            int(item.get("orgId") or 0) == int(grafana_org_id) for item in data or []
        )), f"Grafana {lifecycle} account still belongs to tenant Org (HTTP {status})")


def full_acceptance(args):
    require(os.getenv("ALLOW_LIVE_MUTATION") == "1", "set ALLOW_LIVE_MUTATION=1 to run live create/suspend/delete")
    needed = ("TENANT_ACCESS_TOKEN", "IDENTITY_SERVICE_KEY", "IDENTITY_USER_TOKEN", "IDENTITY_USER_REF", "TENANT_REF", "HARBOR_PROJECT", "GRAFANA_DATASOURCE_UID", "GRAFANA_ORG_ID")
    env = {key: os.getenv(key, "") for key in needed}
    require(all(env.values()), "missing live acceptance inputs: " + ", ".join(key for key in needed if not env[key]))
    tenant, identity_user = env["TENANT_REF"], env["IDENTITY_USER_REF"]
    if not args.skip_preflight:
        preflight(args.harbor_url, args.grafana_url, args.identity_url, args.mcp_url)
    status, _, members = request(args.identity_url + "/rpc/resolve_tenant_service_user", method="POST", payload={"p_tenant_ref": tenant, "p_user_id": identity_user}, headers={"Authorization": "Bearer " + env["IDENTITY_SERVICE_KEY"]})
    require(status == 200 and isinstance(members, list) and len(members) == 1 and members[0].get("user_id") == identity_user, "Supabase active tenant member RPC failed")
    print("PASS Supabase tenant member binding")
    mcp = MCP(args.mcp_url, env["TENANT_ACCESS_TOKEN"], tenant)
    suffix = args.resume_suffix or secrets.token_hex(4)
    services = [
        ("harbor", "registry.harbor.v1", "build-push", {"project": env["HARBOR_PROJECT"], "quotaGiB": 1, "identityUserRef": identity_user}),
        ("grafana", "observability.grafana.v1", "viewer", {"datasourceRefs": [env["GRAFANA_DATASOURCE_UID"]], "folderRefs": [], "humanLogin": f"{tenant}-acceptance-{suffix}", "identityUserRef": identity_user}),
    ]
    for provider, service_class, plan, parameters in services:
        name = f"acceptance-{provider}-{suffix}"
        spec = {"serviceClass": service_class, "planRef": plan, "parameters": parameters}
        if args.resume_provider == provider:
            reference = f"service://{tenant}/{name}"
            print(f"RESUMED {provider} {reference}")
        else:
            mcp.tool("service_instance_plan", {"name": name, "spec": spec})
            created = mcp.tool("service_instance_create", {"name": name, "spec": spec, "idempotency_key": f"acceptance-{suffix}-{provider}"})
            reference = created.get("resourceRef") or f"service://{tenant}/{name}"
            print(f"CREATED {provider} {reference}")
        value = await_phase(mcp, reference, "Ready")
        require_identity_config(args.identity_url, env["IDENTITY_USER_TOKEN"], tenant, service_class, name, "Ready")
        print(f"PASS {provider} Supabase user config")
        access = (value.get("status") or {}).get("access") or {}
        material = redeem(mcp, tenant, access.get("humanCredentialRef") or "")
        base = args.harbor_url if provider == "harbor" else args.grafana_url
        path = "/api/v2.0/projects/" + urllib.parse.quote(env["HARBOR_PROJECT"], safe="") if provider == "harbor" else "/api/user/orgs"
        status, _, data = native_identity(base, material["username"], material["password"], path)
        require(status == 200, f"{provider} native human login failed (HTTP {status})")
        if provider == "grafana":
            require(any(int(item.get("orgId") or 0) == int(env["GRAFANA_ORG_ID"]) for item in data or []), "Grafana login lacks tenant Org")
            grafana_web_login(base, material["username"], material["password"], env["GRAFANA_ORG_ID"])
            print("PASS Grafana Web session")
        else:
            artifact_ref = harbor_cli_round_trip(base, env["HARBOR_PROJECT"], material["username"], material["password"], suffix)
            print(f"PASS Harbor Docker push/pull {artifact_ref}")
        print(f"PASS {provider} native human access")
        mcp.tool("service_instance_suspend", {"resource_ref": reference, "idempotency_key": f"suspend-{suffix}-{provider}"})
        await_phase(mcp, reference, "Suspended")
        require_identity_config(args.identity_url, env["IDENTITY_USER_TOKEN"], tenant, service_class, name, "Suspended")
        require_revoked(provider, base, material, path, env["GRAFANA_ORG_ID"], "suspended")
        print(f"PASS {provider} suspension revokes native access")
        mcp.tool("service_instance_resume", {"resource_ref": reference, "idempotency_key": f"resume-{suffix}-{provider}"})
        await_phase(mcp, reference, "Ready")
        require_identity_config(args.identity_url, env["IDENTITY_USER_TOKEN"], tenant, service_class, name, "Ready")
        status, _, _ = native_identity(base, material["username"], material["password"], path)
        require(status == 200, f"{provider} native access did not recover")
        print(f"PASS {provider} resume")
        mcp.tool("service_instance_delete", {"resource_ref": reference, "idempotency_key": f"delete-{suffix}-{provider}"})
        await_phase(mcp, reference, "Deleted")
        require_identity_config(args.identity_url, env["IDENTITY_USER_TOKEN"], tenant, service_class, name, "Deleted")
        require_revoked(provider, base, material, path, env["GRAFANA_ORG_ID"], "deleted")
        print(f"PASS {provider} deletion revokes native access")
        replacement_name = f"{name}-again"
        mcp.tool("service_instance_plan", {"name": replacement_name, "spec": spec})
        replacement = mcp.tool("service_instance_create", {"name": replacement_name, "spec": spec, "idempotency_key": f"recreate-{suffix}-{provider}"})
        replacement_ref = replacement.get("resourceRef") or f"service://{tenant}/{replacement_name}"
        print(f"CREATED {provider} replacement {replacement_ref}")
        value = await_phase(mcp, replacement_ref, "Ready")
        require_identity_config(args.identity_url, env["IDENTITY_USER_TOKEN"], tenant, service_class, replacement_name, "Ready")
        replacement_material = redeem(mcp, tenant, ((value.get("status") or {}).get("access") or {}).get("humanCredentialRef") or "")
        require_revoked(provider, base, material, path, env["GRAFANA_ORG_ID"], "replaced")
        status, _, _ = native_identity(base, replacement_material["username"], replacement_material["password"], path)
        require(status == 200, f"{provider} recreated native account cannot access service (HTTP {status})")
        if provider == "grafana":
            grafana_web_login(base, replacement_material["username"], replacement_material["password"], env["GRAFANA_ORG_ID"])
        mcp.tool("service_instance_delete", {"resource_ref": replacement_ref, "idempotency_key": f"recreate-delete-{suffix}-{provider}"})
        await_phase(mcp, replacement_ref, "Deleted")
        require_revoked(provider, base, replacement_material, path, env["GRAFANA_ORG_ID"], "recreated and deleted")
        print(f"PASS {provider} recreate and delete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="read-only public endpoint checks")
    parser.add_argument("--resume-suffix", default="", help="resume a previous test suffix")
    parser.add_argument("--resume-provider", choices=("harbor", "grafana"), default="")
    parser.add_argument("--skip-preflight", action="store_true", help="resume a prior run whose preflight passed")
    parser.add_argument("--harbor-url", default="https://registry.re8ch.com")
    parser.add_argument("--grafana-url", default="https://grafana.service.re8ch.com/grafana")
    parser.add_argument("--identity-url", default="https://db-rw.re8ch.com/identity/rest/v1")
    parser.add_argument("--mcp-url", default="https://tools.service.re8ch.com/tenant-native/mcp")
    args = parser.parse_args()
    try:
        require(not args.resume_provider or args.resume_suffix, "--resume-provider requires --resume-suffix")
        require(not args.skip_preflight or args.resume_provider, "--skip-preflight requires a resumed provider")
        if args.preflight:
            preflight(args.harbor_url, args.grafana_url, args.identity_url, args.mcp_url)
        else:
            full_acceptance(args)
    except AcceptanceError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
