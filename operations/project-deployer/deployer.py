#!/usr/bin/env python3
"""Validate and render external frontend/backend projects for Flux promotion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas/project-deployment.schema.json"
LABEL = "deployments.re8ch.com/project"


def load_and_validate(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text())
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()), format_checker=jsonschema.FormatChecker()).validate(document)
    names = [component["name"] for component in document["spec"]["components"]]
    if len(names) != len(set(names)):
        raise ValueError("component names must be unique")
    sensitive = ("password", "token", "secret", "privatekey", "private_key", "api_key")
    if any(any(marker in key.lower() for marker in sensitive) for component in document["spec"]["components"] for key in component.get("env", {})):
        raise ValueError("sensitive environment values are forbidden; use secretRefs")
    return document


def metadata(name: str, project: str) -> dict[str, Any]:
    return {"name": name, "namespace": project, "labels": {LABEL: project}}


def render(document: dict[str, Any]) -> list[dict[str, Any]]:
    project = document["metadata"]["name"]
    namespace = f"project-{project}"
    owner = document["spec"]["owner"]
    environment = document["spec"]["environment"]
    labels = {LABEL: project, "app.kubernetes.io/part-of": project, "deployments.re8ch.com/environment": environment}
    result: list[dict[str, Any]] = [{
        "apiVersion": "v1", "kind": "Namespace",
        "metadata": {"name": namespace, "labels": labels, "annotations": {"deployments.re8ch.com/owner": owner}},
    }]
    for component in document["spec"]["components"]:
        name = component["name"]
        pod_labels = {**labels, "app.kubernetes.io/name": name, "app.kubernetes.io/component": component["type"]}
        container: dict[str, Any] = {
            "name": name, "image": component["image"], "imagePullPolicy": "IfNotPresent",
            "ports": [{"name": "http", "containerPort": component["port"]}],
            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "runAsNonRoot": True, "capabilities": {"drop": ["ALL"]}},
            "readinessProbe": {"httpGet": {"path": component.get("healthPath", "/healthz"), "port": "http"}, "initialDelaySeconds": 5},
            "livenessProbe": {"httpGet": {"path": component.get("healthPath", "/healthz"), "port": "http"}, "initialDelaySeconds": 15},
            "resources": component.get("resources", {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}}),
        }
        if component.get("env"):
            container["env"] = [{"name": key, "value": value} for key, value in sorted(component["env"].items())]
        if component.get("secretRefs"):
            container["envFrom"] = [{"secretRef": {"name": ref}} for ref in component["secretRefs"]]
        result.extend([
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata(name, namespace), "spec": {"replicas": component.get("replicas", 2), "selector": {"matchLabels": {"app.kubernetes.io/name": name}}, "template": {"metadata": {"labels": pod_labels}, "spec": {"automountServiceAccountToken": False, "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}}, "containers": [container]}}}},
            {"apiVersion": "v1", "kind": "Service", "metadata": metadata(name, namespace), "spec": {"selector": {"app.kubernetes.io/name": name}, "ports": [{"name": "http", "port": 80, "targetPort": "http"}]}},
            {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": metadata(name, namespace), "spec": {"maxUnavailable": 1, "selector": {"matchLabels": {"app.kubernetes.io/name": name}}}},
        ])
        if component.get("host"):
            gateway = document["spec"].get("gateway", {"name": "public", "namespace": "gateway-system"})
            result.append({"apiVersion": "gateway.networking.k8s.io/v1", "kind": "HTTPRoute", "metadata": metadata(name, namespace), "spec": {"parentRefs": [{"name": gateway["name"], "namespace": gateway["namespace"]}], "hostnames": [component["host"]], "rules": [{"matches": [{"path": {"type": "PathPrefix", "value": component.get("pathPrefix", "/")}}], "backendRefs": [{"name": name, "port": 80}]}]}})
    result.append({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": metadata("restricted-ingress", namespace), "spec": {"podSelector": {}, "policyTypes": ["Ingress"], "ingress": [{"from": [{"podSelector": {}}, {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": document["spec"].get("gateway", {}).get("namespace", "gateway-system")}}}]}]}})
    return result


def dump(resources: list[dict[str, Any]]) -> str:
    return yaml.safe_dump_all(resources, sort_keys=False, explicit_start=True)


def publish(document: dict[str, Any], root: Path) -> dict[str, str]:
    name = document["metadata"]["name"]
    declaration = root / "deployments" / f"{name}.yaml"
    project_dir = root / "platform" / "external-projects"
    rendered = project_dir / "projects" / f"{name}.yaml"
    kustomization = project_dir / "kustomization.yaml"
    if declaration.exists() or rendered.exists():
        raise ValueError(f"project {name!r} already exists; update it through review instead of overwriting")
    config = yaml.safe_load(kustomization.read_text())
    resource = f"projects/{name}.yaml"
    if resource in config.get("resources", []):
        raise ValueError(f"Kustomize resource {resource!r} already exists")
    declaration.parent.mkdir(parents=True, exist_ok=True)
    rendered.parent.mkdir(parents=True, exist_ok=True)
    declaration.write_text(yaml.safe_dump(document, sort_keys=False))
    rendered.write_text(dump(render(document)))
    config.setdefault("resources", []).append(resource)
    config["resources"].sort()
    kustomization.write_text(yaml.safe_dump(config, sort_keys=False))
    return {"published": name, "declaration": str(declaration), "rendered": str(rendered)}


def update(document: dict[str, Any], root: Path) -> dict[str, str]:
    name = document["metadata"]["name"]
    declaration = root / "deployments" / f"{name}.yaml"
    rendered = root / "platform" / "external-projects" / "projects" / f"{name}.yaml"
    if not declaration.exists() or not rendered.exists():
        raise ValueError(f"project {name!r} is not published; use publish first")
    declaration.write_text(yaml.safe_dump(document, sort_keys=False))
    rendered.write_text(dump(render(document)))
    return {"updated": name, "declaration": str(declaration), "rendered": str(rendered)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "render", "publish", "update"))
    parser.add_argument("file", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        document = load_and_validate(args.file)
        resources = render(document)
        if args.command == "validate":
            print(json.dumps({"valid": True, "project": document["metadata"]["name"], "resources": len(resources)}))
        elif args.command == "render":
            print(dump(resources), end="")
        elif args.command == "publish":
            print(json.dumps(publish(document, args.root)))
        else:
            print(json.dumps(update(document, args.root)))
        return 0
    except (OSError, ValueError, yaml.YAMLError, jsonschema.ValidationError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
