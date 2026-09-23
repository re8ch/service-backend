#!/usr/bin/env python3
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

API = "https://kubernetes.default.svc"
GROUP = "/apis/anatomy.re8ch.com/v1alpha1"
TOKEN_PATH = os.getenv("SERVICEACCOUNT_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token")
CA = os.getenv("SERVICEACCOUNT_CA_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
TOKEN = open(TOKEN_PATH).read().strip() if os.path.exists(TOKEN_PATH) else ""
CTX = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
POLL = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
TIMEOUT = int(os.getenv("JOB_TIMEOUT_SECONDS", "300"))
IMAGE = os.getenv("ADAPTER_IMAGE", "python:3.13-alpine")
SNAPSHOT_CLASS = os.getenv("VOLUME_SNAPSHOT_CLASS", "runtime-anatomy-cephfs")
CANARY_LABEL = "anatomy.re8ch.com/canary"
ANALYSIS_LABEL = "anatomy.re8ch.com/analysis"
EVIDENCE_NAMESPACE = os.getenv("EVIDENCE_NAMESPACE", "runtime-anatomy")
ANALYSIS_TERMINAL_PHASES = {"Ready", "Incomplete", "Blocked", "Failed"}
SENSITIVE_PATH_PARTS = {
    ".aws", ".docker", ".kube", ".ssh", "credentials", "id_rsa", "id_ed25519",
    "serviceaccount", "token", "secrets", "cookies",
}


def request(path, method="GET", body=None, allow_404=False, raw_response=False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/merge-patch+json" if method == "PATCH" else "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(API + path, data=data, headers=headers, method=method), context=CTX, timeout=20) as response:
            raw = response.read()
            if raw_response:
                return raw.decode(errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        if allow_404 and error.code == 404:
            return None
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"Kubernetes {method} {path}: {error.code} {detail[:500]}") from error


def list_all(plural):
    return request(f"{GROUP}/{plural}").get("items", [])


def get_custom(namespace, plural, name):
    return request(f"{GROUP}/namespaces/{namespace}/{plural}/{name}", allow_404=True)


def patch_status(obj, plural, status):
    namespace = obj["metadata"]["namespace"]
    name = obj["metadata"]["name"]
    return request(f"{GROUP}/namespaces/{namespace}/{plural}/{name}/status", "PATCH", {"status": status})


def condition(kind, state, reason, message=""):
    return {"type": kind, "status": state, "reason": reason, "message": message, "lastTransitionTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def fail(obj, plural, reason, message):
    patch_status(obj, plural, {"phase": "Failed", "conditions": [condition("Ready", "False", reason, message)]})


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_timestamp(value):
    return int(time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))


def sensitive_path(path):
    parts = {part.lower() for part in str(path).replace("\\", "/").split("/") if part}
    return bool(parts & SENSITIVE_PATH_PARTS) or any(
        token in part for part in parts for token in ("password", "private-key", "access-key", "secret-key")
    )


def normalize_access_event(event):
    """Validate an observer event before it can influence sealed analysis evidence."""
    operation = event.get("operation")
    allowed = {"Scan", "Read", "Create", "Modify", "Rename", "Delete", "Metadata"}
    if operation not in allowed:
        raise ValueError(f"unsupported storage operation {operation!r}")
    backend = event.get("backend")
    if backend not in {"Filesystem", "S3"}:
        raise ValueError(f"unsupported storage backend {backend!r}")
    locator = event.get("path") if backend == "Filesystem" else event.get("key")
    if not locator or len(str(locator)) > 4096:
        raise ValueError("storage event locator is missing or too long")
    result = {
        "backend": backend,
        "operation": operation,
        "firstObservedAt": event.get("firstObservedAt"),
        "lastObservedAt": event.get("lastObservedAt"),
        "count": max(1, int(event.get("count", 1))),
        "bytes": max(0, int(event.get("bytes", 0))),
        "source": event.get("source", "Unknown"),
        "confidence": event.get("confidence", "Unknown"),
    }
    for key in ("volume", "claimName", "mountPath", "path", "endpoint", "bucket", "key", "versionId", "etag", "statusCode"):
        if event.get(key) is not None:
            result[key] = event[key]
    content = event.get("contentEvidence")
    if content:
        if sensitive_path(locator):
            result["contentEvidence"] = {"state": "Redacted", "reason": "SensitivePath"}
        else:
            result["contentEvidence"] = content
    return result


def summarize_access_events(events):
    operations = {}
    backends = {}
    bytes_seen = 0
    for event in events:
        operations[event["operation"]] = operations.get(event["operation"], 0) + event["count"]
        backends[event["backend"]] = backends.get(event["backend"], 0) + 1
        bytes_seen += event["bytes"]
    return {"eventCount": len(events), "operationCounts": operations, "backendCounts": backends, "bytes": bytes_seen}


def enforce_content_policy(events, policy):
    enabled = policy.get("captureTextSnippets", True)
    per_item = min(max(int(policy.get("maxSnippetBytes", 4096)), 0), 4096)
    max_count = min(max(int(policy.get("maxSnippetCount", 100)), 0), 100)
    max_total = min(max(int(policy.get("maxTotalSnippetBytes", 1048576)), 0), 1048576)
    captured = total = 0
    credential_pattern = re.compile(
        r"(?i)(authorization\s*[:=]|bearer\s+[a-z0-9._-]+|password\s*[:=]|secret[_-]?key\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    )
    for event in events:
        evidence = event.get("contentEvidence")
        if not evidence or evidence.get("state") == "Redacted":
            continue
        snippet = evidence.get("snippet")
        if not enabled or not isinstance(snippet, str):
            event["contentEvidence"] = {"state": "MetadataOnly", "reason": "ContentCaptureDisabled" if not enabled else "NoTextSnippet"}
            continue
        encoded = snippet.encode("utf-8", errors="replace")
        if credential_pattern.search(snippet):
            event["contentEvidence"] = {"state": "Redacted", "reason": "CredentialPattern"}
            continue
        if captured >= max_count or total >= max_total:
            event["contentEvidence"] = {"state": "MetadataOnly", "reason": "AnalysisContentLimit"}
            continue
        remaining = min(per_item, max_total - total)
        clipped = encoded[:remaining].decode("utf-8", errors="ignore")
        event["contentEvidence"] = {key: value for key, value in evidence.items() if key != "snippet"}
        event["contentEvidence"].update({"state": "Captured", "snippet": clipped, "truncated": len(encoded) > len(clipped.encode())})
        captured += 1
        total += len(clipped.encode())
    return events


def validate_runtime(namespace, name):
    runtime = get_custom(namespace, "runtimes", name)
    if not runtime:
        raise ValueError(f"Runtime {namespace}/{name} not found")
    if runtime.get("metadata", {}).get("labels", {}).get(CANARY_LABEL) != "true":
        raise ValueError("Runtime is outside the labelled canary boundary")
    pvc_name = runtime.get("spec", {}).get("subjectRef", {}).get("name")
    if not pvc_name:
        raise ValueError("Runtime spec.subjectRef.name is required")
    pvc = request(f"/api/v1/namespaces/{namespace}/persistentvolumeclaims/{pvc_name}", allow_404=True)
    if not pvc or pvc.get("metadata", {}).get("labels", {}).get(CANARY_LABEL) != "true":
        raise ValueError("source PVC is missing or outside the labelled canary boundary")
    if pvc.get("status", {}).get("phase") != "Bound":
        raise ValueError("source PVC is not Bound")
    return runtime, pvc_name, pvc


def adapter_program(mode, snapshot=""):
    if mode in ("commit", "manifest"):
        return '''import base64,hashlib,json,os
root="/source"
entries=[]
for base,dirs,files in os.walk(root):
 dirs[:]=[d for d in dirs if d != ".snap"]
 for name in sorted(files):
  path=os.path.join(base,name); rel=os.path.relpath(path,root)
  h=hashlib.sha256()
  with open(path,"rb") as stream:
   for chunk in iter(lambda:stream.read(1048576),b""): h.update(chunk)
  entries.append({"path":rel,"size":os.path.getsize(path),"sha256":h.hexdigest()})
entries.sort(key=lambda x:x["path"])
digest="sha256:"+hashlib.sha256(json.dumps(entries,separators=(",",":"),sort_keys=True).encode()).hexdigest()
result={"manifest":{"digest":digest,"entries":entries,"fileCount":len(entries)}}
print("RUNTIME_ANATOMY_RESULT="+base64.b64encode(json.dumps(result,separators=(",",":")).encode()).decode())'''
    return f'''import base64,hashlib,json,os,shutil
source=os.path.join("/source",".snap",{snapshot!r}); target="/target"
if not os.path.isdir(source): raise SystemExit("snapshot is unavailable")
for name in os.listdir(source):
 src=os.path.join(source,name); dst=os.path.join(target,name)
 shutil.copytree(src,dst,symlinks=True) if os.path.isdir(src) else shutil.copy2(src,dst,follow_symlinks=False)
entries=[]
for base,dirs,files in os.walk(target):
 for name in sorted(files):
  path=os.path.join(base,name); rel=os.path.relpath(path,target); h=hashlib.sha256()
  with open(path,"rb") as stream:
   for chunk in iter(lambda:stream.read(1048576),b""): h.update(chunk)
  entries.append({{"path":rel,"size":os.path.getsize(path),"sha256":h.hexdigest()}})
entries.sort(key=lambda x:x["path"])
digest="sha256:"+hashlib.sha256(json.dumps(entries,separators=(",",":"),sort_keys=True).encode()).hexdigest()
print("RUNTIME_ANATOMY_RESULT="+base64.b64encode(json.dumps({{"manifest":{{"digest":digest,"entries":entries,"fileCount":len(entries)}}}},separators=(",",":")).encode()).decode())'''


def job_body(namespace, name, source_pvc, mode, snapshot, target_pvc=None):
    mounts = [{"name": "source", "mountPath": "/source"}]
    volumes = [{"name": "source", "persistentVolumeClaim": {"claimName": source_pvc}}]
    if target_pvc:
        mounts.append({"name": "target", "mountPath": "/target"})
        volumes.append({"name": "target", "persistentVolumeClaim": {"claimName": target_pvc}})
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "runtime-anatomy", CANARY_LABEL: "true"}}, "spec": {"backoffLimit": 0, "activeDeadlineSeconds": TIMEOUT, "template": {"metadata": {"labels": {"app.kubernetes.io/part-of": "runtime-anatomy", CANARY_LABEL: "true"}}, "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False, "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}}, "containers": [{"name": "adapter", "image": IMAGE, "command": ["python3", "-c", adapter_program(mode, snapshot)], "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}, "volumeMounts": mounts}], "volumes": volumes}}}}


def ensure_job(namespace, name, source_pvc, mode, snapshot, target_pvc=None):
    job = request(f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}", allow_404=True)
    if not job:
        return request(f"/apis/batch/v1/namespaces/{namespace}/jobs", "POST", job_body(namespace, name, source_pvc, mode, snapshot, target_pvc))
    return job


def ensure_snapshot(namespace, name, pvc_name):
    path = f"/apis/snapshot.storage.k8s.io/v1/namespaces/{namespace}/volumesnapshots/{name}"
    snapshot = request(path, allow_404=True)
    if snapshot:
        return snapshot
    body = {"apiVersion": "snapshot.storage.k8s.io/v1", "kind": "VolumeSnapshot", "metadata": {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "runtime-anatomy", CANARY_LABEL: "true"}}, "spec": {"volumeSnapshotClassName": SNAPSHOT_CLASS, "source": {"persistentVolumeClaimName": pvc_name}}}
    return request(f"/apis/snapshot.storage.k8s.io/v1/namespaces/{namespace}/volumesnapshots", "POST", body)


def snapshot_ready(snapshot):
    status = snapshot.get("status", {})
    if status.get("error"):
        raise ValueError("VolumeSnapshot failed: " + status["error"].get("message", "unknown error"))
    return status.get("readyToUse") is True


def ensure_snapshot_pvc(namespace, name, source, snapshot_name):
    pvc = request(f"/api/v1/namespaces/{namespace}/persistentvolumeclaims/{name}", allow_404=True)
    if pvc:
        return pvc
    spec = source["spec"]
    body = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "runtime-anatomy", CANARY_LABEL: "true", "anatomy.re8ch.com/commit-materialization": "true", "storage.re8ch.com/replicated-approved": "true"}}, "spec": {"accessModes": spec["accessModes"], "storageClassName": spec.get("storageClassName"), "dataSource": {"apiGroup": "snapshot.storage.k8s.io", "kind": "VolumeSnapshot", "name": snapshot_name}, "resources": {"requests": {"storage": spec["resources"]["requests"]["storage"]}}}}
    return request(f"/api/v1/namespaces/{namespace}/persistentvolumeclaims", "POST", body)


