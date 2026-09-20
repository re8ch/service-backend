from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_external_observe_can_read_sanitized_kube_metrics_only():
    documents = list(
        yaml.safe_load_all(
            (ROOT / "clusters/re8ch/components/toolhive/external-observe-rbac.yaml").read_text()
        )
    )
    role = next(item for item in documents if item["kind"] == "ClusterRole")
    metric_rule = next(rule for rule in role["rules"] if rule["apiGroups"] == ["metrics.k8s.io"])
    assert metric_rule == {
        "apiGroups": ["metrics.k8s.io"],
        "resources": ["nodes", "pods"],
        "verbs": ["get", "list"],
    }
