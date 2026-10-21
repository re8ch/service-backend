import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "operations/scripts/recover-cloud-node.py"
SPEC = importlib.util.spec_from_file_location("recover_cloud_node", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def node(provider, product, *, force=True):
    return {
        "metadata": {"name": "example"},
        "spec": {
            "cloud": {
                "provider": provider,
                "product": product,
                "cliProfile": "profile",
                "regionId": "region",
                "instanceId": "instance",
                "apiEndpoint": "api.example.test",
            },
            "recovery": {"cloudReboot": {"enabled": True, "force": force}},
        },
    }


def test_aliyun_ecs_uses_force_reboot():
    assert MODULE.reboot_command(node("aliyun", "ecs")) == [
        "aliyun", "ecs", "RebootInstance", "--profile", "profile",
        "--endpoint", "api.example.test",
        "--RegionId", "region", "--InstanceId", "instance", "--ForceStop", "true",
    ]


def test_aliyun_swas_uses_simple_application_server_api():
    assert MODULE.reboot_command(node("aliyun", "swas")) == [
        "aliyun", "swas-open", "reboot-instance", "--profile", "profile",
        "--endpoint", "api.example.test",
        "--biz-region-id", "region", "--instance-id", "instance",
    ]


def test_tencent_lighthouse_uses_hard_reboot():
    assert MODULE.reboot_command(node("tencent", "lighthouse")) == [
        "tccli", "lighthouse", "RebootInstances", "--profile", "profile",
        "--region", "region", "--InstanceIds", '["instance"]', "--StopType", "HARD",
    ]


def test_inventory_cloud_recovery_targets_are_complete():
    expected = {
        "muaya-ecs", "muaya-lightweight", "domain-ex", "suxunji", "zan", "vm-20-12-ubuntu",
    }
    for name in expected:
        item = MODULE.load_node(ROOT / "inventory/nodes.yaml", name)
        assert item["spec"]["recovery"]["cloudReboot"]["enabled"] is True
        assert MODULE.reboot_command(item)