def job_result(namespace, name):
    job = request(f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}")
    if job.get("status", {}).get("failed"):
        raise ValueError(f"adapter Job {name} failed")
    if not job.get("status", {}).get("succeeded"):
        return None
    pods = request(f"/api/v1/namespaces/{namespace}/pods?labelSelector=" + urllib.parse.quote(f"job-name={name}"))["items"]
    if not pods:
        return None
    log = request(f"/api/v1/namespaces/{namespace}/pods/{pods[0]['metadata']['name']}/log", raw_response=True)
    marker = "RUNTIME_ANATOMY_RESULT="
    line = next((line for line in str(log).splitlines() if line.startswith(marker)), None)
    if not line:
        raise ValueError("adapter Job completed without a result envelope")
    return json.loads(base64.b64decode(line[len(marker):]))


def reconcile_runtime(obj):
    if obj.get("status", {}).get("phase") == "Ready":
        return
    try:
        _, pvc_name, pvc = validate_runtime(obj["metadata"]["namespace"], obj["metadata"]["name"])
        patch_status(obj, "runtimes", {"phase": "Ready", "sourcePVC": pvc_name, "storageClassName": pvc.get("spec", {}).get("storageClassName"), "conditions": [condition("Ready", "True", "CanaryPVCBound")]})
    except Exception as error:
        fail(obj, "runtimes", "InvalidRuntime", str(error))


