#!/usr/bin/env python3
"""Run the one-shot, OIDC-authorized GHCR to Harbor mirror Job."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

JOB_NAME = "release-artifact-mirror"
NAMESPACE = "flux-system"
ORAS_IMAGE = "registry.re8ch.com/proxy-ghcr/oras-project/oras@sha256:a3ce6b38d4c510ea9fdc0449b942ea44fb790f157e79b5e7e30b1e7460fe5579"
COSIGN_IMAGE = "registry.re8ch.com/functions-shared/cosign@sha256:57c0e93a829ae213ab4273b5bd31bc24812043183040882d7cc215a12b5a6870"
ISSUER = "https://token.actions.githubusercontent.com"
PUBLISHER_AUDIENCE = "https://kube-api.re8ch.com/release-artifact-publisher"

SOURCE_RE = re.compile(
    r"^ghcr\.io/re8ch/(?:release-compat-controller|"
    r"service-backend-chart/(?:rook-ceph|ceph-csi-drivers|rook-ceph-cluster)|"
    r"service-backend-release/(?:rook-ceph|ceph-csi-drivers|rook-ceph-cluster))$"
)
TARGET_RE = re.compile(
    r"^registry\.re8ch\.com/functions-shared/(?:release-compat-controller|"
    r"rook-ceph|ceph-csi-drivers|rook-ceph-cluster|"
    r"ceph-release-(?:rook-ceph|ceph-csi-drivers|rook-ceph-cluster))$"
)
SIGNERS = {
    "https://github.com/re8ch/service-backend/.github/workflows/release-compat-controller.yml@refs/heads/main",
    "https://github.com/re8ch/service-backend/.github/workflows/ceph-shadow-compile.yml@refs/heads/main",
}


def manifest(source: str, target: str, digest: str, tag: str, signer: str, operation_id: str) -> dict:
    if not SOURCE_RE.fullmatch(source):
        raise ValueError("source repository is outside the release allowlist")
    if not TARGET_RE.fullmatch(target):
        raise ValueError("target repository is outside functions-shared allowlist")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("digest must be immutable")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,126}", tag):
        raise ValueError("invalid target tag")
    if signer not in SIGNERS:
        raise ValueError("unexpected keyless signer")
    if not re.fullmatch(r"[0-9]{1,20}", operation_id):
        raise ValueError("operation ID must be a GitHub run ID")

    signature_tag = "sha256-" + digest.removeprefix("sha256:") + ".sig"
    mount = {"name": "registry-auth", "mountPath": "/auth", "readOnly": True}
    security = {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "runAsGroup": 65532,
        "capabilities": {"drop": ["ALL"]},
    }
    copy_base = {
        "image": ORAS_IMAGE,
        "command": ["/bin/oras"],
        "env": [{"name": "DOCKER_CONFIG", "value": "/auth"}],
        "volumeMounts": [mount],
        "securityContext": security,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": JOB_NAME,
            "namespace": NAMESPACE,
            "labels": {"delivery.re8ch.com/component": "artifact-mirror"},
            "annotations": {"delivery.re8ch.com/operation-id": operation_id},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 900,
            "ttlSecondsAfterFinished": 300,
            "template": {
                "metadata": {"labels": {"delivery.re8ch.com/component": "artifact-mirror"}},
                "spec": {
                    "serviceAccountName": "release-artifact-mirror",
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "hostNetwork": True,
                    "dnsPolicy": "ClusterFirstWithHostNet",
                    "nodeSelector": {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"},
                    "securityContext": {"fsGroup": 65532, "fsGroupChangePolicy": "OnRootMismatch"},
                    "imagePullSecrets": [{"name": "release-artifact-mirror-auth"}],
                    "initContainers": [
                        {
                            **copy_base,
                            "name": "copy-artifact",
                            "args": ["cp", "--recursive", f"{source}@{digest}", f"{target}:{tag}"],
                        },
                        {
                            **copy_base,
                            "name": "copy-signature",
                            "args": ["cp", f"{source}:{signature_tag}", f"{target}:{signature_tag}"],
                        },
                    ],
                    "containers": [{
                        "name": "verify-signature",
                        "image": COSIGN_IMAGE,
                        "command": ["/ko-app/cosign"],
                        "args": ["verify", "--certificate-identity", signer,
                                 "--certificate-oidc-issuer", ISSUER, f"{target}@{digest}"],
                        "env": [{"name": "DOCKER_CONFIG", "value": "/auth"},
                                {"name": "HOME", "value": "/tmp"}],
                        "volumeMounts": [mount, {"name": "tmp", "mountPath": "/tmp"}],
                        "securityContext": security,
                    }],
                    "volumes": [
                        {"name": "registry-auth", "secret": {"secretName": "release-artifact-mirror-auth",
                                                               "items": [{"key": ".dockerconfigjson", "path": "config.json"}],
                                                               "defaultMode": 288}},
                        {"name": "tmp", "emptyDir": {}},
                    ],
                },
            },
        },
    }


def fresh_actions_token() -> str | None:
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        return None
    parsed = urllib.parse.urlparse(request_url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".actions.githubusercontent.com"):
        raise RuntimeError("unexpected Actions OIDC endpoint")
    separator = "&" if "?" in request_url else "?"
    url = request_url + separator + urllib.parse.urlencode({"audience": PUBLISHER_AUDIENCE})
    request = urllib.request.Request(url, headers={"Authorization": "bearer " + request_token})
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response).get("value")
    if not token:
        raise RuntimeError("Actions OIDC endpoint returned no token")
    return token


def read_job() -> tuple[dict | None, str]:
    command = ["kubectl", "get", "job/" + JOB_NAME, "-n", NAMESPACE, "-o", "json"]
    token = fresh_actions_token()
    if token:
        command.extend(["--token", token])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        return None, result.stderr.strip()
    return json.loads(result.stdout), ""


def run(job: dict) -> None:
    subprocess.run(["kubectl", "delete", "job", JOB_NAME, "-n", NAMESPACE,
                    "--ignore-not-found", "--wait=true"], check=True)
    subprocess.run(["kubectl", "create", "-f", "-"], input=json.dumps(job), text=True, check=True)
    deadline = time.monotonic() + 900
    last_error = ""
    while time.monotonic() < deadline:
        current, error = read_job()
        if current is not None:
            status = current.get("status", {})
            conditions = {item.get("type"): item.get("status") for item in status.get("conditions", [])}
            if conditions.get("Failed") == "True" or status.get("failed"):
                raise RuntimeError("protected Harbor mirror Job failed")
            if conditions.get("Complete") == "True":
                if status.get("succeeded") != 1:
                    raise RuntimeError("mirror Job did not record exactly one success")
                return
        else:
            last_error = error
        time.sleep(15)
    suffix = f": {last_error}" if last_error else ""
    raise RuntimeError("timed out waiting for protected Harbor mirror Job" + suffix)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--signer", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    job = manifest(args.source, args.target, args.digest, args.tag, args.signer, args.operation_id)
    if args.apply:
        run(job)
    else:
        print(json.dumps(job, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
