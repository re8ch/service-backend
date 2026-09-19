from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_k3s_helm_installers_are_pinned_to_local_egress_nodes():
    directory = ROOT / "platform/scheduling-fabric/governance/policies"
    kustomization = yaml.safe_load((directory / "kustomization.yaml").read_text())
    assert "helm-installer-placement.yaml" in kustomization["resources"]

    documents = list(
        yaml.safe_load_all((directory / "helm-installer-placement.yaml").read_text())
    )
    policy, binding = documents
    name = "place-k3s-helm-installers-on-local-egress.re8ch.com"
    assert policy["apiVersion"] == "admissionregistration.k8s.io/v1"
    assert policy["kind"] == "MutatingAdmissionPolicy"
    assert policy["metadata"]["name"] == name
    assert policy["spec"]["failurePolicy"] == "Fail"
    rule = policy["spec"]["matchConstraints"]["resourceRules"][0]
    assert rule == {
        "apiGroups": [""],
        "apiVersions": ["v1"],
        "operations": ["CREATE"],
        "resources": ["pods"],
    }
    condition = policy["spec"]["matchConditions"][0]["expression"]
    assert "object.metadata.namespace == 'kube-system'" in condition
    assert "'helmcharts.helm.cattle.io/chart' in object.metadata.labels" in condition
    assert "'batch.kubernetes.io/job-name' in object.metadata.labels" in condition
    assert "object.metadata.labels['batch.kubernetes.io/job-name'].startsWith('helm-install-')" in condition
    mutation = policy["spec"]["mutations"][0]
    assert mutation["patchType"] == "JSONPatch"
    expression = mutation["jsonPatch"]["expression"]
    assert "path: '/spec/affinity'" in expression
    assert "key: 'kubernetes.io/hostname'" in expression
    assert "values: ['r640', 'overseas-la']" in expression
    assert "path: '/spec/hostNetwork'" in expression
    assert "value: true" in expression
    assert "path: '/spec/dnsPolicy'" in expression
    assert "value: 'ClusterFirstWithHostNet'" in expression

    assert binding["apiVersion"] == "admissionregistration.k8s.io/v1"
    assert binding["kind"] == "MutatingAdmissionPolicyBinding"
    assert binding["spec"]["policyName"] == name
    assert binding["spec"]["matchResources"]["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "kube-system"
    }