def reconcile_commit(obj):
    if obj.get("status", {}).get("phase") in ("Sealed", "Failed"):
        return
    namespace = obj["metadata"]["namespace"]
    name = obj["metadata"]["name"]
    try:
        runtime_name = obj.get("spec", {}).get("runtimeRef", {}).get("name")
        _, pvc_name, source = validate_runtime(namespace, runtime_name)
        snapshot = "ra-" + obj["metadata"]["uid"][:12]
        snapshot_obj = ensure_snapshot(namespace, snapshot, pvc_name)
        if not snapshot_ready(snapshot_obj):
            patch_status(obj, "runtimecommits", {"phase": "Capturing", "operationID": obj["metadata"]["uid"], "snapshotName": snapshot, "conditions": [condition("Captured", "False", "VolumeSnapshotPending")]})
            return
        materialized = "ra-snap-" + obj["metadata"]["uid"][:8]
        snapshot_pvc = ensure_snapshot_pvc(namespace, materialized, source, snapshot)
        if snapshot_pvc.get("status", {}).get("phase") != "Bound":
            patch_status(obj, "runtimecommits", {"phase": "Capturing", "operationID": obj["metadata"]["uid"], "snapshotName": snapshot, "materializedPVC": materialized, "conditions": [condition("Captured", "False", "SnapshotPVCPending")]})
            return
        job_name = "ra-commit-" + obj["metadata"]["uid"][:8]
        ensure_job(namespace, job_name, materialized, "manifest", snapshot)
        result = job_result(namespace, job_name)
        if not result:
            patch_status(obj, "runtimecommits", {"phase": "Capturing", "operationID": obj["metadata"]["uid"], "jobRef": {"name": job_name}, "conditions": [condition("Captured", "False", "AdapterJobRunning")]})
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        digest = result["manifest"]["digest"]
        patch_status(obj, "runtimecommits", {"phase": "Sealed", "operationID": obj["metadata"]["uid"], "snapshotName": snapshot, "materializedPVC": materialized, "manifest": result["manifest"], "achievedConsistency": "FilesystemConsistent", "sealedAt": now, "indicators": {"health": "Healthy", "freshness": "Fresh", "validity": "Valid", "durability": "Replicated", "restorability": "Verified", "provenance": "Verified"}, "conditions": [condition("Captured", "True", "CSIVolumeSnapshot"), condition("Verified", "True", "ManifestDigestVerified"), condition("Ready", "True", "Sealed", digest)]})
    except Exception as error:
        fail(obj, "runtimecommits", "CaptureFailed", str(error))


