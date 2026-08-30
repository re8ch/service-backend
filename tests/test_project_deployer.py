import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("project_deployer", ROOT / "operations/project-deployer/deployer.py")
deployer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployer)


def document():
    digest = "a" * 64
    return {"apiVersion": "deployments.re8ch.com/v1alpha1", "kind": "ProjectDeployment", "metadata": {"name": "demo"}, "spec": {"owner": "team", "environment": "staging", "components": [{"name": "web", "type": "frontend", "image": f"registry.test/web@sha256:{digest}", "port": 8080, "host": "demo.test"}, {"name": "api", "type": "backend", "image": f"registry.test/api@sha256:{digest}", "port": 3000, "secretRefs": ["api-config"]}]}}


def test_render_is_namespaced_and_has_frontend_and_backend():
    resources = deployer.render(document())
    assert resources[0]["metadata"]["name"] == "project-demo"
    deployments = [item for item in resources if item["kind"] == "Deployment"]
    assert {item["metadata"]["name"] for item in deployments} == {"web", "api"}
    assert all(item["metadata"]["namespace"] == "project-demo" for item in deployments)
    assert next(item for item in resources if item["kind"] == "HTTPRoute")["spec"]["hostnames"] == ["demo.test"]


def test_secret_reference_never_renders_secret_value():
    rendered = deployer.dump(deployer.render(document()))
    assert "api-config" in rendered
    assert "kind: Secret" not in rendered
    assert "automountServiceAccountToken: false" in rendered


def test_duplicate_components_are_rejected(tmp_path):
    import yaml
    value = document()
    value["spec"]["components"].append(value["spec"]["components"][0])
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(value))
    with pytest.raises(ValueError, match="unique"):
        deployer.load_and_validate(path)


def test_publish_writes_declaration_render_and_kustomization(tmp_path):
    import yaml
    project_dir = tmp_path / "platform/external-projects"
    project_dir.mkdir(parents=True)
    (project_dir / "kustomization.yaml").write_text("apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n")
    result = deployer.publish(document(), tmp_path)
    assert Path(result["declaration"]).exists()
    assert Path(result["rendered"]).exists()
    config = yaml.safe_load((project_dir / "kustomization.yaml").read_text())
    assert config["resources"] == ["projects/demo.yaml"]
    with pytest.raises(ValueError, match="already exists"):
        deployer.publish(document(), tmp_path)
    changed = document()
    changed["spec"]["components"][0]["replicas"] = 3
    deployer.update(changed, tmp_path)
    assert "replicas: 3" in Path(result["rendered"]).read_text()
