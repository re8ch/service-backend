#!/usr/bin/env python3
"""Bounded executor for approved, immutable release bundles.

The executor does not render source and accepts no arbitrary kubectl arguments.
It verifies the typed request and archive, performs conflict-detecting SSA
dry-runs, applies with one stable field manager, evaluates bounded health,
persists accepted state, and prunes only explicitly authorized stale objects.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Callable

import yaml

from unified_bundle import canonical, digest, object_identity, validate_schema

PROTECTED_KINDS = {"Namespace", "CustomResourceDefinition", "PersistentVolume", "PersistentVolumeClaim", "StorageClass"}
READY_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job"}
Runner = Callable[..., subprocess.CompletedProcess[str]]


class Rejected(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_archive(path: Path) -> tuple[dict, list[dict]]:
    if path.stat().st_size > 12 * 1024 * 1024:
        raise Rejected("bundle archive exceeds 12 MiB")
    try:
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
            if names != ["manifest.json", "objects.json"]:
                raise Rejected(f"unexpected archive members: {names}")
            for member in archive.getmembers():
                if not member.isfile() or member.name.startswith(("/", "../")) or ".." in Path(member.name).parts:
                    raise Rejected(f"unsafe archive member: {member.name}")
            manifest_bytes = archive.extractfile("manifest.json").read()
            objects_bytes = archive.extractfile("objects.json").read()
    except (tarfile.TarError, json.JSONDecodeError) as error:
        raise Rejected("invalid bundle archive") from error
    manifest = json.loads(manifest_bytes)
    objects = json.loads(objects_bytes)
    validate_schema(manifest, "release-bundle-manifest.schema.json")
    if canonical(manifest) != manifest_bytes or canonical(objects) != objects_bytes:
        raise Rejected("bundle members are not canonical JSON")
    if digest(objects_bytes) != manifest["renderedDigest"]:
        raise Rejected("rendered digest mismatch")
    inventory = sorted(object_identity(item) for item in objects)
    if inventory != manifest["inventory"] or digest(canonical(inventory)) != manifest["inventoryDigest"]:
        raise Rejected("bundle inventory mismatch")
    return manifest, objects


def object_ref(item: dict) -> dict:
    metadata = item["metadata"]
    result = {"apiVersion": item["apiVersion"], "kind": item["kind"], "name": metadata["name"]}
    if metadata.get("namespace"):
        result["namespace"] = metadata["namespace"]
    return result


class Store:
    def __init__(self, root: Path, release_id: str):
        self.root = root
        self.release_id = release_id
        self.state_path = root / "state" / f"{release_id}.json"

    @contextlib.contextmanager
    def lock(self):
        path = self.root / "locks" / f"{self.release_id}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def state(self) -> dict | None:
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else None

    def result(self, operation_id: str) -> dict | None:
        path = self.root / "results" / self.release_id / f"{operation_id}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def save_result(self, result: dict) -> None:
        atomic_json(self.root / "results" / self.release_id / f"{result['operationId']}.json", result)

    def cache_bundle(self, bundle_path: Path, bundle_digest: str) -> None:
        target = self.root / "bundles" / (bundle_digest.removeprefix("sha256:") + ".tar.gz")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            data = bundle_path.read_bytes()
            descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def cached_bundle(self, bundle_digest: str) -> Path:
        return self.root / "bundles" / (bundle_digest.removeprefix("sha256:") + ".tar.gz")

    def accept(self, state: dict) -> None:
        validate_schema(state, "release-accepted-state.schema.json")
        atomic_json(self.state_path, state)


class Executor:
    def __init__(self, runner: Runner = subprocess.run, now: Callable[[], dt.datetime] = utc_now,
                 sleeper: Callable[[float], None] = time.sleep):
        self.runner = runner
        self.now = now
        self.sleeper = sleeper

    def command(self, argv: list[str], stdin: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        result = self.runner(argv, input=stdin, text=True, capture_output=True, timeout=timeout, check=False)
        if result.returncode:
            message = (result.stderr or result.stdout or "command failed")[-2048:]
            raise Rejected(message)
        return result

    def validate(self, contract: dict, operation: dict, bundle_path: Path, store: Store) -> tuple[dict, list[dict], str]:
        validate_schema(contract, "release-contract.schema.json")
        validate_schema(operation, "release-operation.schema.json")
        now = self.now()
        if parse_time(operation["issuedAt"]) > now + dt.timedelta(minutes=2) or parse_time(operation["expiresAt"]) <= now:
            raise Rejected("operation is stale or not yet valid")
        request_digest = digest(canonical(operation))
        prior_result = store.result(operation["operationId"])
        if prior_result:
            if prior_result["requestDigest"] != request_digest:
                raise Rejected("operation ID was reused with different content")
            return {}, [], request_digest
        state = store.state()
        if state and state.get("blocked"):
            raise Rejected(f"release is blocked: {state.get('blockedReason', 'manual recovery required')}")
        expected_sequence = 1 if state is None else state["sequence"] + 1
        if operation["sequence"] != expected_sequence:
            raise Rejected(f"out-of-order operation: expected sequence {expected_sequence}")
        if operation["releaseId"] != contract["metadata"]["name"]:
            raise Rejected("release ID mismatch")
        target = contract["spec"]["target"]
        if operation["target"] != f"{target['cluster']}/{target['namespace']}":
            raise Rejected("target mismatch")
        contract_digest = digest(canonical(contract))
        if operation["contractDigest"] != contract_digest:
            raise Rejected("contract digest mismatch")
        provenance = contract["spec"]["provenance"]
        github = operation["github"]
        for key in ("repository", "workflow", "ref", "environment", "audience"):
            if github[key] != provenance[key]:
                raise Rejected(f"GitHub {key} is not authorized")
        for key in ("signer", "issuer"):
            if operation["bundle"][key] != provenance[key]:
                raise Rejected(f"bundle {key} is not authorized")
        data = bundle_path.read_bytes()
        payload_digest = digest(data)
        if operation["bundle"]["payloadDigest"] != payload_digest:
            raise Rejected("bundle payload digest mismatch")
        manifest, objects = load_archive(bundle_path)
        if manifest["contractDigest"] != contract_digest or manifest["releaseId"] != operation["releaseId"]:
            raise Rejected("bundle is bound to another contract or release")
        if manifest["inventoryDigest"] != operation["inventoryDigest"]:
            raise Rejected("operation inventory differs from bundle")
        if state and operation.get("previousApprovedDigest") != state["appliedDigest"]:
            raise Rejected("previous approved digest mismatch")
        return manifest, objects, request_digest

    def verify_signature(self, operation: dict) -> None:
        bundle = operation["bundle"]
        reference = bundle["reference"].removeprefix("oci://") + "@" + bundle["digest"]
        self.command(["cosign", "verify", "--certificate-identity", bundle["signer"],
                      "--certificate-oidc-issuer", bundle["issuer"], reference], timeout=120)

    @staticmethod
    def with_audit(objects: list[dict], operation: dict) -> list[dict]:
        annotated = json.loads(json.dumps(objects))
        for item in annotated:
            annotations = item.setdefault("metadata", {}).setdefault("annotations", {})
            annotations["delivery.re8ch.com/release-id"] = operation["releaseId"]
            annotations["delivery.re8ch.com/operation-id"] = operation["operationId"]
        return annotated

    def apply(self, objects: list[dict], field_manager: str, dry_run: bool) -> None:
        for item in objects:
            argv = ["kubectl", "apply", "--server-side", f"--field-manager={field_manager}"]
            if dry_run:
                argv.append("--dry-run=server")
            argv += ["-f", "-"]
            self.command(argv, json.dumps(item), timeout=120)

    def healthy(self, item: dict) -> tuple[bool, str]:
        ref = object_ref(item)
        if ref["kind"] not in READY_KINDS:
            return True, "no readiness rule"
        argv = ["kubectl", "get", ref["kind"], ref["name"]]
        if ref.get("namespace"):
            argv += ["--namespace", ref["namespace"]]
        argv += ["-o", "json"]
        live = json.loads(self.command(argv).stdout)
        generation = live.get("metadata", {}).get("generation", 0)
        status = live.get("status", {})
        if status.get("observedGeneration", 0) < generation:
            return False, "generation not observed"
        spec = live.get("spec", {})
        if ref["kind"] in {"Deployment", "StatefulSet"}:
            desired = spec.get("replicas", 1)
            return status.get("availableReplicas", 0) >= desired, "replicas unavailable"
        if ref["kind"] == "DaemonSet":
            return status.get("numberAvailable", 0) >= status.get("desiredNumberScheduled", 0), "daemon pods unavailable"
        if ref["kind"] == "Job":
            return status.get("succeeded", 0) >= spec.get("completions", 1), "job incomplete"
        return True, "healthy"

    def wait_healthy(self, objects: list[dict], timeout_seconds: int) -> tuple[bool, str]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            failures = []
            for item in objects:
                ready, reason = self.healthy(item)
                if not ready:
                    failures.append(f"{object_identity(item)}: {reason}")
            if not failures:
                return True, "declared readiness satisfied"
            if time.monotonic() >= deadline:
                return False, "; ".join(failures)[:512]
            self.sleeper(min(5, max(0, deadline - time.monotonic())))

    def prune(self, previous: dict | None, new_inventory: set[str], contract: dict, field_manager: str) -> None:
        if not previous or not contract["spec"]["lifecycle"]["prune"]:
            return
        allowed = set(contract["spec"]["lifecycle"].get("deletionAllowlist", []))
        stale = set(previous["inventory"]) - new_inventory
        refs = {f"{ref['apiVersion'].split('/', 1)[0] if '/' in ref['apiVersion'] else 'core'}/{ref['kind']}/{ref.get('namespace', '_')}/{ref['name']}": ref for ref in previous["objectRefs"]}
        for identity in sorted(stale):
            ref = refs[identity]
            if identity not in allowed or ref["kind"] in PROTECTED_KINDS or ref["kind"] in set(contract["spec"]["lifecycle"]["protectedKinds"]):
                raise Rejected(f"stale object is not deletion-authorized: {identity}")
            manifest = {"apiVersion": ref["apiVersion"], "kind": ref["kind"], "metadata": {"name": ref["name"]}}
            if ref.get("namespace"):
                manifest["metadata"]["namespace"] = ref["namespace"]
            self.command(["kubectl", "delete", "--wait=true", "-f", "-"], json.dumps(manifest), timeout=120)

    def rollback(self, previous: dict, attempted_manifest: dict, attempted_objects: list[dict],
                 contract: dict, manager: str, store: Store, reason: str) -> tuple[str, str]:
        rollback_digest = previous["appliedDigest"]
        rollback_path = store.cached_bundle(rollback_digest)
        if not rollback_path.exists():
            with tempfile.TemporaryDirectory(prefix="re8ch-rollback-") as temporary:
                self.command([
                    "flux", "pull", "artifact",
                    previous["bundleReference"].removeprefix("oci://") + "@" + rollback_digest,
                    "--output", temporary,
                ], timeout=300)
                fetched = Path(temporary) / "release.bundle"
                if not fetched.is_file() or digest(fetched.read_bytes()) != previous["payloadDigest"]:
                    raise Rejected("rollback bundle payload is unavailable or has the wrong digest")
                store.cache_bundle(fetched, rollback_digest)
        _, rollback_objects = load_archive(rollback_path)
        self.apply(rollback_objects, manager, dry_run=True)
        self.apply(rollback_objects, manager, dry_run=False)
        attempted_state = {
            "inventory": attempted_manifest["inventory"],
            "objectRefs": [object_ref(item) for item in attempted_objects],
        }
        self.prune(attempted_state, set(previous["inventory"]), contract, manager)
        rollback_healthy, rollback_reason = self.wait_healthy(
            rollback_objects, contract["spec"]["lifecycle"]["healthTimeoutSeconds"])
        if not rollback_healthy:
            raise Rejected(rollback_reason)
        return rollback_digest, f"rollback restored previous healthy digest after: {reason}"

    def reconcile(self, contract: dict, operation: dict, bundle_path: Path, state_root: Path,
                  verify_signature: bool = True) -> dict:
        started = self.now()
        validate_schema(operation, "release-operation.schema.json")
        store = Store(state_root, operation["releaseId"])
        with store.lock():
            return self._reconcile_locked(contract, operation, bundle_path, store, started, verify_signature)

    def _reconcile_locked(self, contract: dict, operation: dict, bundle_path: Path, store: Store,
                          started: dt.datetime, verify_signature: bool) -> dict:
        manifest, objects, request_digest = self.validate(contract, operation, bundle_path, store)
        cached = store.result(operation["operationId"])
        if cached:
            return cached
        if verify_signature:
            self.verify_signature(operation)
        previous = store.state()
        audited = self.with_audit(objects, operation)
        manager = contract["spec"]["authorization"]["fieldManager"]
        self.apply(audited, manager, dry_run=True)
        try:
            self.apply(audited, manager, dry_run=False)
            healthy, reason = self.wait_healthy(audited, contract["spec"]["lifecycle"]["healthTimeoutSeconds"])
        except Rejected as error:
            healthy, reason = False, f"apply failed: {error}"
        outcome = "healthy"
        rollback_digest = None
        if not healthy:
            if not previous:
                raise Rejected("health failed and no accepted rollback bundle exists")
            try:
                rollback_digest, reason = self.rollback(previous, manifest, objects, contract, manager, store, reason)
                outcome, healthy = "rolled-back", True
            except Rejected as error:
                blocked = {**previous, "blocked": True, "blockedReason": f"rollback failed: {error}", "updatedAt": self.now().isoformat()}
                store.accept(blocked)
                raise Rejected(blocked["blockedReason"]) from error
        else:
            self.prune(previous, set(manifest["inventory"]), contract, manager)
            store.cache_bundle(bundle_path, operation["bundle"]["digest"])
            state = {
                "schemaVersion": "delivery.re8ch.com/accepted-state/v1alpha1",
                "releaseId": operation["releaseId"], "lastOperationId": operation["operationId"],
                "requestDigest": request_digest, "bundleReference": operation["bundle"]["reference"],
                "payloadDigest": operation["bundle"]["payloadDigest"],
                "appliedDigest": operation["bundle"]["digest"],
                "healthyDigest": operation["bundle"]["digest"], "inventoryDigest": manifest["inventoryDigest"],
                "inventory": manifest["inventory"], "objectRefs": [object_ref(item) for item in objects],
                "sequence": operation["sequence"], "updatedAt": self.now().isoformat(), "blocked": False,
            }
            store.accept(state)
        result = {
            "apiVersion": "delivery.re8ch.com/release-result/v1alpha1",
            "operationId": operation["operationId"], "releaseId": operation["releaseId"],
            "requestDigest": request_digest, "approvedDigest": operation["bundle"]["digest"],
            "observedDigest": rollback_digest if outcome == "rolled-back" else operation["bundle"]["digest"],
            "inventoryDigest": manifest["inventoryDigest"], "startedAt": started.isoformat(),
            "finishedAt": self.now().isoformat(), "outcome": outcome,
            "health": {"status": "healthy" if healthy else "unhealthy", "reason": reason},
        }
        if rollback_digest:
            result["rollbackDigest"] = rollback_digest
        validate_schema(result, "release-operation-result.schema.json")
        store.save_result(result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--operation", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    contract = yaml.safe_load(args.contract.read_text())
    operation = json.loads(args.operation.read_text())
    result = Executor().reconcile(contract, operation, args.bundle, args.state_dir)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