def manifest_entries(commit):
    return {entry["path"]: entry for entry in commit.get("status", {}).get("manifest", {}).get("entries", [])}


def reconcile_diff(obj):
    if obj.get("status", {}).get("phase") in ("Ready", "Failed"):
        return
    namespace = obj["metadata"]["namespace"]
    try:
        base_name = obj.get("spec", {}).get("baseRef", {}).get("name")
        target_name = obj.get("spec", {}).get("targetRef", {}).get("name")
        base = get_custom(namespace, "runtimecommits", base_name)
        target = get_custom(namespace, "runtimecommits", target_name)
        if not base or not target or base.get("status", {}).get("phase") != "Sealed" or target.get("status", {}).get("phase") != "Sealed":
            raise ValueError("both commits must exist and be Sealed")
        left, right = manifest_entries(base), manifest_entries(target)
        added = sorted(set(right) - set(left)); removed = sorted(set(left) - set(right))
        modified = sorted(path for path in set(left) & set(right) if left[path]["sha256"] != right[path]["sha256"])
        patch_status(obj, "runtimediffs", {"phase": "Ready", "added": added, "removed": removed, "modified": modified, "conditions": [condition("Ready", "True", "DiffComputed")]})
    except Exception as error:
        fail(obj, "runtimediffs", "DiffFailed", str(error))


