from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = ROOT / "platform/scheduling-fabric/governance"


def docs(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_ontology_quota_exception_is_scoped_to_one_managed_namespace():
    policy = next(
        item for item in docs(GOVERNANCE / "policies/kyverno.yaml")
        if item["metadata"]["name"] == "generate-tenant-resource-boundaries"
    )
    rules = {rule["name"]: rule for rule in policy["spec"]["rules"]}
    assert "generate-ontology-resource-quota" not in rules
    default = rules["generate-default-resource-quota"]
    assert "exclude" not in default
    assert default["match"]["any"][0]["resources"]["selector"]["matchLabels"] == {
        "tenancy.re8ch.com/managed": "true"
    }
    assert default["generate"]["name"] == "tenant-governance-defaults"
    assert default["generate"]["data"]["spec"]["hard"]["limits.cpu"] == (
        "{{ (request.object.metadata.name == 're8ch-ontology-microscope' && '12') || '8' }}"
    )


def test_controller_releases_are_pinned_and_initially_non_disruptive():
    bootstrap = docs(GOVERNANCE / "controllers.yaml")
    releases = {
        item["metadata"]["name"]: item
        for item in docs(GOVERNANCE / "controllers/releases.yaml")
        if item["kind"] == "HelmRelease"
    }
    assert releases["descheduler"]["spec"]["chart"]["spec"]["version"] == "0.36.0"
    assert releases["descheduler"]["spec"]["values"]["cmdOptions"]["dry-run"] is True
    assert releases["descheduler"]["spec"]["values"]["deschedulerPolicy"]["maxNoOfPodsToEvictTotal"] == 5
    descheduler_plugins = releases["descheduler"]["spec"]["values"]["deschedulerPolicy"]["profiles"][0][
        "pluginConfig"
    ]
    taint_args = next(
        plugin["args"] for plugin in descheduler_plugins if plugin["name"] == "RemovePodsViolatingNodeTaints"
    )
    assert taint_args["includePreferNoSchedule"] is True
    assert taint_args["includedTaints"] == ["node-role.kubernetes.io/control-plane=true"]
    assert set(taint_args["namespaces"]["exclude"]) >= {
        "cert-manager",
        "kueue-system",
        "kyverno",
        "observability-system",
        "opentelemetry-operator",
        "scheduler-system",
    }
    assert releases["kyverno"]["spec"]["chart"]["spec"]["version"] == "3.9.1"
    assert releases["kueue"]["spec"]["chart"]["spec"] == {
        "chart": "platform/scheduling-fabric/vendor/kueue",
        "reconcileStrategy": "ChartVersion",
        "sourceRef": {"kind": "GitRepository", "name": "service-backend"},
    }
    kueue_manager = releases["kueue"]["spec"]["values"]["controllerManager"]
    assert kueue_manager["replicas"] == 2
    assert kueue_manager["hostNetwork"] is True
    assert kueue_manager["dnsPolicy"] == "ClusterFirstWithHostNet"
    assert kueue_manager["metricsPort"] == 17443
    assert kueue_manager["manager"]["env"] == [
        {"name": "KUBERNETES_SERVICE_HOST", "value": "127.0.0.1"},
        {"name": "KUBERNETES_SERVICE_PORT", "value": "6443"},
    ]
    assert kueue_manager["strategy"]["rollingUpdate"] == {
        "maxSurge": 0,
        "maxUnavailable": 1,
    }
    assert kueue_manager["nodeSelector"] == {
        "node-role.kubernetes.io/control-plane": "true",
    }
    assert kueue_manager["tolerations"] == [
        {
            "key": "node-role.kubernetes.io/control-plane",
            "operator": "Exists",
            "effect": "NoSchedule",
        }
    ]
    manager_config = yaml.safe_load(
        releases["kueue"]["spec"]["values"]["managerConfig"]["controllerManagerConfigYaml"]
    )
    assert manager_config["managedJobsNamespaceSelector"] == {
        "matchLabels": {"kueue.x-k8s.io/managed": "true"}
    }
    assert manager_config["metrics"]["bindAddress"] == ":17443"

    chart = yaml.safe_load(
        (GOVERNANCE.parent / "vendor/kueue/Chart.yaml").read_text()
    )
    assert chart["version"] == "0.19.5-re8ch.3"
    manager_template = (
        GOVERNANCE.parent / "vendor/kueue/templates/manager/manager.yaml"
    ).read_text()
    assert "containerPort: {{ .Values.controllerManager.metricsPort }}" in manager_template

    assert not any(item["metadata"]["name"] == "kueue-official" for item in bootstrap)
    provenance = yaml.safe_load(
        (GOVERNANCE.parent / "vendor/kueue/RE8CH_PROVENANCE.yaml").read_text()
    )
    assert provenance["upstream"]["tag"] == "v0.19.5"
    assert provenance["upstream"]["assetSHA256"] == "1c97fb124bc18e5b9124e2c84292a0ea9045f2e9e1c0d4648494a4d1311048e0"

    child_kustomizations = {
        item["metadata"]["name"]: item
        for item in bootstrap
        if item["kind"] == "Kustomization"
    }
    assert child_kustomizations["scheduling-governance-controllers"]["spec"]["wait"] is True
    assert child_kustomizations["scheduling-governance-policies"]["spec"]["dependsOn"] == [
        {"name": "scheduling-governance-controllers"}
    ]


def test_policies_start_in_audit_and_kueue_is_explicit_opt_in():
    policies = docs(GOVERNANCE / "policies/kyverno.yaml")
    assert all(item["spec"]["validationFailureAction"] == "Audit" for item in policies)
    assert "node-role.kubernetes.io/control-plane" in (GOVERNANCE / "policies/kyverno.yaml").read_text()
    privileged = next(
        item for item in policies if item["metadata"]["name"] == "audit-privileged-workloads"
    )
    assert "request.object.spec.volumes || `[]`" in yaml.safe_dump(privileged)
    resource_bounds = next(
        item for item in policies if item["metadata"]["name"] == "audit-workload-resource-bounds"
    )
    conditions = resource_bounds["spec"]["rules"][0]["validate"]["foreach"][0][
        "deny"
    ]["conditions"]["any"]
    keys = [condition["key"] for condition in conditions]
    assert "{{ contains(keys(element.resources.requests || `{}`), 'cpu') }}" in keys
    assert "{{ contains(keys(element.resources.limits || `{}`), 'memory') }}" in keys
    assert all(condition["value"] is False for condition in conditions)

    queues = docs(GOVERNANCE / "policies/kueue.yaml")
    local_queues = [item for item in queues if item["kind"] == "LocalQueue"]
    assert {(item["metadata"]["namespace"], item["metadata"]["name"]) for item in local_queues} == {
        ("tenant-qwen-runtime", "tenant-batch"),
        ("artchais-default", "tenant-batch"),
    }

    namespaces = docs(GOVERNANCE / "policies/namespace-labels.yaml")
    namespace_labels = {
        item["metadata"]["name"]: item["metadata"].get("labels", {}) for item in namespaces
    }
    for item in namespaces:
        assert item["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"
    for name in ("kube-system", "flux-system", "rook-ceph", "observability-system"):
        assert namespace_labels[name]["scheduling.re8ch.com/control-plane-allowed"] == "true"
    for name in ("tenant-qwen-runtime", "artchais-default"):
        assert namespace_labels[name]["tenancy.re8ch.com/managed"] == "true"
        assert namespace_labels[name]["kueue.x-k8s.io/managed"] == "true"


def test_control_plane_reservations_are_declared_without_noexecute():
    configs = {
        name: yaml.safe_load((ROOT / "clusters/re8ch/components/ops" / name).read_text())
        for name in ("r640-k3s-server-config.yaml", "a1-wsl2-k3s-server-config.yaml", "b1-wsl2-k3s-server-config.yaml")
    }
    assert "max-pods=256" in configs["r640-k3s-server-config.yaml"]["kubelet-arg"]
    for name in ("a1-wsl2-k3s-server-config.yaml", "b1-wsl2-k3s-server-config.yaml"):
        assert "max-pods=128" in configs[name]["kubelet-arg"]
    for config in configs.values():
        args = config["kubelet-arg"]
        assert any(value.startswith("kube-reserved=") for value in args)
        assert any(value.startswith("system-reserved=") for value in args)
        assert "enforce-node-allocatable=pods" in args
        assert not any("NoExecute" in value for value in args)


def test_advanced_fabric_controller_runs_on_always_on_control_node():
    release = list(
        yaml.safe_load_all(
            (ROOT / "platform/advanced-fabric/release.yaml").read_text()
        )
    )
    helm_release = next(item for item in release if item and item.get("kind") == "HelmRelease")
    selector = helm_release["spec"]["values"]["advancedFabric"]["controller"]["nodeSelector"]
    assert selector == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}


def test_prefer_taint_stage_is_not_blocked_by_existing_pending_pods():
    rollout = (
        ROOT / "operations/scripts/rollout-control-plane-isolation.sh"
    ).read_text()
    assert '"$pending" != "0" && "$mode" == "enforce"' in rollout
    assert '"$pending" != "0" && "$mode" != "observe"' not in rollout
    assert "for _ in 1 2 3; do" in rollout
    assert 'if [[ "$apply" == "--apply" ]]; then' in rollout
    assert '"${dry_run[@]}"' not in rollout


def test_dynamic_scheduler_uses_production_metrics_and_hard_spread():
    scheduler = (ROOT / "platform/scheduling-fabric/resources/scheduler.yaml").read_text()
    controller = (ROOT / "platform/scheduling-fabric/resources/controller.yaml").read_text()
    expected = "vmselect-re8ch-metrics.observability-system.svc.cluster.local:8481/select/0/prometheus"
    assert expected in scheduler
    assert expected in controller
    assert "whenUnsatisfiable: DoNotSchedule" in scheduler
    assert "replicas: 0" in scheduler


def test_placement_classes_are_the_single_portable_placement_contract():
    resources = docs(ROOT / "platform/scheduling-fabric/resources/placement.yaml")
    classes = {item["metadata"]["name"]: item for item in resources}
    assert set(classes) == {
        "control-plane-critical",
        "service-core-local", "service-core-ab", "service-core-r640",
        "online-reliable", "batch-opportunistic", "gpu-rocm",
        "stateful-ceph", "function-execution", "public-edge", "egress",
        "node-bootstrap", "node-serving-agent",
    }
    assert all(item["spec"]["topologyKey"] == "scheduling.re8ch.com/failure-domain" for item in classes.values())
    assert all(item["spec"]["failurePolicy"] == "FailClosed" for item in classes.values())
    assert {classes[name]["spec"]["distributionPolicy"] for name in (
        "service-core-local", "service-core-ab", "service-core-r640"
    )} == {"ObserveOnly"}
    assert classes["service-core-local"]["spec"]["nodeSelector"] == {
        "scheduling.re8ch.com/pool-service-core-local": "true",
        "scheduling.re8ch.com/network-serving": "true",
    }
    assert classes["service-core-ab"]["spec"]["minEligibleNodes"] == 1
    assert classes["service-core-r640"]["spec"]["minFailureDomains"] == 1
    assert classes["control-plane-critical"]["spec"] == {
        "workloadMode": "ControlPlaneService",
        "nodeSelector": {"node-role.kubernetes.io/control-plane": "true"},
        "allowControlPlane": True,
        "topologyKey": "scheduling.re8ch.com/failure-domain",
        "minEligibleNodes": 1,
        "minFailureDomains": 1,
        "distributionPolicy": "ObserveOnly",
        "failurePolicy": "FailClosed",
        "requiredCapabilities": ["local-kubernetes-api"],
        "resourceFlavorName": "placement-control-plane-critical",
    }
    for name in ("service-core-local", "service-core-ab", "service-core-r640"):
        spec = classes[name]["spec"]
        assert spec["nodeSelector"][f"scheduling.re8ch.com/pool-{spec['nodePool']}"] == "true"
    preferred = [item for item in classes.values() if item["spec"]["distributionPolicy"] == "PreferSpread"]
    assert preferred
    assert all(item["spec"]["whenUnsatisfiable"] == "ScheduleAnyway" for item in preferred)
    assert all(item["spec"]["nodeTaintsPolicy"] == "Honor" for item in preferred)
    assert classes["node-bootstrap"]["spec"]["workloadMode"] == "NodeBootstrapDaemon"
    assert classes["node-serving-agent"]["spec"]["workloadMode"] == "NodeServingDaemon"
    assert all(item["spec"]["resourceFlavorName"].startswith("placement-") for item in classes.values())
    policies = docs(GOVERNANCE / "policies/kyverno.yaml")
    assert any(item["metadata"]["name"] == "audit-placement-class-contract" for item in policies)
    queues = docs(GOVERNANCE / "policies/kueue.yaml")
    check = next(item for item in queues if item["kind"] == "AdmissionCheck")
    assert check["spec"]["controllerName"] == "scheduling.re8ch.com/placement-admission"
    strategies = [item["spec"]["admissionChecksStrategy"] for item in queues if item["kind"] == "ClusterQueue"]
    assert all(strategy["admissionChecks"] == [{"name": "re8ch-placement", "onFlavors": ["default"]}] for strategy in strategies)
    flavors = {item["metadata"]["name"] for item in queues if item["kind"] == "ResourceFlavor"}
    assert {"default", *(item["spec"]["resourceFlavorName"] for item in classes.values())} <= flavors


def test_first_unready_cohort_uses_only_reviewed_classes():
    cohort = yaml.safe_load((ROOT / "platform/scheduling-fabric/workload-cohorts.yaml").read_text())
    classes = {
        item["metadata"]["name"]
        for item in docs(ROOT / "platform/scheduling-fabric/resources/placement.yaml")
    }
    workloads = cohort["spec"]["workloads"]
    assert len(workloads) == 40
    assert {item["placementClass"] for item in workloads} <= classes
    assert cohort["spec"]["enforcement"] == "Audit"


def test_penrose_source_changes_roll_the_controller():
    kustomization = yaml.safe_load(
        (ROOT / "platform/scheduling-fabric/resources/kustomization.yaml").read_text()
    )
    assert kustomization["configMapGenerator"][0]["name"] == "penrose-controller-source"
    assert not kustomization.get("generatorOptions", {}).get("disableNameSuffixHash", False)


def test_observability_rollouts_do_not_deadlock_on_hard_anti_affinity():
    namespace = docs(ROOT / "platform/observability/bootstrap/namespace.yaml")[0]
    labels = namespace["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "privileged"
    assert labels["pod-security.kubernetes.io/audit"] == "restricted"
    assert labels["pod-security.kubernetes.io/warn"] == "restricted"

    resources = docs(ROOT / "platform/observability/production/collectors.yaml")
    by_kind = {item["kind"]: item for item in resources}
    for kind in ("VMAgent", "VMAlert"):
        spec = by_kind[kind]["spec"]
        assert spec["updateStrategy"] == "RollingUpdate"
        assert spec["rollingUpdate"] == {"maxSurge": 0, "maxUnavailable": 1}
        assert spec["priorityClassName"] == "system-cluster-critical"
        assert spec["tolerations"] == [
            {
                "key": "node-role.kubernetes.io/control-plane",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        ]


def test_access_dns_keeps_a_bounded_r640_local_resolver():
    resources = docs(ROOT / "clusters/re8ch/components/personal-access/access-dns.yaml")
    deployments = {
        item["metadata"]["name"]: item
        for item in resources
        if item["kind"] == "Deployment"
    }
    mosdns = deployments["mosdns"]["spec"]
    assert mosdns["replicas"] == 3
    assert mosdns["strategy"]["rollingUpdate"] == {
        "maxSurge": 0,
        "maxUnavailable": 1,
    }
    mosdns_pod = mosdns["template"]
    assert mosdns_pod["metadata"]["labels"]["scheduling.re8ch.com/control-plane-allowed"] == "true"
    assert "requiredDuringSchedulingIgnoredDuringExecution" in mosdns_pod["spec"]["affinity"]["podAntiAffinity"]
    assert mosdns_pod["spec"]["priorityClassName"] == "system-cluster-critical"

    adguard_pod = deployments["access-adguard"]["spec"]["template"]
    assert adguard_pod["metadata"]["labels"]["scheduling.re8ch.com/control-plane-allowed"] == "true"
    assert adguard_pod["spec"]["priorityClassName"] == "system-cluster-critical"


def test_advfab_external_dns_has_service_source_rbac_and_stable_worker_placement():
    resources = docs(ROOT / "clusters/re8ch/components/public-exposure/external-dns-advfab.yaml")
    role = next(item for item in resources if item["kind"] == "ClusterRole")
    core_rule = next(rule for rule in role["rules"] if rule["apiGroups"] == [""])
    assert set(core_rule["resources"]) == {"pods", "services"}
    assert set(core_rule["verbs"]) == {"get", "list", "watch"}
    node_rule = next(
        rule
        for rule in role["rules"]
        if rule["apiGroups"] == [""] and rule["resources"] == ["nodes"]
    )
    assert set(node_rule["verbs"]) == {"list", "watch"}
    endpoint_slice_rule = next(
        rule for rule in role["rules"] if rule["apiGroups"] == ["discovery.k8s.io"]
    )
    assert endpoint_slice_rule["resources"] == ["endpointslices"]
    assert set(endpoint_slice_rule["verbs"]) == {"get", "list", "watch"}

    deployment = next(item for item in resources if item["kind"] == "Deployment")
    assert deployment["spec"]["template"]["spec"]["nodeSelector"] == {
        "scheduling.re8ch.com/provider": "tencent",
        "scheduling.re8ch.com/reliability-class": "stable",
    }


def test_scheduling_governance_alerts_on_kueue_manager_restarts():
    resources = docs(GOVERNANCE / "policies/observability.yaml")
    scrapes = {
        item["metadata"]["name"]: item
        for item in resources
        if item["kind"] == "VMServiceScrape"
    }
    assert scrapes["kyverno"]["spec"]["namespaceSelector"] == {
        "matchNames": ["kyverno"]
    }
    assert scrapes["kyverno"]["spec"]["endpoints"][0]["port"] == "metrics-port"
    rule = next(item for item in resources if item["kind"] == "VMRule")
    alerts = {
        item["alert"]: item
        for group in rule["spec"]["groups"]
        for item in group["rules"]
        if "alert" in item
    }
    assert "Re8chKueueManagerRestarting" in alerts
    assert "kueue-system" in alerts["Re8chKueueManagerRestarting"]["expr"]
    assert "Re8chKyvernoMetricsMissing" in alerts
    assert "kyverno_info" in alerts["Re8chKyvernoMetricsMissing"]["expr"]
    assert "Re8chSchedulingPolicyAuditViolation" in alerts
    assert "kyverno_policy_results_total" in alerts["Re8chSchedulingPolicyAuditViolation"]["expr"]
    assert "Re8chVLAgentDroppingLogs" in alerts
    assert "vlagent_remotewrite_packets_dropped_total" in alerts["Re8chVLAgentDroppingLogs"]["expr"]


def test_vlagent_writes_to_the_native_victorialogs_ingest_endpoint():
    resources = docs(ROOT / "platform/observability/production/logs.yaml")
    agent = next(item for item in resources if item["kind"] == "VLAgent")
    assert agent["spec"]["remoteWrite"] == [
        {
            "url": "http://vlsingle-re8ch-logs.observability-system.svc.cluster.local:9428/insert/native",
            "maxDiskUsage": "1GiB",
        }
    ]


def test_root_includes_governance_but_no_node_taint_object():
    kustomization = yaml.safe_load((ROOT / "platform/scheduling-fabric/kustomization.yaml").read_text())
    assert "governance/controllers.yaml" in kustomization["resources"]
    rendered_text = "\n".join(path.read_text() for path in (ROOT / "platform/scheduling-fabric").rglob("*.yaml"))
    assert "effect: NoExecute" not in rendered_text
