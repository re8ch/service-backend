#!/usr/bin/env python3
"""Bounded on-demand ptrace observer for explicit RuntimeAnalysis objects.

This is the portable v1 collector. Its evidence names the PtraceObserver source;
future eBPF collectors can emit the same normalized envelope without changing
the CRD or CLI.
"""
import hashlib
import json
import os
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API = "https://kubernetes.default.svc"
GROUP = "/apis/anatomy.re8ch.com/v1alpha1"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
TOKEN = open(TOKEN_PATH).read().strip() if os.path.exists(TOKEN_PATH) else ""
CTX = ssl.create_default_context(cafile=CA_PATH) if os.path.exists(CA_PATH) else ssl.create_default_context()
NODE = os.getenv("NODE_NAME", "test-node")
POLL = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
MAX_TRACE_BYTES = int(os.getenv("MAX_TRACE_BYTES", str(16 * 1024 * 1024)))
ANALYSIS_LABEL = "anatomy.re8ch.com/analysis"
EVIDENCE_NAMESPACE = os.getenv("EVIDENCE_NAMESPACE", "runtime-anatomy")
SENSITIVE = re.compile(r"(?i)(^|/)(\.aws|\.docker|\.kube|\.ssh|secrets?|tokens?|cookies?|credentials)(/|$)|password|private.?key|access.?key")
CREDENTIAL = re.compile(r"(?i)(authorization\s*[:=]|bearer\s+[a-z0-9._-]+|password\s*[:=]|secret[_-]?key\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def request(path, method="GET", body=None, content_type="application/json", allow_404=False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = content_type
    try:
        with urllib.request.urlopen(urllib.request.Request(API + path, data=data, headers=headers, method=method), context=CTX, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        if allow_404 and error.code == 404:
            return None
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"Kubernetes {method} {path}: {error.code} {detail[:500]}") from error


def target_pid(container_id):
    identity = container_id.split("://", 1)[-1]
    matches = []
    for name in os.listdir("/host/proc"):
        if not name.isdigit():
            continue
        try:
            cgroup = open(f"/host/proc/{name}/cgroup").read()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if identity in cgroup:
            matches.append(int(name))
    if not matches:
        raise RuntimeError("target container cgroup has no live host PID")
    return min(matches)


def within_scope(path, mounts):
    normalized = os.path.normpath(path)
    return any(normalized == root or normalized.startswith(root.rstrip("/") + "/") for root in mounts)


def operation_for(syscall, detail):
    if syscall in {"getdents", "getdents64"}:
        return "Scan"
    if syscall in {"rename", "renameat", "renameat2"}:
        return "Rename"
    if syscall in {"unlink", "unlinkat", "rmdir"}:
        return "Delete"
    if syscall in {"truncate", "ftruncate", "write", "pwrite", "pwrite64", "writev"}:
        return "Modify"
    if syscall in {"stat", "lstat", "newfstatat", "access", "faccessat", "readlink", "readlinkat"}:
        return "Metadata"
    if "O_DIRECTORY" in detail:
        return "Scan"
    if "O_CREAT" in detail:
        return "Create"
    if "O_WRONLY" in detail or "O_RDWR" in detail or "O_TRUNC" in detail:
        return "Modify"
    return "Read"


def parse_trace(path, mounts, pid, evidence_policy=None):
    records = {}
    line_pattern = re.compile(r"(?:^|\s)([a-zA-Z0-9_]+)\((.*)\)\s+=\s+(-?\d+)")
    quoted_path = re.compile(r'"((?:[^"\\]|\\.)*)"')
    fd_path = re.compile(r"\d+<([^>]+)>")
    for trace_path in sorted(path.parent.glob(path.name + "*")):
        for line in trace_path.read_text(errors="replace").splitlines():
            match = line_pattern.search(line)
            if not match:
                continue
            syscall, detail, result = match.groups()
            candidates = fd_path.findall(detail)
            candidates += [bytes(value, "utf-8").decode("unicode_escape") for value in quoted_path.findall(detail)]
            storage_path = next((value for value in candidates if value.startswith("/") and within_scope(value, mounts)), None)
            if not storage_path:
                continue
            operation = operation_for(syscall, detail)
            key = (operation, storage_path)
            row = records.setdefault(key, {"backend": "Filesystem", "operation": operation, "path": storage_path,
                "source": "PtraceObserver", "confidence": "SyscallObserved", "count": 0, "bytes": 0,
                "firstObservedAt": int(time.time()), "lastObservedAt": int(time.time())})
            row["count"] += 1
            row["lastObservedAt"] = int(time.time())
            if syscall in {"read", "pread", "pread64", "write", "pwrite", "pwrite64", "writev"} and int(result) > 0:
                row["bytes"] += int(result)
    events = list(records.values())
    for row in events:
        attach_content_evidence(row, pid)
    apply_content_policy(events, evidence_policy or {})
    return events


def apply_content_policy(events, policy):
    enabled = policy.get("captureTextSnippets", True)
    per_item = min(max(int(policy.get("maxSnippetBytes", 4096)), 0), 4096)
    max_count = min(max(int(policy.get("maxSnippetCount", 100)), 0), 100)
    max_total = min(max(int(policy.get("maxTotalSnippetBytes", 1048576)), 0), 1048576)
    count = total = 0
    for row in events:
        evidence = row.get("contentEvidence")
        snippet = evidence.get("snippet") if evidence else None
        if snippet is None:
            continue
        if not enabled or count >= max_count or total >= max_total:
            row["contentEvidence"] = {key: value for key, value in evidence.items() if key != "snippet"}
            row["contentEvidence"].update({"state": "MetadataOnly", "reason": "ContentCaptureDisabled" if not enabled else "AnalysisContentLimit"})
            continue
        encoded = snippet.encode()[:min(per_item, max_total - total)]
        clipped = encoded.decode("utf-8", errors="ignore")
        evidence["snippet"] = clipped
        evidence["truncated"] = len(clipped.encode()) < len(snippet.encode())
        count += 1
        total += len(clipped.encode())


def attach_content_evidence(row, pid):
    path = row.get("path", "")
    if SENSITIVE.search(path):
        row["contentEvidence"] = {"state": "Redacted", "reason": "SensitivePath"}
        return
    host_path = f"/host/proc/{pid}/root{path}"
    try:
        stat = os.stat(host_path)
        if not os.path.isfile(host_path):
            return
        digest = hashlib.sha256()
        with open(host_path, "rb") as stream:
            data = stream.read(4097)
            digest.update(data)
            for chunk in iter(lambda: stream.read(1048576), b""):
                digest.update(chunk)
        evidence = {"state": "MetadataOnly", "sha256": digest.hexdigest(), "size": stat.st_size}
        if len(data) <= 4096:
            try:
                snippet = data.decode("utf-8")
                if CREDENTIAL.search(snippet):
                    evidence = {"state": "Redacted", "reason": "CredentialPattern", "sha256": digest.hexdigest(), "size": stat.st_size}
                else:
                    evidence.update({"state": "Captured", "snippet": snippet})
            except UnicodeDecodeError:
                pass
        row["contentEvidence"] = evidence
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        row["contentEvidence"] = {"state": "MetadataOnly", "reason": "PostStateUnavailable"}


def publish(analysis, events, complete, error=None):
    uid = analysis["metadata"]["uid"]
    name = "runtime-analysis-" + uid[:12]
    body = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": EVIDENCE_NAMESPACE,
        "labels": {ANALYSIS_LABEL: uid, "app.kubernetes.io/part-of": "runtime-anatomy"}},
        "data": {"events.json": json.dumps(events, separators=(",", ":")), "complete": str(complete).lower(),
                 "observer": "ptrace-v1", "observerError": error or ""}}
    path = f"/api/v1/namespaces/{EVIDENCE_NAMESPACE}/configmaps/{name}"
    existing = request(path, allow_404=True)
    if existing:
        body["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]
        request(path, "PUT", body)
    else:
        request(f"/api/v1/namespaces/{EVIDENCE_NAMESPACE}/configmaps", "POST", body)


def observe(analysis):
    status = analysis.get("status", {})
    pid = target_pid(status["target"]["containerID"])
    mounts = [item["mountPath"] for item in status["target"].get("volumes", [])]
    if not mounts:
        publish(analysis, [], False, "NoPersistentVolumeMounts")
        return
    remaining = max(1, int(status["deadlineEpoch"]) - int(time.time()))
    with tempfile.TemporaryDirectory() as directory:
        output = os.path.join(directory, "trace")
        command = ["strace", "-ff", "-tt", "-yy", "-s", "4096", "-o", output, "-p", str(pid), "-e",
                   "trace=%file,read,pread64,write,pwrite64,writev,getdents64,fsync,fdatasync"]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + remaining
        collector_error = None
        while time.time() < deadline and process.poll() is None:
            current = request(f"{GROUP}/namespaces/{analysis['metadata']['namespace']}/runtimeanalyses/{analysis['metadata']['name']}", allow_404=True)
            if not current or current.get("spec", {}).get("stop") is True:
                break
            trace_bytes = sum(item.stat().st_size for item in Path(directory).glob("trace*") if item.is_file())
            if trace_bytes > MAX_TRACE_BYTES:
                collector_error = "TraceLimitExceeded"
                break
            time.sleep(min(POLL, max(1, deadline - time.time())))
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        events = parse_trace(Path(output), mounts, pid, analysis.get("spec", {}).get("evidencePolicy", {}))
        if len(events) > 1000:
            events = events[:1000]
            collector_error = collector_error or "EventLimitExceeded"
        stderr = process.stderr.read().strip()[:1000] if process.stderr else ""
        if not events and stderr and "Operation not permitted" in stderr:
            collector_error = "PtraceUnavailable: " + stderr
        publish(analysis, events, not bool(collector_error), collector_error)


def run_once():
    analyses = request(f"{GROUP}/runtimeanalyses").get("items", [])
    for analysis in analyses:
        status = analysis.get("status", {})
        if status.get("phase") != "Capturing" or status.get("target", {}).get("nodeName") != NODE:
            continue
        name = "runtime-analysis-" + analysis["metadata"]["uid"][:12]
        existing = request(f"/api/v1/namespaces/{EVIDENCE_NAMESPACE}/configmaps/{name}", allow_404=True)
        if existing:
            continue
        try:
            observe(analysis)
        except Exception as error:
            publish(analysis, [], False, str(error)[:1000])


if __name__ == "__main__":
    while True:
        try:
            run_once()
        except Exception as error:
            print(json.dumps({"level": "error", "message": str(error)}), flush=True)
        time.sleep(POLL)