def reconcile_branch(obj):
    if obj.get("status", {}).get("phase") in ("Active", "Failed"):
        return
    namespace = obj["metadata"]["namespace"]
    try:
        commit_name = obj.get("spec", {}).get("commitRef", {}).get("name")
        expected = obj.get("spec", {}).get("expectedHead", "")
        current = obj.get("status", {}).get("head", "")
        if expected != current:
            raise ValueError(f"expectedHead mismatch: expected {expected!r}, current {current!r}")
        commit = get_custom(namespace, "runtimecommits", commit_name)
        if not commit or commit.get("status", {}).get("phase") != "Sealed":
            raise ValueError("branch target must be a Sealed commit")
        patch_status(obj, "runtimebranches", {"phase": "Active", "head": commit_name, "digest": commit["status"]["manifest"]["digest"], "conditions": [condition("Ready", "True", "HeadAdvanced")]})
    except Exception as error:
        fail(obj, "runtimebranches", "CompareAndSwapFailed", str(error))


def quantity_bytes(value):
    units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}
    text = str(value)
    for suffix, multiplier in units.items():
        if text.endswith(suffix):
            return int(text[:-len(suffix)]) * multiplier
    return int(text)


def ensure_target_pvc(namespace, name, source, requested_size=None, snapshot_name=None):
    pvc = request(f"/api/v1/namespaces/{namespace}/persistentvolumeclaims/{name}", allow_404=True)
    if pvc:
        if pvc.get("metadata", {}).get("labels", {}).get(CANARY_LABEL) != "true":
            raise ValueError("target PVC exists outside canary boundary")
        return pvc
    spec = source["spec"]
    source_size = spec["resources"]["requests"]["storage"]
    size = requested_size or source_size
    if quantity_bytes(size) < quantity_bytes(source_size):
        raise ValueError(f"target capacity {size} is smaller than source capacity {source_size}")
    pvc_spec = {"accessModes": spec["accessModes"], "storageClassName": spec.get("storageClassName"), "resources": {"requests": {"storage": size}}}
    if snapshot_name:
        pvc_spec["dataSource"] = {"apiGroup": "snapshot.storage.k8s.io", "kind": "VolumeSnapshot", "name": snapshot_name}
    body = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "runtime-anatomy", CANARY_LABEL: "true", "anatomy.re8ch.com/restored": "true", "storage.re8ch.com/replicated-approved": "true"}}, "spec": pvc_spec}
    return request(f"/api/v1/namespaces/{namespace}/persistentvolumeclaims", "POST", body)


