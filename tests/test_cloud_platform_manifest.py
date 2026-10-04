from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def resources():
    return list(yaml.safe_load_all((ROOT / "platform/cloud-platform/runtime.yaml").read_text()))


def test_cloud_platform_uses_the_owned_core_service_plane():
    deployment = next(item for item in resources() if item["kind"] == "Deployment")
    affinity = deployment["spec"]["template"]["spec"]["affinity"]
    expressions = affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"]

    assert expressions == [{
        "key": "scheduling.re8ch.com/service-plane",
        "operator": "In",
        "values": ["core"],
    }]
    anti_affinity = affinity["podAntiAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"]
    assert anti_affinity == [{
        "weight": 100,
        "podAffinityTerm": {
            "labelSelector": {"matchLabels": {"app": "re8ch-cloud-platform"}},
            "topologyKey": "kubernetes.io/hostname",
        },
    }]


def test_cloud_platform_keeps_one_replica_during_voluntary_disruption():
    pdb = next(item for item in resources() if item["kind"] == "PodDisruptionBudget")

    assert pdb["spec"]["minAvailable"] == 1
    assert pdb["spec"]["selector"]["matchLabels"] == {"app": "re8ch-cloud-platform"}


def test_rollout_can_replace_pods_with_old_required_anti_affinity():
    deployment = next(item for item in resources() if item["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 2


def test_http_acceptance_has_bounded_private_temporary_storage_with_readonly_root():
    from pathlib import Path
    import yaml
    root = Path(__file__).parents[1]
    deployment = next(doc for doc in yaml.safe_load_all((root / 'platform/cloud-platform/runtime.yaml').read_text()) if doc and doc['kind'] == 'Deployment')
    pod = deployment['spec']['template']['spec']
    container = pod['containers'][0]
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    assert container['resources']['limits']['memory'] == '512Mi'
    volume = next(item for item in pod['volumes'] if item['name'] == 'http-acceptance-tmp')
    assert volume['emptyDir'] == {'medium': 'Memory', 'sizeLimit': '16Mi'}
    assert {'name': 'http-acceptance-tmp', 'mountPath': '/tmp'} in container['volumeMounts']
