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


def test_rollout_preserves_ready_replicas_while_new_image_pulls():
    deployment = next(item for item in resources() if item["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0


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


def test_shared_ssh_configuration_paths_exist_in_readonly_runtime():
    deployment = next(item for item in resources() if item["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    mounts = {m["mountPath"]: m for m in pod["containers"][0]["volumeMounts"]}
    volumes = {v["name"]: v for v in pod["volumes"]}
    mount = mounts["/etc/re8ch-ai-operations"]
    assert mount["readOnly"] is True
    assert volumes[mount["name"]]["projected"]["sources"][0]["secret"] == {
        "name": "ai-operations-ssh-config",
        "items": [{"key": "ssh-config", "path": "ssh-config"}],
    }
    hosts = mounts["/var/lib/re8ch-ai-operations"]
    assert not hosts.get("readOnly", False)
    assert "emptyDir" in volumes[hosts["name"]]
    assert volumes["infrastructure-contracts"]["secret"]["secretName"] == "infrastructure-contracts"