def reconcile_checkout(obj):
    if obj.get("status", {}).get("phase") in ("Ready", "Planned", "Failed", "Blocked"):
        return
    namespace = obj["metadata"]["namespace"]
    try:
        spec = obj.get("spec", {})
        runtime_name = spec.get("runtimeRef", {}).get("name")
        _, source_name, source = validate_runtime(namespace, runtime_name)
        commit_name = spec.get("commitRef", {}).get("name")
        commit = get_custom(namespace, "runtimecommits", commit_name)
        if not commit or commit.get("status", {}).get("phase") != "Sealed":
            patch_status(obj, "runtimecheckouts", {"phase": "Blocked", "conditions": [condition("Ready", "False", "CommitUnavailable")]})
            return
        target = spec.get("target", {}).get("pvcName") or ("restore-" + obj["metadata"]["uid"][:8])
        if spec.get("mode", "Plan") == "Plan":
            patch_status(obj, "runtimecheckouts", {"phase": "Planned", "targetPVC": target, "actions": ["CreateTargetPVC", "CopyCephFSSnapshot", "VerifyManifest"], "conditions": [condition("Ready", "True", "PlanComplete")]})
            return
        requested_size = spec.get("target", {}).get("size")
        if requested_size and quantity_bytes(requested_size) < quantity_bytes(source["spec"]["resources"]["requests"]["storage"]):
            patch_status(obj, "runtimecheckouts", {"phase": "Blocked", "targetPVC": target, "conditions": [condition("Ready", "False", "InsufficientTargetCapacity")]})
            return
        target_pvc = ensure_target_pvc(namespace, target, source, requested_size, commit["status"]["snapshotName"])
        if target_pvc.get("status", {}).get("phase") != "Bound":
            patch_status(obj, "runtimecheckouts", {"phase": "Materializing", "targetPVC": target, "conditions": [condition("Ready", "False", "WaitingForTargetPVC")]})
            return
        job_name = "ra-restore-" + obj["metadata"]["uid"][:8]
        ensure_job(namespace, job_name, target, "manifest", commit["status"]["snapshotName"])
        result = job_result(namespace, job_name)
        if not result:
            patch_status(obj, "runtimecheckouts", {"phase": "Materializing", "targetPVC": target, "jobRef": {"name": job_name}, "conditions": [condition("Ready", "False", "AdapterJobRunning")]})
            return
        expected = commit["status"]["manifest"]["digest"]
        if result["manifest"]["digest"] != expected:
            raise ValueError("restored manifest digest does not match commit")
        patch_status(obj, "runtimecheckouts", {"phase": "Ready", "targetPVC": target, "manifest": result["manifest"], "conditions": [condition("Restorable", "True", "RestoreDigestVerified"), condition("Ready", "True", "RestoreComplete")]})
    except Exception as error:
        fail(obj, "runtimecheckouts", "RestoreFailed", str(error))


def reconcile_gc(obj):
    if obj.get("status", {}).get("phase") == "Ready":
        return
    namespace = obj["metadata"]["namespace"]
    try:
        commits = request(f"{GROUP}/namespaces/{namespace}/runtimecommits").get("items", [])
        branches = request(f"{GROUP}/namespaces/{namespace}/runtimebranches").get("items", [])
        checkouts = request(f"{GROUP}/namespaces/{namespace}/runtimecheckouts").get("items", [])
        roots = {item.get("status", {}).get("head") for item in branches}
        roots |= {item.get("spec", {}).get("commitRef", {}).get("name") for item in checkouts if item.get("status", {}).get("phase") not in ("Failed", "Blocked")}
        roots |= {item["metadata"]["name"] for item in commits if item.get("metadata", {}).get("annotations", {}).get("anatomy.re8ch.com/pinned") == "true"}
        candidates = sorted(item["metadata"]["name"] for item in commits if item["metadata"]["name"] not in roots)
        patch_status(obj, "runtimegarbagecollections", {"phase": "Ready", "mode": "DryRun", "roots": sorted(item for item in roots if item), "candidates": candidates, "deleted": [], "conditions": [condition("Ready", "True", "DryRunComplete")]})
    except Exception as error:
        fail(obj, "runtimegarbagecollections", "ScanFailed", str(error))


