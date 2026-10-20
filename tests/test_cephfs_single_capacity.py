from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
ROOK = ROOT / "clusters/re8ch/components/rook-ceph"


def load(path):
    return yaml.safe_load(path.read_text())


def test_ceph_daemon_topology_and_pools():
    values = load(ROOK / "cluster-values.yaml")
    spec = values["cephClusterSpec"]
    assert spec["mon"]["count"] == 1
    assert spec["mgr"]["count"] == 1
    for daemon in ("mon", "mgr"):
        expression = spec["placement"][daemon]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"][0]["matchExpressions"][0]
        assert expression == {
            "key": "storage.re8ch.com/coordinator",
            "operator": "In",
            "values": ["true"],
        }

    filesystem = values["cephFileSystems"][0]
    fs_spec = filesystem["spec"]
    assert fs_spec["metadataPool"]["deviceClass"] == "r640-hdd"
    assert fs_spec["metadataPool"]["replicated"] == {
        "size": 1,
        "requireSafeReplicaSize": False,
    }
    assert fs_spec["metadataServer"]["activeCount"] == 1
    assert fs_spec["metadataServer"]["activeStandby"] is False
    pools = {pool["name"]: pool for pool in fs_spec["dataPools"]}
    assert pools["data0"]["deviceClass"] == "r640-hdd"
    assert pools["data0"]["replicated"] == {
        "size": 1,
        "requireSafeReplicaSize": False,
    }
    assert pools["r640"]["deviceClass"] == "r640-hdd"
    assert pools["r640"]["replicated"] == {
        "size": 1,
        "requireSafeReplicaSize": False,
    }
    assert filesystem["storageClass"]["name"] == "cephfs-pilot"
    assert filesystem["storageClass"]["labels"] == {
        "storage.re8ch.com/availability-class": "retired",
        "storage.re8ch.com/replication": "1",
    }
    assert filesystem["storageClass"]["annotations"][
        "storage.re8ch.com/deprecated"
    ] == "true"


def test_explicit_devices_and_no_discovery():
    storage = load(ROOK / "cluster-values.yaml")["cephClusterSpec"]["storage"]
    assert storage["useAllNodes"] is False
    assert storage["useAllDevices"] is False
    nodes = {node["name"]: node for node in storage["nodes"]}
    assert set(nodes) == {"r640"}
    assert all(len(node["devices"]) == 1 for node in nodes.values())


def test_a1_b1_are_static_node_local_pools():
    classes = list(yaml.safe_load_all((ROOK / "local-storage-classes.yaml").read_text()))
    by_name = {item["metadata"]["name"]: item for item in classes}
    assert set(by_name) == {"local-a1", "local-b1"}
    for name, node in (("local-a1", "a1-wsl-zt"), ("local-b1", "b1-wsl-zt")):
        storage_class = by_name[name]
        assert storage_class["provisioner"] == "kubernetes.io/no-provisioner"
        assert storage_class["reclaimPolicy"] == "Retain"
        assert storage_class["volumeBindingMode"] == "WaitForFirstConsumer"
        assert storage_class["metadata"]["labels"][
            "storage.re8ch.com/failure-domain"
        ] == node


def test_b1_is_the_tainted_storage_coordinator():
    nodes = list(yaml.safe_load_all((ROOK / "storage-node-labels.yaml").read_text()))
    by_name = {node["metadata"]["name"]: node for node in nodes}
    assert by_name["b1-wsl-zt"]["metadata"]["labels"][
        "storage.re8ch.com/coordinator"
    ] == "true"
    assert by_name["b1-wsl-zt"]["spec"]["taints"] == [
        {
            "key": "storage.re8ch.com/coordinator",
            "value": "true",
            "effect": "NoSchedule",
        }
    ]
    assert "storage.re8ch.com/coordinator" not in by_name["a1-wsl-zt"][
        "metadata"
    ]["labels"]
    assert "storage.re8ch.com/coordinator" not in by_name["r640"]["metadata"][
        "labels"
    ]
    assert by_name["a1-wsl-zt"]["metadata"]["labels"][
        "scheduling.re8ch.com/capability-storage-ceph-osd"
    ] == "false"
    assert by_name["b1-wsl-zt"]["metadata"]["labels"][
        "scheduling.re8ch.com/capability-storage-ceph-osd"
    ] == "false"


def test_csi_controllers_are_singleton_on_b1_and_node_plugins_tolerate_b1():
    values = load(ROOK / "csi-drivers-values.yaml")
    assert values["operatorConfig"]["driverSpecDefaults"]["controllerPlugin"][
        "replicas"
    ] == 1
    for driver_name in ("rbd", "cephfs"):
        driver = values["drivers"][driver_name]
        assert driver["controllerPlugin"]["replicas"] == 1
        expression = driver["controllerPlugin"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"][0]["matchExpressions"][0]
        assert expression["key"] == "storage.re8ch.com/coordinator"
        assert expression["values"] == ["true"]
        toleration_keys = {
            item["key"] for item in driver["nodePlugin"]["tolerations"]
        }
        assert "storage.re8ch.com/coordinator" in toleration_keys


def test_legacy_cephfs_claims_are_denied():
    policy = list(
        yaml.safe_load_all(
            (ROOK / "replicated-storage-whitelist-policy.yaml").read_text()
        )
    )[0]
    validation = policy["spec"]["validations"][0]
    assert "cephfs-re8ch" in validation["expression"]
    assert "cephfs-pilot" in validation["expression"]
    assert "replicated-approved" not in validation["expression"]
    assert "retired for new claims" in validation["message"]
