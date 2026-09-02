import hashlib
import io
from pathlib import Path
import tarfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
HEADLAMP = ROOT / "platform/headlamp"


def test_all_declared_sidebar_plugins_are_rendered_into_headlamp():
    deployment = (HEADLAMP / "deployment.yaml").read_text()
    kustomization = yaml.safe_load((HEADLAMP / "kustomization.yaml").read_text())
    generators = {item["name"]: item for item in kustomization["configMapGenerator"]}
    expected = {
        "headlamp-public-edge-v011": "re8ch-headlamp-public-edge-0.1.1.bundle",
        "headlamp-mihomos-cluster-v011": "re8ch-headlamp-mihomos-cluster-0.1.1.bundle",
    }
    for configmap, filename in expected.items():
        assert generators[configmap]["files"] == [f"plugin.tar.gz=assets/{filename}"]
        bundle = HEADLAMP / "assets" / filename
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        assert f"echo '{digest}  /tmp/plugin.tar.gz'" in deployment
        with tarfile.open(fileobj=io.BytesIO(bundle.read_bytes()), mode="r:gz") as archive:
            names = archive.getnames()
        assert any(name.endswith("/main.js") for name in names)
        assert any(name.endswith("/package.json") for name in names)


def test_plugin_runtime_is_pod_local_and_complete():
    deployment = yaml.safe_load((HEADLAMP / "deployment.yaml").read_text())
    pod = deployment["spec"]["template"]["spec"]
    init_names = {item["name"] for item in pod["initContainers"]}
    assert {
        "install-birfucate-browser",
        "install-advanced-fabric-plugin",
        "install-among-clusters-plugin",
        "install-public-edge-plugin",
        "install-mihomos-cluster-plugin",
        "install-flux-plugin",
    } <= init_names
    plugins = next(volume for volume in pod["volumes"] if volume["name"] == "plugins")
    assert plugins == {"name": "plugins", "emptyDir": {}}
