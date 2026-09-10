#!/usr/bin/env python3
"""Destructive-within-canary Runtime Anatomy CephFS black-box test."""
import argparse
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

NAMESPACE = "runtime-anatomy-canary"
GROUP = "anatomy.re8ch.com/v1alpha1"


def kubectl(*args, stdin=None, json_output=False):
    result = subprocess.run(["kubectl", *args], input=stdin, text=True, capture_output=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"kubectl {' '.join(args)} failed ({result.returncode}): {detail[:2000]}")
    return json.loads(result.stdout) if json_output else result.stdout.strip()


def apply(obj):
    return kubectl("apply", "-f", "-", stdin=json.dumps(obj))


def resource(kind, name, spec):
    return {"apiVersion": GROUP, "kind": kind, "metadata": {"name": name, "namespace": NAMESPACE}, "spec": spec}


def wait_phase(plural, name, wanted, timeout=360):
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = kubectl("-n", NAMESPACE, "get", plural, name, "-o", "json", json_output=True)
        phase = item.get("status", {}).get("phase")
        if phase in wanted:
            return item
        time.sleep(3)
    raise TimeoutError(f"{plural}/{name} did not reach {wanted}")


def wait_exists(namespace, kind, name, timeout=600):
    """Wait across the asynchronous GitOps reconciliation boundary."""
    deadline = time.time() + timeout
    last_error = "not observed"
    while time.time() < deadline:
        result = subprocess.run(
            ["kubectl", "-n", namespace, "get", kind, name, "-o", "name"],
            text=True, capture_output=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        last_error = (result.stderr or result.stdout).strip()[:2000]
        time.sleep(5)
    raise TimeoutError(f"{kind}/{name} was not created in namespace {namespace}: {last_error}")


def install_failure_evidence(evidence, path):
    def record(exc_type, exc, traceback):
        evidence["result"] = "failed"
        evidence["finishedAt"] = int(time.time())
        evidence["error"] = {"type": exc_type.__name__, "message": str(exc)[:4000]}
        Path(path).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        sys.__excepthook__(exc_type, exc, traceback)
    sys.excepthook = record


def run_pod(name, script, pvc="runtime-anatomy-source-v3"):
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": NAMESPACE, "labels": {"anatomy.re8ch.com/canary": "true"}}, "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False, "containers": [{"name": "probe", "image": "python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d", "command": ["/bin/sh", "-ec", script], "volumeMounts": [{"name": "data", "mountPath": "/data"}], "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}], "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": pvc}}]}}
    apply(pod)
    kubectl("-n", NAMESPACE, "wait", "--for=jsonpath={.status.phase}=Succeeded", f"pod/{name}", "--timeout=180s")
    return kubectl("-n", NAMESPACE, "logs", name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default="runtime-anatomy-canary.evidence.json")
    parser.add_argument("--preflight-timeout", type=int, default=600)
    args = parser.parse_args()
    run = "ra" + secrets.token_hex(4)
    marker_a = secrets.token_hex(16)
    marker_b = secrets.token_hex(16)
    evidence = {"schemaVersion": 1, "run": run, "namespace": NAMESPACE, "startedAt": int(time.time()), "checks": {}}
    install_failure_evidence(evidence, args.evidence)

    wait_exists("flux-system", "helmrelease", "runtime-anatomy", args.preflight_timeout)
    kubectl("-n", "flux-system", "wait", "--for=condition=Ready", "helmrelease/runtime-anatomy", f"--timeout={args.preflight_timeout}s")
    wait_exists("runtime-anatomy", "configmap", "runtime-anatomy-runtime-anatomy-scope", args.preflight_timeout)
    kubectl("-n", "runtime-anatomy", "rollout", "status", "deployment/runtime-anatomy-runtime-anatomy-controller", "--timeout=180s")
    discovered = [line.split(".")[0] for line in kubectl("api-resources", "--api-group=anatomy.re8ch.com", "-o", "name").splitlines()]
    required = {"runtimes", "runtimecommits", "runtimediffs", "runtimebranches", "runtimecheckouts", "runtimegarbagecollections"}
    assert required <= set(discovered), discovered
    kubectl("-n", NAMESPACE, "wait", "--for=jsonpath={.status.phase}=Bound", "pvc/runtime-anatomy-source-v3", "--timeout=180s")
    wait_phase("runtimes", "cephfs-canary", {"Ready"})
    evidence["checks"]["preflight"] = "passed"

    run_pod(f"{run}-write-a", f"mkdir -p /data/nested; printf %s {marker_a} > /data/marker; printf alpha > /data/nested/a.txt; printf removed > /data/remove.txt", "runtime-anatomy-source-v3")
    commit_a = f"{run}-a"
    apply(resource("RuntimeCommit", commit_a, {"runtimeRef": {"name": "cephfs-canary"}, "consistency": "FilesystemConsistent"}))
    commit_a_obj = wait_phase("runtimecommits", commit_a, {"Sealed", "Failed"})
    assert commit_a_obj["status"]["phase"] == "Sealed", commit_a_obj

    run_pod(f"{run}-write-b", f"printf %s {marker_b} > /data/marker; printf beta > /data/nested/b.txt; rm /data/remove.txt", "runtime-anatomy-source-v3")
    commit_b = f"{run}-b"
    apply(resource("RuntimeCommit", commit_b, {"runtimeRef": {"name": "cephfs-canary"}, "parents": [commit_a], "consistency": "FilesystemConsistent"}))
    commit_b_obj = wait_phase("runtimecommits", commit_b, {"Sealed", "Failed"})
    assert commit_b_obj["status"]["phase"] == "Sealed", commit_b_obj

    diff = f"{run}-diff"
    apply(resource("RuntimeDiff", diff, {"baseRef": {"name": commit_a}, "targetRef": {"name": commit_b}}))
    diff_obj = wait_phase("runtimediffs", diff, {"Ready", "Failed"})
    assert "nested/b.txt" in diff_obj["status"]["added"]
    assert "remove.txt" in diff_obj["status"]["removed"]
    assert "marker" in diff_obj["status"]["modified"]

    branch = f"{run}-branch"
    apply(resource("RuntimeBranch", branch, {"runtimeRef": {"name": "cephfs-canary"}, "commitRef": {"name": commit_a}, "expectedHead": ""}))
    assert wait_phase("runtimebranches", branch, {"Active", "Failed"})["status"]["phase"] == "Active"
    bad_branch = f"{run}-bad-cas"
    apply(resource("RuntimeBranch", bad_branch, {"runtimeRef": {"name": "cephfs-canary"}, "commitRef": {"name": commit_b}, "expectedHead": "wrong"}))
    assert wait_phase("runtimebranches", bad_branch, {"Active", "Failed"})["status"]["phase"] == "Failed"

    blocked = f"{run}-small"
    blocked_target = f"{run}-too-small"
    apply(resource("RuntimeCheckout", blocked, {"runtimeRef": {"name": "cephfs-canary"}, "commitRef": {"name": commit_a}, "mode": "Restore", "target": {"pvcName": blocked_target, "size": "1Mi"}}))
    blocked_obj = wait_phase("runtimecheckouts", blocked, {"Ready", "Failed", "Blocked"})
    assert blocked_obj["status"]["phase"] == "Blocked", blocked_obj
    missing = subprocess.run(["kubectl", "-n", NAMESPACE, "get", "pvc", blocked_target], capture_output=True)
    assert missing.returncode != 0, "blocked restore unexpectedly created a target PVC"

    checkout = f"{run}-restore"
    target = f"{run}-restored"
    apply(resource("RuntimeCheckout", checkout, {"runtimeRef": {"name": "cephfs-canary"}, "commitRef": {"name": commit_a}, "mode": "Restore", "target": {"pvcName": target}}))
    checkout_obj = wait_phase("runtimecheckouts", checkout, {"Ready", "Failed", "Blocked"}, 480)
    assert checkout_obj["status"]["phase"] == "Ready", checkout_obj
    restored = run_pod(f"{run}-verify", "test \"$(cat /data/marker)\" = \"" + marker_a + "\"; test -f /data/nested/a.txt; test ! -e /data/nested/b.txt; test -f /data/remove.txt; echo verified", target)
    assert restored == "verified"

    gc = f"{run}-gc"
    apply(resource("RuntimeGarbageCollection", gc, {"mode": "DryRun"}))
    gc_obj = wait_phase("runtimegarbagecollections", gc, {"Ready", "Failed"})
    assert gc_obj["status"]["mode"] == "DryRun" and gc_obj["status"]["deleted"] == []
    assert commit_a in gc_obj["status"]["roots"]

    evidence["checks"].update({"commitA": commit_a_obj["status"], "commitB": commit_b_obj["status"], "diff": diff_obj["status"], "branchCAS": "passed", "insufficientCapacity": blocked_obj["status"], "restore": checkout_obj["status"], "restoreProbe": "passed", "gc": gc_obj["status"]})
    evidence["finishedAt"] = int(time.time())
    evidence["result"] = "passed"
    Path(args.evidence).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"result": "passed", "evidence": args.evidence, "run": run}))


if __name__ == "__main__":
    main()
