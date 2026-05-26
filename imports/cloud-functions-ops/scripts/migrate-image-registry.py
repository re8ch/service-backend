#!/usr/bin/env python3
"""Move existing Nuclio functions from one registry hostname to another.

This script intentionally preserves the live function env, replicas, triggers,
and scheduling. It only rewrites image references plus Nuclio registry metadata,
then waits for the Deployment to roll out.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import yaml


KUBECTL_CMD = os.environ.get("KUBECTL_CMD", "kubectl")


def command(prefix: list[str]) -> list[str]:
    return shlex.split(KUBECTL_CMD) + prefix


def kctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command(args), check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def kctl_input(args: list[str], payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command(args), input=payload, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def exists(namespace: str, kind: str, name: str) -> bool:
    proc = subprocess.run(command(["-n", namespace, "get", kind, name]), text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def path_exists(obj: Any, parts: list[str]) -> bool:
    cur = obj
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
            continue
        if isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                return False
            if 0 <= idx < len(cur):
                cur = cur[idx]
                continue
        return False
    return True


def json_pointer(parts: list[str]) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def patch_set(doc: Any, parts: list[str], value: Any) -> dict[str, Any]:
    return {"op": "replace" if path_exists(doc, parts) else "add", "path": json_pointer(parts), "value": value}


def registry_from_image(image: str) -> str:
    parts = image.split("/")
    if len(parts) >= 3 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        return "/".join(parts[:2])
    if len(parts) >= 2 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        return parts[0]
    return ""


def target_image(image: str, from_host: str, to_host: str) -> str:
    if image.startswith(from_host + "/"):
        return to_host + image[len(from_host):]
    return image


def update_function_config(raw: str, image: str, build_spec: dict[str, Any], run_registry: str) -> str:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    data["image"] = image
    data["build"] = dict(build_spec)
    if run_registry:
        data["runRegistry"] = run_registry
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def update_processor_yaml(raw: str, image: str, build_spec: dict[str, Any], run_registry: str, annotations: dict[str, str]) -> str:
    data = yaml.safe_load(raw) if raw else {}
    if not isinstance(data, dict):
        data = {}
    metadata = data.setdefault("metadata", {})
    metadata.setdefault("annotations", {}).update(annotations)
    spec = data.setdefault("spec", {})
    spec["image"] = image
    spec["build"] = dict(build_spec)
    if run_registry:
        spec["runRegistry"] = run_registry
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def image_from_deployment(namespace: str, function: str) -> str:
    deployment = f"nuclio-{function}"
    if not exists(namespace, "deployment", deployment):
        return ""
    raw = kctl(["-n", namespace, "get", "deployment", deployment, "-o", "json"]).stdout
    spec = json.loads(raw)
    containers = spec.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    return containers[0].get("image", "") if containers else ""


@dataclass
class MigrationResult:
    name: str
    old_image: str
    new_image: str
    changed: bool
    ready: str


def migrate_one(namespace: str, function: str, from_host: str, to_host: str, rollout_timeout: str, dry_run: bool) -> MigrationResult:
    nf_raw = kctl(["-n", namespace, "get", "nucliofunction", function, "-o", "json"]).stdout
    nf = json.loads(nf_raw)
    spec = nf.get("spec", {})
    old_image = spec.get("image") or image_from_deployment(namespace, function)
    if not old_image:
        return MigrationResult(function, "", "", False, "no-image")
    new_image = target_image(old_image, from_host, to_host)
    if old_image == new_image:
        return MigrationResult(function, old_image, new_image, False, "already")

    run_registry = registry_from_image(new_image)
    build_spec: dict[str, Any] = {"codeEntryType": "image"}
    if run_registry:
        build_spec["registry"] = run_registry
        build_spec["noBaseImagesPull"] = True
    annotations = {
        "saas.re8ch.com/registry-migrated-from": old_image,
        "saas.re8ch.com/code-image": new_image,
    }

    if dry_run:
        return MigrationResult(function, old_image, new_image, True, "dry-run")

    nf_patch = {
        "metadata": {"annotations": annotations},
        "spec": {
            "image": new_image,
            "build": {
                **build_spec,
                "onbuildImage": None,
                "baseImage": None,
                "functionConfigPath": None,
                "timestamp": None,
            },
        },
    }
    if run_registry:
        nf_patch["spec"]["runRegistry"] = run_registry
    kctl_input(["-n", namespace, "patch", "nucliofunction", function, "--type", "merge", "--patch-file", "/dev/stdin"], json.dumps(nf_patch))

    deployment = f"nuclio-{function}"
    if exists(namespace, "deployment", deployment):
        deploy_raw = kctl(["-n", namespace, "get", "deployment", deployment, "-o", "json"]).stdout
        deploy = json.loads(deploy_raw)
        deploy_annotations = dict(deploy.get("metadata", {}).get("annotations", {}) or {})
        deploy_annotations.update(annotations)
        deploy_annotations["nuclio.io/function-config"] = update_function_config(
            deploy_annotations.get("nuclio.io/function-config", ""),
            new_image,
            build_spec,
            run_registry,
        )
        template_meta = deploy.get("spec", {}).get("template", {}).setdefault("metadata", {})
        template_labels = dict(template_meta.get("labels", {}) or {})
        service_name = deployment
        if exists(namespace, "service", service_name):
            service = json.loads(kctl(["-n", namespace, "get", "service", service_name, "-o", "json"]).stdout)
            template_labels.update(service.get("spec", {}).get("selector", {}) or {})
        template_annotations = dict(template_meta.get("annotations", {}) or {})
        template_annotations.update(annotations)
        json_patch = [
            {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": new_image},
            patch_set(deploy, ["metadata", "annotations"], deploy_annotations),
            patch_set(deploy, ["spec", "template", "metadata", "labels"], template_labels),
            patch_set(deploy, ["spec", "template", "metadata", "annotations"], template_annotations),
        ]

        cm_name = deployment
        for volume in deploy.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", []):
            config_map_name = volume.get("configMap", {}).get("name", "")
            if config_map_name in (deployment, function):
                cm_name = config_map_name
                break
        if exists(namespace, "configmap", cm_name):
            cm = json.loads(kctl(["-n", namespace, "get", "configmap", cm_name, "-o", "json"]).stdout)
            processor_yaml = cm.get("data", {}).get("processor.yaml")
            if processor_yaml:
                updated = update_processor_yaml(processor_yaml, new_image, build_spec, run_registry, annotations)
                kctl_input(
                    ["-n", namespace, "patch", "configmap", cm_name, "--type", "merge", "--patch-file", "/dev/stdin"],
                    json.dumps({"data": {"processor.yaml": updated}}),
                )

        kctl_input(["-n", namespace, "patch", "deployment", deployment, "--type", "json", "--patch-file", "/dev/stdin"], json.dumps(json_patch))
        subprocess.run(command(["-n", namespace, "rollout", "status", f"deployment/{deployment}", f"--timeout={rollout_timeout}"]), check=True)

    ready = "unknown"
    if exists(namespace, "deployment", deployment):
        status = json.loads(kctl(["-n", namespace, "get", "deployment", deployment, "-o", "json"]).stdout).get("status", {})
        ready = f"{status.get('readyReplicas', 0)}/{status.get('replicas', 0)}"
    return MigrationResult(function, old_image, new_image, True, ready)


def function_names(namespace: str) -> list[str]:
    payload = json.loads(kctl(["-n", namespace, "get", "nucliofunctions", "-o", "json"]).stdout)
    return sorted(item["metadata"]["name"] for item in payload.get("items", []))


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Nuclio function image registry hostnames without rebuilding code.")
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "nuclio"))
    parser.add_argument("--from-host", default=os.environ.get("FROM_REGISTRY_HOST", "registry.ynzs.com"))
    parser.add_argument("--to-host", default=os.environ.get("TO_REGISTRY_HOST", "registry.re8ch.com"))
    parser.add_argument("--rollout-timeout", default=os.environ.get("ROLLOUT_TIMEOUT", "600s"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("functions", nargs="*")
    args = parser.parse_args()

    names = args.functions or function_names(args.namespace)
    results = []
    for name in names:
        try:
            results.append(migrate_one(args.namespace, name, args.from_host, args.to_host, args.rollout_timeout, args.dry_run))
        except subprocess.CalledProcessError as exc:
            print(exc.stderr or exc.stdout, file=sys.stderr)
            raise

    for result in results:
        flag = "changed" if result.changed else "skipped"
        print(f"{flag}\t{result.name}\t{result.ready}\t{result.old_image}\t{result.new_image}")


if __name__ == "__main__":
    main()
