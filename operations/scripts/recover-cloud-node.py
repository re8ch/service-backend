#!/usr/bin/env python3
"""Recover an unreachable inventory node through its declared cloud control plane."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_node(path: Path, name: str) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text())
    for item in document.get("nodes", document.get("items", [])):
        if item.get("metadata", {}).get("name") == name:
            return item
    raise ValueError(f"node not found in inventory: {name}")


def reboot_command(node: dict[str, Any]) -> list[str]:
    spec = node["spec"]
    cloud = spec["cloud"]
    recovery = spec.get("recovery", {}).get("cloudReboot", {})
    if not recovery.get("enabled"):
        raise ValueError("cloud reboot is not enabled for this node")

    provider = cloud["provider"]
    product = cloud["product"]
    profile = cloud["cliProfile"]
    region = cloud["regionId"]
    instance = cloud["instanceId"]
    endpoint = cloud["apiEndpoint"]
    force = bool(recovery.get("force"))

    if (provider, product) == ("aliyun", "ecs"):
        return [
            "aliyun", "ecs", "RebootInstance", "--profile", profile,
            "--endpoint", endpoint,
            "--RegionId", region, "--InstanceId", instance,
            "--ForceStop", str(force).lower(),
        ]
    if (provider, product) == ("aliyun", "swas"):
        return [
            "aliyun", "swas-open", "reboot-instance", "--profile", profile,
            "--endpoint", endpoint,
            "--biz-region-id", region, "--instance-id", instance,
        ]
    if (provider, product) == ("tencent", "lighthouse"):
        command = [
            "tccli", "lighthouse", "RebootInstances", "--profile", profile,
            "--region", region, "--InstanceIds", json.dumps([instance]),
        ]
        if force:
            command += ["--StopType", "HARD"]
        return command
    if (provider, product) == ("tencent", "cvm"):
        return [
            "tccli", "cvm", "RebootInstances", "--profile", profile,
            "--region", region, "--InstanceIds", json.dumps([instance]),
            "--ForceReboot", str(force).lower(), "--StopType", "HARD",
        ]
    raise ValueError(f"unsupported cloud reboot target: {provider}/{product}")


def run(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)


def kube_ready(name: str, kubeconfig: str | None) -> bool:
    command = ["kubectl"]
    if kubeconfig:
        command += ["--kubeconfig", kubeconfig]
    command += ["get", "node", name, "-o", "json"]
    try:
        result = run(command, 10)
        if result.returncode:
            return False
        node = json.loads(result.stdout)
        return any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in node.get("status", {}).get("conditions", [])
        )
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return False


def ssh_reachable(address: str, ssh_config: str | None) -> bool:
    command = ["ssh"]
    if ssh_config:
        command += ["-F", ssh_config]
    command += [
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-o", "ConnectionAttempts=1", "--", address, "true",
    ]
    try:
        return run(command, 12).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def probe(node: dict[str, Any], kubeconfig: str | None, ssh_config: str | None) -> dict[str, bool]:
    name = node["metadata"]["name"]
    return {
        "kubeReady": kube_ready(name, kubeconfig),
        "sshReachable": ssh_reachable(node["spec"]["address"], ssh_config),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node")
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory/nodes.yaml")
    parser.add_argument("--kubeconfig")
    parser.add_argument("--ssh-config")
    parser.add_argument("--execute", action="store_true", help="perform the reboot after the dual-unreachable gate")
    parser.add_argument("--wait-seconds", type=int, default=300)
    args = parser.parse_args()

    try:
        node = load_node(args.inventory, args.node)
        command = reboot_command(node)
    except (KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False))
        return 2

    health = probe(node, args.kubeconfig, args.ssh_config)
    result = {
        "node": args.node,
        "cloud": node["spec"]["cloud"],
        "health": health,
        "command": command,
        "execute": args.execute,
    }
    if health["kubeReady"] or health["sshReachable"]:
        result.update(status="refused", reason="dual-unreachable health gate not satisfied")
        print(json.dumps(result, ensure_ascii=False))
        return 2
    if not args.execute:
        result.update(status="planned", reason="pass --execute to perform cloud reboot")
        print(json.dumps(result, ensure_ascii=False))
        return 0

    reboot = run(command, 30)
    if reboot.returncode:
        result.update(status="failed", stderr=reboot.stderr.strip())
        print(json.dumps(result, ensure_ascii=False))
        return reboot.returncode

    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        time.sleep(10)
        health = probe(node, args.kubeconfig, args.ssh_config)
        if health["kubeReady"] or health["sshReachable"]:
            result.update(status="recovered", health=health)
            print(json.dumps(result, ensure_ascii=False))
            return 0
    result.update(status="rebooted-not-reachable", health=health)
    print(json.dumps(result, ensure_ascii=False))
    return 3


if __name__ == "__main__":
    sys.exit(main())
