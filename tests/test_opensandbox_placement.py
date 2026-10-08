from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "clusters/re8ch/components/opensandbox"


def test_sandbox_control_and_workloads_use_existing_r640_hostname():
    controller = yaml.safe_load((SANDBOX / "controller-values.yaml").read_text())
    resources = [resource for resource in yaml.safe_load_all((SANDBOX / "platform.yaml").read_text()) if resource]
    server = next(resource for resource in resources if resource["kind"] == "Deployment" and resource["metadata"]["name"] == "opensandbox-server")
    config = next(resource for resource in resources if resource["kind"] == "ConfigMap" and resource["metadata"]["name"] == "opensandbox-server-config")
    sandbox = yaml.safe_load(config["data"]["batchsandbox-template.yaml"])

    expected = {"kubernetes.io/hostname": "r640"}
    assert controller["controller"]["nodeSelector"] == expected
    assert server["spec"]["template"]["spec"]["nodeSelector"] == expected
    assert sandbox["spec"]["template"]["spec"]["nodeSelector"] == expected
