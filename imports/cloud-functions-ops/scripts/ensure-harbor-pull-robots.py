#!/usr/bin/env python3
"""Create Harbor pull-only robot accounts and optional K8S pull secrets."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class RobotSecret:
    project: str
    robot_name: str
    username: str
    token: str
    secret_name: str


def credentials(registry: str) -> tuple[str, str]:
    user = os.environ.get("HARBOR_USERNAME")
    password = os.environ.get("HARBOR_PASSWORD")
    if user and password:
        return user, password
    helper = os.environ.get("DOCKER_CREDENTIAL_HELPER", "docker-credential-osxkeychain")
    try:
        raw = subprocess.check_output([helper, "get"], input=registry.encode(), stderr=subprocess.DEVNULL)
        data = json.loads(raw)
        return data["Username"], data["Secret"]
    except Exception as exc:  # pragma: no cover - local credential fallback
        raise SystemExit(f"Cannot read Harbor credentials from env or {helper}: {exc}") from exc


def request(method: str, url: str, user: str, password: str, data: object | None = None) -> object:
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body)
    req.method = method
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as res:
        raw = res.read()
        if not raw:
            return {}
        return json.loads(raw)


def paged(url: str, user: str, password: str) -> list[object]:
    items: list[object] = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        batch = request("GET", f"{url}{sep}page={page}&page_size=100", user, password)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def find_robot(base: str, name: str, user: str, password: str) -> dict | None:
    robots = paged(f"{base}/robots", user, password)
    for robot in robots:
        if robot.get("name") == name:
            return robot
    return None


def delete_robot(base: str, robot_id: int, user: str, password: str) -> None:
    request("DELETE", f"{base}/robots/{robot_id}", user, password)


def create_robot(base: str, project: str, robot: str, user: str, password: str) -> tuple[str, str]:
    secret = secrets.token_urlsafe(48)
    payload = {
        "name": robot,
        "secret": secret,
        "description": f"pull-only robot for tenant project {project}",
        "disable": False,
        "duration": -1,
        "level": "project",
        "permissions": [
            {
                "kind": "project",
                "namespace": project,
                "access": [{"resource": "repository", "action": "pull"}],
            }
        ],
    }
    created = request("POST", f"{base}/robots", user, password, payload)
    username = str(created.get("name") or f"robot${robot}")
    token = str(created.get("token") or created.get("secret") or secret)
    if not token:
        raise SystemExit(f"Harbor did not return a token for robot {username}")
    return username, token


def ensure_robot(base: str, project: str, user: str, password: str) -> RobotSecret:
    suffix = os.environ.get("HARBOR_ROBOT_SUFFIX", "pull-20260602b")
    robot = f"{project}-{suffix}"
    secret = f"harbor-{project}-pull"
    existing = find_robot(base, f"robot${robot}", user, password) or find_robot(base, robot, user, password)
    if existing:
        delete_robot(base, int(existing["id"]), user, password)
    username, token = create_robot(base, project, robot, user, password)
    return RobotSecret(project=project, robot_name=robot, username=username, token=token, secret_name=secret)


def apply_k8s_secret(registry: str, namespace: str, secret: RobotSecret, kubectl: str) -> None:
    auth = base64.b64encode(f"{secret.username}:{secret.token}".encode()).decode()
    dockercfg = {
        "auths": {
            registry: {
                "username": secret.username,
                "password": secret.token,
                "auth": auth,
            }
        }
    }
    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": secret.secret_name, "namespace": namespace},
        "type": "kubernetes.io/dockerconfigjson",
        "data": {
            ".dockerconfigjson": base64.b64encode(
                json.dumps(dockercfg, separators=(",", ":")).encode()
            ).decode()
        },
    }
    subprocess.run(
        kubectl.split() + ["apply", "-f", "-"],
        input=json.dumps(manifest).encode(),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Harbor pull-only robots for tenant projects.")
    parser.add_argument("--registry", default=os.environ.get("REGISTRY_HOST", "registry.re8ch.com"))
    parser.add_argument("--kube-namespace", default=os.environ.get("KUBE_NAMESPACE", "nuclio"))
    parser.add_argument("--kubectl", default=os.environ.get("KUBECTL_CMD", "kubectl"))
    parser.add_argument("--apply-k8s", action="store_true")
    parser.add_argument("projects", nargs="*", default=["qianwen", "ledger", "anycam", "bench", "functions-shared"])
    args = parser.parse_args()

    user, password = credentials(args.registry)
    base = f"https://{args.registry}/api/v2.0"
    for project in args.projects:
        secret = ensure_robot(base, project, user, password)
        if args.apply_k8s:
            apply_k8s_secret(args.registry, args.kube_namespace, secret, args.kubectl)
        print(f"created {secret.username} -> secret/{secret.secret_name}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
