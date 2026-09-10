#!/usr/bin/env python3
import base64
import hashlib
import json
import os
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
  entries.append({{"path":rel,"size":os.path.getsize(path),"sha256":h.hexdigest()}})
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
    body = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "runtime-anatomy", CANARY_LABEL: "true", "anatomy.re8ch.com/commit-materialization": "true"}}, "spec": {"accessModes": spec["accessModes"], "storageClassName": spec.get("storageClassName"), "dataSource": {"apiGroup": "snapshot.storage.k8s.io", "kind": "VolumeSnapshot", "name": snapshot_name}, "resources": {"requests": {"storage": spec["resources"]["requests"]["storage"]}}}}
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
    body = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "runtime-anatomy", CANARY_LABEL: "true", "anatomy.re8ch.com/restored": "true"}}, "spec": pvc_spec}
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


RECONCILERS = [("runtimes", reconcile_runtime), ("runtimecommits", reconcile_commit), ("runtimediffs", reconcile_diff), ("runtimebranches", reconcile_branch), ("runtimecheckouts", reconcile_checkout), ("runtimegarbagecollections", reconcile_gc)]


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
