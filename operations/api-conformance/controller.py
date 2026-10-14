#!/usr/bin/env python3
"""GitOps target scheduler, discovery notifier, and repository result publisher."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from contract import ContractError, validate_target
from delivery import authority, publication_target, project_report, failed_report, delivery_record

GROUP = "conformance.re8ch.com"
VERSION = "v1alpha1"
NAMESPACE = os.getenv("POD_NAMESPACE", "api-conformance-system")
CODE_CONFIG_MAP = os.getenv("CODE_CONFIG_MAP", "api-conformance-code")
RUNNER_IMAGE = os.getenv("RUNNER_IMAGE", "schemathesis/schemathesis:4.29.1@sha256:e276982651536cb60c316f805a822b469bbf0d27544794fbeed42eeb957278e0")
APP_ID = os.getenv("GITHUB_APP_ID", "")
PRIVATE_KEY = Path(os.getenv("GITHUB_PRIVATE_KEY_PATH", "/github-app/private-key.pem"))
REPOSITORY_INSTALLATIONS = json.loads(os.getenv("GITHUB_REPOSITORY_INSTALLATIONS", "{}"))
RESULT_BINDINGS = json.loads(os.getenv("RESULT_REPOSITORY_BINDINGS", "{}"))
APP_PERMISSIONS = {"actions": "write", "checks": "write", "contents": "read", "issues": "write", "metadata": "read"}
DISCOVERY_PATHS = ("/openapi.json", "/swagger.json", "/openapi.yaml")
_last_discovery = 0.0


def now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or now()).isoformat().replace("+00:00", "Z")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _tlv(data: bytes, offset: int = 0):
    tag, length, used = data[offset], data[offset + 1], 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[offset + 2:offset + 2 + count], "big")
        used += count
    start = offset + used
    return tag, data[start:start + length], start + length


def _children(sequence: bytes):
    result, offset = [], 0
    while offset < len(sequence):
        tag, value, offset = _tlv(sequence, offset)
        result.append((tag, value))
    return result


def rsa_private_numbers(pem: bytes):
    body = b"".join(line.strip() for line in pem.splitlines() if not line.startswith(b"---"))
    tag, sequence, _ = _tlv(base64.b64decode(body))
    if tag != 0x30:
        raise ValueError("invalid RSA key")
    items = _children(sequence)
    if len(items) >= 3 and items[1][0] == 0x30 and items[2][0] == 0x04:
        tag, sequence, _ = _tlv(items[2][1])
        items = _children(sequence)
    return int.from_bytes(items[1][1], "big"), int.from_bytes(items[3][1], "big")


def github_jwt():
    issued = int(time.time())
    header = b64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = b64url(json.dumps({"iat": issued - 30, "exp": issued + 540, "iss": APP_ID}, separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    digest = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(signing).digest()
    modulus, private_exponent = rsa_private_numbers(PRIVATE_KEY.read_bytes())
    size = (modulus.bit_length() + 7) // 8
    encoded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    signature = pow(int.from_bytes(encoded, "big"), private_exponent, modulus).to_bytes(size, "big")
    return f"{header}.{payload}.{b64url(signature)}"


class NoGitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ContractError("GitHub redirects are forbidden")


def http_json(url: str, token: str, method: str = "GET", body: dict | None = None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "api.github.com" or parsed.fragment:
        raise ContractError("unexpected GitHub API origin")
    raw = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=raw, method=method, headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "Content-Type": "application/json", "User-Agent": "re8ch-api-conformance",
        "X-GitHub-Api-Version": "2026-03-10",
    })
    with urllib.request.build_opener(NoGitHubRedirect()).open(request, timeout=20) as response:
        return json.load(response) if response.length != 0 else {}


def installation_token(installation_id: str, repository: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ContractError("invalid repository")
    if not installation_id.isdecimal() or REPOSITORY_INSTALLATIONS.get(repository) != installation_id:
        raise ContractError("unreviewed repository installation")
    if not APP_ID or not installation_id or not PRIVATE_KEY.is_file():
        raise ContractError("GitHub App is not configured")
    jwt = github_jwt()
    installation = http_json(f"https://api.github.com/repos/{repository}/installation", jwt)
    if str(installation.get("id")) != installation_id or str(installation.get("app_id")) != APP_ID:
        raise ContractError("repository installation mismatch")
    result = http_json(f"https://api.github.com/app/installations/{installation_id}/access_tokens", jwt, "POST",
                       {"repositories": [repository.split("/")[1]], "permissions": APP_PERMISSIONS})
    return result["token"]


def kube_request(method: str, path: str, body: dict | None = None, content_type: str = "application/json"):
    host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
    raw = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"https://{host}:{port}{path}", data=raw, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": content_type})
    context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    with urllib.request.urlopen(request, context=context, timeout=15) as response:
        return json.load(response) if response.length != 0 else {}


def list_resource(plural: str):
    return kube_request("GET", f"/apis/{GROUP}/{VERSION}/namespaces/{NAMESPACE}/{plural}").get("items", [])


def patch_status(plural: str, name: str, status: dict):
    return kube_request("PATCH", f"/apis/{GROUP}/{VERSION}/namespaces/{NAMESPACE}/{plural}/{name}/status",
                        {"status": status}, "application/merge-patch+json")


def due(status: dict, key: str, seconds: int) -> bool:
    value = status.get(key)
    if not value:
        return True
    try:
        return (now() - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds() >= seconds
    except ValueError:
        return True


def active_runs():
    return [item for item in list_resource("apiconformanceruns")
            if item.get("status", {}).get("phase", "Pending") in {"Pending", "Starting", "Running"}]


def create_run(target: dict, mode: str, trigger: str, run_name: str | None = None) -> None:
    validate_target(target["spec"])
    if mode not in {"read", "mutation"}:
        raise ContractError("invalid run mode")
    target_name = target["metadata"]["name"]
    if run_name is None:
        run_name = f"{target_name}-{mode}-{int(time.time())}"
        run = {
            "apiVersion": f"{GROUP}/{VERSION}", "kind": "APIConformanceRun",
            "metadata": {"name": run_name, "namespace": NAMESPACE, "labels": {"conformance.re8ch.com/target": target_name}},
            "spec": {"targetRef": target_name, "mode": mode, "trigger": trigger,
                     "commit": target["spec"].get("commit", ""), "imageDigest": target["spec"].get("imageDigest", ""),
                     "publication": authority(target)},
        }
        kube_request("POST", f"/apis/{GROUP}/{VERSION}/namespaces/{NAMESPACE}/apiconformanceruns", run)
    # Persist the lock before submitting a Job. Lost responses and restarts must
    # never schedule a second mutation for an already-created Run.
    patch_status("apiconformanceruns", run_name, {"phase": "Starting", "startedAt": timestamp()})
    target_json = json.dumps(target["spec"], separators=(",", ":"), sort_keys=True)
    env = [
        {"name": "TARGET_NAME", "value": target_name}, {"name": "RUN_ID", "value": run_name},
        {"name": "RUN_MODE", "value": mode}, {"name": "TARGET_JSON", "value": target_json},
    ]
    auth = target["spec"].get("authSecretRef")
    if auth:
        env.append({"name": "AUTHORIZATION", "valueFrom": {"secretKeyRef": {"name": auth["name"], "key": auth.get("key", "authorization")}}})
    job = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": run_name, "namespace": NAMESPACE,
                     "labels": {"app.kubernetes.io/name": "api-conformance-runner", "conformance.re8ch.com/run": run_name}},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": min(930, int(target["spec"].get("limits", {}).get("timeoutSeconds", 600)) + 60),
                 "ttlSecondsAfterFinished": 604800,
                 "template": {"metadata": {"labels": {"app.kubernetes.io/name": "api-conformance-runner", "conformance.re8ch.com/run": run_name}},
                              "spec": {"automountServiceAccountToken": False, "restartPolicy": "Never",
                                       "securityContext": {"runAsNonRoot": True, "runAsUser": 65532, "runAsGroup": 65532, "seccompProfile": {"type": "RuntimeDefault"}},
                                       "containers": [{"name": "runner", "image": RUNNER_IMAGE, "command": ["python", "/app/runner.py"],
                                                       "env": env, "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}},
                                                       "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                                                       "volumeMounts": [{"name": "code", "mountPath": "/app", "readOnly": True}, {"name": "tmp", "mountPath": "/tmp"}]}],
                                       "volumes": [{"name": "code", "configMap": {"name": CODE_CONFIG_MAP}}, {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}]}}},
    }
    created = kube_request("POST", f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs", job)
    allowed_hosts = target["spec"].get("network", {}).get("allowedHosts", [])
    policy = {
        "apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
        "metadata": {"name": run_name, "namespace": NAMESPACE,
                     "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job", "name": run_name,
                                           "uid": created["metadata"]["uid"], "controller": True, "blockOwnerDeletion": True}]},
        "spec": {"endpointSelector": {"matchLabels": {"conformance.re8ch.com/run": run_name}},
                 "egress": [
                     {"toEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system", "k8s:k8s-app": "kube-dns"}}],
                      "toPorts": [{"ports": [{"port": "53", "protocol": "UDP"}, {"port": "53", "protocol": "TCP"}]}]},
                     {"toFQDNs": [{"matchName": host} for host in allowed_hosts],
                      "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]},
                 ]},
    }
    kube_request("POST", f"/apis/cilium.io/v2/namespaces/{NAMESPACE}/ciliumnetworkpolicies", policy)
    patch_status("apiconformanceruns", run_name, {"phase": "Running", "startedAt": timestamp(), "jobRef": run_name})


def pod_log(run_name: str) -> str:
    query = urllib.parse.urlencode({"labelSelector": f"conformance.re8ch.com/run={run_name}"})
    pods = kube_request("GET", f"/api/v1/namespaces/{NAMESPACE}/pods?{query}").get("items", [])
    if not pods:
        return ""
    name = pods[0]["metadata"]["name"]
    host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
    request = urllib.request.Request(f"https://{host}:{port}/api/v1/namespaces/{NAMESPACE}/pods/{name}/log?container=runner&tailLines=2",
                                     headers={"Authorization": f"Bearer {token}"})
    context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    with urllib.request.urlopen(request, context=context, timeout=15) as response:
        return response.read(65536).decode("utf-8", "replace")


def publish(target: dict, report: dict, streak: int) -> None:
    github = target["spec"].get("github", {})
    repository, sha, name = target["spec"]["repository"], target["spec"]["commit"], target["metadata"]["name"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ContractError("full commit SHA required")
    token = installation_token(str(github.get("installationId", "")), repository)
    commit = http_json(f"https://api.github.com/repos/{repository}/commits/{sha}", token)
    if commit.get("sha") != sha:
        raise ContractError("repository commit mismatch")
    conclusion = "success" if report.get("status") == "passed" else "failure"
    if os.getenv("RESULT_DELIVERY_MODE", "dispatch") != "oidc":
        http_json(f"https://api.github.com/repos/{repository}/check-runs", token, "POST", {
            "name": f"re8ch/schemathesis/{name}", "head_sha": sha, "status": "completed", "conclusion": conclusion,
            "output": {"title": f"Schemathesis {conclusion}", "summary": json.dumps(report, indent=2)[:60000]},
        })
    if os.getenv("RESULT_DELIVERY_MODE", "dispatch") == "oidc":
        inputs = {"run_id": report["runId"]}
    else:
        encoded = base64.b64encode(json.dumps(report, separators=(",", ":"), sort_keys=True).encode()).decode()
        inputs = {"report": encoded, "run_id": report["runId"]}
    http_json(f"https://api.github.com/repos/{repository}/actions/workflows/re8ch-schemathesis-result.yml/dispatches", token, "POST",
              {"ref": target["spec"].get("branch", "main"), "inputs": inputs})
    marker = f"[Schemathesis] regression: {name}"
    issues = http_json(f"https://api.github.com/repos/{repository}/issues?state=all&per_page=100", token)
    issue = next((item for item in issues if item.get("title") == marker and "pull_request" not in item), None)
    if conclusion == "failure":
        uncertain = (report.get("mode") == "mutation" or report.get("lifecycle", {}).get("cleanup") == "failed"
                     or report.get("lifecycle", {}).get("reversal") == "failed")
        priority = "**Priority: high — mutation testing is blocked until cleanup/reversal is reviewed.**\n\n" if uncertain else ""
        body = "Automated production-canary API conformance regression.\n\n" + priority + "```json\n" + json.dumps(report, indent=2) + "\n```\n"
        if issue:
            http_json(f"https://api.github.com/repos/{repository}/issues/{issue['number']}", token, "PATCH", {"state": "open", "body": body})
        else:
            http_json(f"https://api.github.com/repos/{repository}/issues", token, "POST", {"title": marker, "body": body})
    elif issue and issue.get("state") == "open" and streak >= 3:
        http_json(f"https://api.github.com/repos/{repository}/issues/{issue['number']}", token, "PATCH",
                  {"state": "closed", "state_reason": "completed", "body": "Recovered after three consecutive passing runs."})


def collect_runs(targets: dict[str, dict]) -> None:
    jobs = {item["metadata"]["name"]: item for item in kube_request("GET", f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs").get("items", [])}
    for run in list_resource("apiconformanceruns"):
        phase = run.get("status", {}).get("phase", "Pending")
        if phase not in {"Starting", "Running", "Pending"} or (phase == "Pending" and run["spec"].get("trigger") == "manual"):
            continue
        job = jobs.get(run["metadata"]["name"])
        if not job or not (job.get("status", {}).get("succeeded") or job.get("status", {}).get("failed")):
            continue
        text = pod_log(run["metadata"]["name"])
        try:
            report = project_report(json.loads(next(line for line in reversed(text.splitlines()) if line.startswith("{"))), run)
        except Exception:
            report = failed_report(run)
        target = targets.get(run["spec"]["targetRef"])
        previous = int(target.get("status", {}).get("consecutivePasses", 0)) if target else 0
        streak = previous + 1 if report.get("status") == "passed" else 0
        completion = {"phase": "Succeeded" if report.get("status") == "passed" else "Failed",
                      "finishedAt": timestamp(), "result": report}
        if os.getenv("RESULT_DELIVERY_MODE", "dispatch") == "oidc":
            try:
                repository = publication_target(run)["spec"]["repository"]
                completion["delivery"] = delivery_record(run, report, RESULT_BINDINGS.get(repository), timestamp(now() + timedelta(hours=1)))
            except Exception:
                completion["publication"] = "blocked"
        patch_status("apiconformanceruns", run["metadata"]["name"], completion)
        if target:
            mode = run["spec"]["mode"]
            status = {**target.get("status", {}), "consecutivePasses": streak, "lastResult": report.get("status"),
                      "lastRunRef": run["metadata"]["name"], "lastReadAt" if mode == "read" else "lastMutationAt": timestamp()}
            # Missing/truncated logs, timeout, a lost write response or failed
            # compensation all require review. Cleanup success alone does not
            # prove that an unknown write was reversed.
            if mode == "mutation" and report.get("status") != "passed":
                status["mutationBlocked"] = True
                status["mutationBlockedReason"] = "mutation_run_failed_requires_review"
            try:
                if completion.get("publication") == "blocked":
                    raise ContractError("delivery initialization blocked")
                publish(publication_target(run), report, streak)
                status["publication"] = "published"
                patch_status("apiconformanceruns", run["metadata"]["name"], {"publication": "published"})
            except Exception as exc:
                status["publication"] = "blocked"
                status["publicationReason"] = type(exc).__name__
                patch_status("apiconformanceruns", run["metadata"]["name"], {"publication": "blocked", "publicationReason": type(exc).__name__})
            patch_status("apiconformancetargets", target["metadata"]["name"], status)


def discovery_sources() -> list[dict]:
    sources = []
    paths = (
        "/apis/gateway.networking.k8s.io/v1/httproutes",
        "/apis/networking.k8s.io/v1/ingresses",
        "/api/v1/services",
    )
    for path in paths:
        try:
            sources.extend(kube_request("GET", path).get("items", []))
        except urllib.error.HTTPError as exc:
            if exc.code not in (403, 404):
                raise
    return sources


def source_candidate(item: dict) -> tuple[str, str, str] | None:
    metadata = item.get("metadata", {})
    annotations = metadata.get("annotations", {}) or {}
    repository = annotations.get("conformance.re8ch.com/repository", "")
    installation_id = annotations.get("conformance.re8ch.com/github-installation-id", "")
    if not repository or not installation_id:
        return None
    if base := annotations.get("conformance.re8ch.com/base-url"):
        return base.rstrip("/"), repository, installation_id
    kind = item.get("kind")
    if kind == "HTTPRoute":
        hosts = item.get("spec", {}).get("hostnames", [])
    elif kind == "Ingress":
        hosts = [rule.get("host") for rule in item.get("spec", {}).get("rules", [])]
    else:
        hosts = []
    host = next((value for value in hosts if value and "*" not in value), None)
    return (f"https://{host}", repository, installation_id) if host else None


def discover_schema(base_url: str) -> tuple[str, str] | None:
    for path in DISCOVERY_PATHS:
        try:
            request = urllib.request.Request(base_url + path, headers={"Accept": "application/json, application/yaml"})
            with urllib.request.urlopen(request, timeout=8) as response:
                if response.status != 200:
                    continue
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    continue
                return base_url + path, hashlib.sha256(raw).hexdigest()
        except Exception:
            continue
    return None


def onboarding_issue(repository: str, installation_id: str, base_url: str, schema_url: str, digest: str) -> None:
    token = installation_token(installation_id, repository)
    host = urllib.parse.urlparse(base_url).hostname
    title = f"[Schemathesis] onboarding: {host}"
    issues = http_json(f"https://api.github.com/repos/{repository}/issues?state=all&per_page=100", token)
    if any(item.get("title") == title for item in issues if "pull_request" not in item):
        return
    suggested = {
        "version": 1, "baseUrl": base_url, "schema": {"url": schema_url, "sha256": digest},
        "network": {"allowedHosts": sorted({host})}, "operations": {"readOperationIds": [], "mutationOperationIds": []},
        "limits": {"maxExamples": 25, "maxRequests": 500, "ratePerSecond": 1, "timeoutSeconds": 600},
    }
    body = ("A managed route exposes an OpenAPI document but has no reviewed APIConformanceTarget. "
            "Discovery did not execute any API operation. Review operation ownership, authentication, and cleanup before enabling it.\n\n"
            "Suggested `.re8ch/api-conformance.yaml`:\n```json\n" + json.dumps(suggested, indent=2) + "\n```\n")
    http_json(f"https://api.github.com/repos/{repository}/issues", token, "POST", {"title": title, "body": body})


def discover(targets: dict[str, dict]) -> None:
    global _last_discovery
    if os.getenv("DISCOVERY_ENABLED", "false") != "true":
        return
    if time.monotonic() - _last_discovery < 86400:
        return
    _last_discovery = time.monotonic()
    configured = {urllib.parse.urlparse(item["spec"]["baseUrl"]).hostname for item in targets.values()}
    seen = set()
    for item in discovery_sources():
        candidate = source_candidate(item)
        if not candidate:
            continue
        base_url, repository, installation_id = candidate
        host = urllib.parse.urlparse(base_url).hostname
        if not host or host in configured or (repository, host) in seen:
            continue
        seen.add((repository, host))
        if found := discover_schema(base_url):
            try:
                onboarding_issue(repository, installation_id, base_url, found[0], found[1])
            except Exception as exc:
                print(json.dumps({"level": "warning", "event": "discovery-publication-blocked", "error": type(exc).__name__}), flush=True)


def reconcile() -> None:
    targets = {item["metadata"]["name"]: item for item in list_resource("apiconformancetargets")}
    collect_runs(targets)
    discover(targets)
    active = active_runs()
    running = [item for item in active if item.get("status", {}).get("phase") in {"Starting", "Running"}
               or (item.get("status", {}).get("phase", "Pending") == "Pending" and item["spec"].get("trigger") != "manual")]
    running_targets = {item["spec"]["targetRef"] for item in running}
    for run in active:
        phase = run.get("status", {}).get("phase", "Pending")
        if phase != "Pending" or run.get("spec", {}).get("trigger") != "manual":
            continue
        target = targets.get(run.get("spec", {}).get("targetRef"))
        mode = run.get("spec", {}).get("mode")
        valid = (target and not target["spec"].get("suspend", False) and
                 run["spec"].get("commit") == target["spec"].get("commit") and
                 run["spec"].get("imageDigest") == target["spec"].get("imageDigest") and
                 run["spec"].get("publication") == authority(target) and
                 (mode == "read" or (mode == "mutation" and not target.get("status", {}).get("mutationBlocked") and
                                      bool(target["spec"].get("operations", {}).get("mutationOperationIds")))))
        if not valid:
            patch_status("apiconformanceruns", run["metadata"]["name"],
                         {"phase": "Failed", "finishedAt": timestamp(), "reason": "invalid_manual_request"})
            continue
        if len(running) < 2 and target["metadata"]["name"] not in running_targets:
            create_run(target, mode, "manual", run["metadata"]["name"])
            return
    if len(running) >= 2:
        return
    active_targets = running_targets
    for name, target in targets.items():
        if name in active_targets or target["spec"].get("suspend", False):
            continue
        try:
            validate_target(target["spec"])
        except ContractError as exc:
            patch_status("apiconformancetargets", name, {"ready": False, "reason": str(exc)})
            continue
        status = target.get("status", {})
        if due(status, "lastReadAt", int(target["spec"].get("schedule", {}).get("readSeconds", 21600))):
            create_run(target, "read", "schedule")
            return
        if (not status.get("mutationBlocked") and target["spec"].get("operations", {}).get("mutationOperationIds") and
                due(status, "lastMutationAt", int(target["spec"].get("schedule", {}).get("mutationSeconds", 86400)))):
            create_run(target, "mutation", "schedule")
            return


if __name__ == "__main__":
    while True:
        started = time.monotonic()
        try:
            reconcile()
        except Exception as exc:
            print(json.dumps({"level": "error", "event": "reconcile-failed", "error": type(exc).__name__}), flush=True)
        time.sleep(max(5, 60 - (time.monotonic() - started)))
