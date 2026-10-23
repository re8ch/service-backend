#!/usr/bin/env python3
"""Fail-closed Ceph acceptance using status plus non-destructive smoke state."""
from __future__ import annotations

import argparse
import json
import subprocess


def command(*argv: str) -> dict:
    completed = subprocess.run(argv, check=True, text=True, capture_output=True, timeout=120)
    return json.loads(completed.stdout)


def require(value: bool, message: str) -> None:
    if not value:
        raise SystemExit(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True, choices=["rook-ceph", "ceph-csi-drivers", "rook-ceph-cluster"])
    args = parser.parse_args()
    evidence: dict[str, object] = {"release": args.release}
    deployments = command("kubectl", "get", "deployment", "-n", "rook-ceph", "-o", "json")["items"]
    required = {"rook-ceph-operator", "ceph-csi-controller-manager", "rook-ceph.rbd.csi.ceph.com-ctrlplugin", "rook-ceph.cephfs.csi.ceph.com-ctrlplugin"}
    by_name = {item["metadata"]["name"]: item for item in deployments}
    for name in required:
        item = by_name.get(name)
        require(item is not None, f"missing deployment {name}")
        require(item.get("status", {}).get("readyReplicas", 0) >= item.get("spec", {}).get("replicas", 1), f"deployment not ready: {name}")
    daemonsets = command("kubectl", "get", "daemonset", "-n", "rook-ceph", "-o", "json")["items"]
    node_plugins = [item for item in daemonsets if item["metadata"]["name"] in {"rook-ceph.rbd.csi.ceph.com-nodeplugin", "rook-ceph.cephfs.csi.ceph.com-nodeplugin"}]
    require(len(node_plugins) == 2, "both CSI node plugins are required")
    for item in node_plugins:
        status = item.get("status", {})
        require(status.get("numberReady", 0) == status.get("desiredNumberScheduled", -1), f'node plugin not ready: {item["metadata"]["name"]}')
    ceph = command("kubectl", "-n", "rook-ceph", "exec", "deploy/rook-ceph-tools", "--", "ceph", "status", "--format", "json")
    require(ceph.get("monmap", {}).get("num_mons") == len(ceph.get("quorum", [])) == 1, "single MON quorum is not healthy")
    osd = ceph.get("osdmap", {})
    require(osd.get("num_osds", 0) > 0 and osd.get("num_up_osds") == osd.get("num_osds") and osd.get("num_in_osds") == osd.get("num_osds"), "OSDs are not all up/in")
    pg = ceph.get("pgmap", {})
    clean = sum(item.get("count", 0) for item in pg.get("pgs_by_state", []) if item.get("state_name") == "active+clean")
    require(pg.get("num_pgs", 0) > 0 and clean == pg.get("num_pgs"), "PGs are not all active+clean")
    for smoke_name in ("rbd-smoke", "cephfs-smoke"):
        pvc = command("kubectl", "get", "persistentvolumeclaim", smoke_name, "-n", "ceph-storage-smoke", "-o", "json")
        smoke = command("kubectl", "get", "deployment", smoke_name, "-n", "ceph-storage-smoke", "-o", "json")
        require(pvc.get("status", {}).get("phase") == "Bound", f"{smoke_name} PVC is not Bound")
        require(smoke.get("status", {}).get("readyReplicas", 0) >= 1, f"{smoke_name} read/write smoke is not ready")
    evidence.update({"monQuorum": 1, "osdUpIn": osd["num_osds"], "pgActiveClean": clean,
                     "csiNodePlugins": len(node_plugins), "rbdSmoke": "ready", "cephfsSmoke": "ready"})
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