def analysis_evidence_name(obj):
    return "runtime-analysis-" + obj["metadata"]["uid"][:12]


def resolve_analysis_target(obj):
    namespace = obj["metadata"]["namespace"]
    spec = obj.get("spec", {})
    target = spec.get("target", {})
    pod_name = target.get("podName")
    container_name = target.get("containerName")
    if not pod_name or not container_name:
        raise ValueError("spec.target.podName and containerName are required")
    pod = request(f"/api/v1/namespaces/{namespace}/pods/{pod_name}", allow_404=True)
    if not pod:
        raise ValueError("target Pod was not found")
    statuses = {item.get("name"): item for item in pod.get("status", {}).get("containerStatuses", [])}
    status = statuses.get(container_name)
    if not status:
        raise ValueError("target container was not found")
    container_id = status.get("containerID", "")
    if not container_id:
        raise ValueError("target container has no runtime identity")
    container = next(item for item in pod.get("spec", {}).get("containers", []) if item.get("name") == container_name)
    mounts = {item.get("mountPath"): item.get("name") for item in container.get("volumeMounts", [])}
    volumes = {item.get("name"): item for item in pod.get("spec", {}).get("volumes", [])}
    requested = spec.get("volumes") or [{"mountPath": path} for path in sorted(mounts)]
    resolved = []
    for item in requested:
        mount_path = item.get("mountPath")
        volume_name = mounts.get(mount_path)
        if not volume_name:
            raise ValueError(f"mountPath {mount_path!r} is not mounted by the target container")
        source = volumes.get(volume_name, {})
        claim_name = source.get("persistentVolumeClaim", {}).get("claimName")
        if item.get("claimName") and item["claimName"] != claim_name:
            raise ValueError(f"mountPath {mount_path!r} does not use requested claim {item['claimName']!r}")
        if not claim_name:
            continue
        pvc = request(f"/api/v1/namespaces/{namespace}/persistentvolumeclaims/{claim_name}", allow_404=True) or {}
        resolved.append({"name": volume_name, "mountPath": mount_path, "claimName": claim_name,
                         "claimUID": pvc.get("metadata", {}).get("uid"),
                         "storageClassName": pvc.get("spec", {}).get("storageClassName")})
    return pod, container_id, resolved


def analysis_evidence(obj):
    name = analysis_evidence_name(obj)
    config = request(f"/api/v1/namespaces/{EVIDENCE_NAMESPACE}/configmaps/{name}", allow_404=True)
    if not config:
        return None
    if config.get("metadata", {}).get("labels", {}).get(ANALYSIS_LABEL) != obj["metadata"]["uid"]:
        raise ValueError("observer evidence identity does not match RuntimeAnalysis UID")
    raw = config.get("data", {}).get("events.json", "[]")
    if len(raw.encode()) > 4 * 1024 * 1024:
        raise ValueError("observer evidence exceeds the 4 MiB ingestion bound")
    events = json.loads(raw)
    if not isinstance(events, list) or len(events) > 10000:
        raise ValueError("observer evidence must contain at most 10000 events")
    normalized = [normalize_access_event(event) for event in events]
    normalized = enforce_content_policy(normalized, obj.get("spec", {}).get("evidencePolicy", {}))
    digest = "sha256:" + hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    data = config.get("data", {})
    return {"events": normalized, "digest": digest, "complete": data.get("complete") == "true",
            "evidenceURI": data.get("evidenceURI"), "observer": data.get("observer", "unknown"),
            "error": data.get("observerError") or None}


def reconcile_analysis(obj):
    if obj.get("status", {}).get("phase") in ANALYSIS_TERMINAL_PHASES:
        return
    namespace = obj["metadata"]["namespace"]
    status = obj.get("status", {})
    spec = obj.get("spec", {})
    try:
        if not status.get("startedAt"):
            pod, container_id, volumes = resolve_analysis_target(obj)
            duration = min(max(int(spec.get("durationSeconds", 60)), 1), 900)
            now = int(time.time())
            confidence = "TraceCorrelated" if spec.get("traceId") else "WindowCorrelated"
            patch_status(obj, "runtimeanalyses", {
                "phase": "Capturing", "startedAt": utc_now(), "startedEpoch": now,
                "deadlineEpoch": now + duration, "evidenceConfigMap": analysis_evidence_name(obj),
                "target": {"podUID": pod["metadata"]["uid"], "containerID": container_id,
                           "nodeName": pod.get("spec", {}).get("nodeName"), "volumes": volumes},
                "correlation": {"traceId": spec.get("traceId"), "confidence": confidence},
                "conditions": [condition("Ready", "False", "CaptureWindowOpen",
                                         "Waiting for the node observer and object audit adapters")],
            })
            return
        evidence = analysis_evidence(obj)
        expired = int(time.time()) >= int(status.get("deadlineEpoch", 0))
        stopped = spec.get("stop") is True
        if not evidence and not (expired or stopped):
            return
        if not evidence:
            patch_status(obj, "runtimeanalyses", {
                **status, "phase": "Incomplete", "completedAt": utc_now(),
                "summary": {"eventCount": 0, "operationCounts": {}, "backendCounts": {}, "bytes": 0},
                "conditions": [condition("Ready", "False", "ObserverEvidenceUnavailable",
                                         "No attributable filesystem or object audit evidence was received")],
            })
            return
        if not evidence["complete"] and not evidence["error"] and not (expired or stopped):
            return
        events = evidence["events"]
        summary = summarize_access_events(events)
        requested_buckets = {scope.get("bucket") for scope in spec.get("objectScopes", []) if scope.get("bucket")}
        observed_buckets = {event.get("bucket") for event in events if event.get("backend") == "S3"}
        missing_buckets = sorted(requested_buckets - observed_buckets)
        if missing_buckets and not (expired or stopped):
            return
        locators = []
        for event in events:
            locator = event.get("path") or event.get("key")
            if locator not in locators:
                locators.append(locator)
        phase = "Ready" if evidence["complete"] and not missing_buckets else "Incomplete"
        reason = "ObjectAuditUnavailable" if missing_buckets else "EvidenceSealed" if evidence["complete"] else "ObserverFailed" if evidence["error"] else "CaptureWindowExpired"
        message = ("No attributable audit events for buckets: " + ", ".join(missing_buckets)) if missing_buckets else evidence["error"] or ""
        patch_status(obj, "runtimeanalyses", {
            **status, "phase": phase, "completedAt": utc_now(), "summary": summary,
            "topLocators": locators[:20], "evidence": {"digest": evidence["digest"],
            "uri": evidence["evidenceURI"], "configMap": analysis_evidence_name(obj),
            "namespace": EVIDENCE_NAMESPACE, "observer": evidence["observer"], "eventsTruncated": len(locators) > 20},
            "findings": ([{"reason": "ObjectAuditUnavailable", "buckets": missing_buckets}] if missing_buckets else []),
            "conditions": [condition("Ready", "True" if phase == "Ready" else "False", reason, message)],
        })
    except Exception as error:
        fail(obj, "runtimeanalyses", "AnalysisFailed", str(error))


RECONCILERS = [("runtimes", reconcile_runtime), ("runtimecommits", reconcile_commit), ("runtimediffs", reconcile_diff), ("runtimebranches", reconcile_branch), ("runtimecheckouts", reconcile_checkout), ("runtimegarbagecollections", reconcile_gc), ("runtimeanalyses", reconcile_analysis)]


def run_once():
    for plural, reconciler in RECONCILERS:
        for item in list_all(plural):
            try:
                reconciler(item)
            except Exception as error:
                print(json.dumps({"level": "error", "resource": plural, "name": item.get("metadata", {}).get("name"), "error": str(error)}), flush=True)


if __name__ == "__main__":
    print(json.dumps({"level": "info", "message": "Runtime Anatomy controller started", "pollSeconds": POLL}), flush=True)
    while True:
        try:
            run_once()
        except Exception as error:
            print(json.dumps({"level": "error", "message": "reconcile pass failed", "error": str(error)}), flush=True)
        time.sleep(POLL)
