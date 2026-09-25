from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "platform/hyperswitch/release.yaml"
ROUTES = ROOT / "platform/hyperswitch/public-route.yaml"


def objects():
    return list(yaml.safe_load_all(RELEASE.read_text()))


def test_hyperswitch_is_active_internal_sandbox_with_sops_values():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    assert release["spec"]["suspend"] is False
    assert release["spec"]["targetNamespace"] == "payments"
    assert release["spec"]["valuesFrom"] == [
        {
            "kind": "Secret",
            "name": "hyperswitch-runtime-values",
            "valuesKey": "values.yaml",
        }
    ]
    values = release["spec"]["values"]
    assert values["hyperswitch-web"]["enabled"] is False
    assert values["hyperswitch-control-center"]["enabled"] is True
    assert values["hyperswitch-control-center"]["ingress"]["enabled"] is False
    features = values["hyperswitch-control-center"]["config"]["default"]["features"]
    assert features["totp"] == "true"
    assert features["email"] == "false"
    assert features["sample_data"] == "false"
    assert values["hyperswitch-app"]["server"]["ingress"]["enabled"] is False
    assert values["hyperswitch-app"]["server"]["run_env"] == "sandbox"


def test_control_center_route_uses_service_port_not_container_port():
    routes = list(yaml.safe_load_all(ROUTES.read_text()))
    route = next(item for item in routes if item.get("metadata", {}).get("name") == "hyperswitch-control-center-public")
    assert route["spec"]["rules"][0]["backendRefs"][0]["port"] == 80


def test_admin_bootstrap_uses_injected_service_coordinates():
    job = yaml.safe_load((ROOT / "platform/hyperswitch/bootstrap-admin-job.yaml").read_text())
    assert job["metadata"]["name"] == "hyperswitch-bootstrap-admin-v11"
    command = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
    assert "HYPERSWITCH_HYPERSWITCH_SERVER_SERVICE_HOST" in command
    assert "HYPERSWITCH_HYPERSWITCH_SERVER_SERVICE_PORT" in command
    assert "/user/tenant_signup" in command
    assert '"$base_url/accounts"' in command
    assert "already exists" in command
    env = job["spec"]["template"]["spec"]["containers"][0]["env"]
    admin_key = next(item for item in env if item["name"] == "ADMIN_API_KEY")
    assert admin_key["valueFrom"]["secretKeyRef"]["key"] == "ROUTER__SECRETS__ADMIN_API_KEY"


def test_hyperswitch_disables_upstream_root_telemetry_hooks():
    release = yaml.safe_load_all(RELEASE.read_text())
    helm_release = next(item for item in release if item.get("kind") == "HelmRelease")
    assert helm_release["spec"]["timeout"] == "10m"
    assert helm_release["spec"]["install"]["disableHooks"] is True
    assert helm_release["spec"]["upgrade"]["disableHooks"] is True
    patch = helm_release["spec"]["postRenderers"][0]["kustomize"]["patches"][0]
    assert patch["target"]["name"] == "hyperswitch-hyperswitch-server"
    assert "ROUTER__ANALYTICS__CLICKHOUSE__PASSWORD" in patch["patch"]
    assert "$patch: delete" in patch["patch"]


def test_hyperswitch_singleton_upgrades_without_surge():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    patches = release["spec"]["postRenderers"][0]["kustomize"]["patches"]
    singleton_deployments = {
        "hyperswitch-hyperswitch-server",
        "hyperswitch-superposition",
        "hyperswitch-control-center",
    }
    strategies = {
        item["target"]["name"]: yaml.safe_load(item["patch"])["spec"]["strategy"]
        for item in patches
        if item["target"]["name"] in singleton_deployments
    }
    assert strategies == {
        name: {
            "type": "RollingUpdate",
            "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
        }
        for name in singleton_deployments
    }


def test_hyperswitch_router_uses_re8ch_connector_image_by_digest():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    patches = release["spec"]["postRenderers"][0]["kustomize"]["patches"]
    server_patch = next(
        item for item in patches
        if item["target"]["name"] == "hyperswitch-hyperswitch-server"
    )
    rendered_patch = yaml.safe_load(server_patch["patch"])
    router = next(
        container
        for container in rendered_patch["spec"]["template"]["spec"]["containers"]
        if container["name"] == "hyperswitch-router"
    )
    assert router["image"] == (
        "registry.re8ch.com/hyperswitch/hyperswitch-router@"
        "sha256:10a106c6a144633613529959a6a2f5edcd54fd5786e1ca3e722e27d87c958eb6"
    )
    assert "@sha256:" in router["image"]
    assert not router["image"].endswith(":latest")
    pod_spec = rendered_patch["spec"]["template"]["spec"]
    assert pod_spec["imagePullSecrets"] == [{"name": "harbor-hyperswitch-admin"}]


def test_hyperswitch_vendor_workloads_use_baseline_and_r640_rbd():
    namespace = yaml.safe_load(
        (ROOT / "platform/hyperswitch/namespace.yaml").read_text()
    )
    labels = namespace["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "baseline"
    assert labels["pod-security.kubernetes.io/audit"] == "restricted"
    values = next(item for item in objects() if item["kind"] == "HelmRelease")["spec"]["values"]
    app = values["hyperswitch-app"]
    assert app["postgresql"]["enabled"] is False
    assert app["externalPostgresql"]["enabled"] is True
    assert app["externalPostgresql"]["primary"]["host"] == (
        "pg-re8ch-primary.pigsty-external.svc.cluster.local"
    )
    assert app["externalPostgresql"]["readOnly"]["enabled"] is True
    assert app["externalPostgresql"]["readOnly"]["auth"]["username"] == (
        "hyperswitch_app_ro"
    )
    assert app["redis"]["master"]["persistence"]["storageClass"] == "rbd-r640"
    assert values["hyperswitch-ucs"]["enabled"] is False


def test_hyperswitch_redis_owner_is_enrolled_without_pod_template_mutation():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    patches = release["spec"]["postRenderers"][0]["kustomize"]["patches"]
    redis_patch = next(
        item for item in patches
        if item["target"]["kind"] == "StatefulSet"
        and item["target"]["name"] == "hyperswitch-redis-master"
    )
    rendered = yaml.safe_load(redis_patch["patch"])
    assert rendered["metadata"]["labels"] == {
        "scheduling.re8ch.com/placement-managed": "true"
    }
    assert "spec" not in rendered


def test_superposition_is_explicit_and_has_no_plaintext_secret():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    app = release["spec"]["values"]["hyperswitch-app"]
    assert app["superposition"]["enabled"] is True
    assert app["superpositionDB"]["enable"] is True
    assert app["superpositionFallback"]["source"] == "configMap"
    assert app["superposition"]["configs"]["app_env"] == "DEV"
    assert app["superposition"]["configs"]["db_host"] == (
        "pg-re8ch-primary.pigsty-external.svc.cluster.local:5432"
    )
    assert app["server"]["configs"]["superposition"]["backup_file_path"].endswith(
        "superposition_seed.toml"
    )
    cors = app["server"]["configs"]["cors"]
    assert cors["wildcard_origin"] is False
    assert "https://pay.service.re8ch.com" in cors["origins"]
    connectors = app["server"]["configs"]["connectors"]
    assert connectors["alipaycn"]["base_url"].startswith(
        "https://openapi-sandbox.dl.alipaydev.com/"
    )
    assert connectors["wechatpaycn"]["base_url"] == "https://api.mch.weixin.qq.com"
    secretish_keys = {"db" + "_password", "superposition" + "_token", "api" + "_key", "client" + "_secret"}

    def visit(value):
        if isinstance(value, dict):
            assert secretish_keys.isdisjoint(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(release)


def test_superposition_bootstrap_artifacts_are_vendored():
    kustomization = yaml.safe_load(
        (ROOT / "platform/hyperswitch/kustomization.yaml").read_text()
    )
    assert kustomization["generatorOptions"]["disableNameSuffixHash"] is True
    files = {
        item
        for generator in kustomization["configMapGenerator"]
        for item in generator["files"]
    }
    assert any("hyperswitch-v1.126.0-superposition_seed.toml" in item for item in files)
    assert any("superposition-v0.112.0.sql" in item for item in files)
    assert kustomization["resources"].count("superposition-schema-job.yaml") == 1


def test_chart_is_version_pinned_and_latest_is_forbidden():
    release = next(item for item in objects() if item["kind"] == "HelmRelease")
    assert release["spec"]["chart"]["spec"]["version"] == "0.2.26"
    assert "latest" not in RELEASE.read_text().lower()


def test_public_routes_use_dedicated_tls_listeners():
    routes = list(yaml.safe_load_all(ROUTES.read_text()))
    http_routes = [route for route in routes if route["kind"] == "HTTPRoute"]
    assert {route["spec"]["hostnames"][0] for route in http_routes} == {
        "pay.service.re8ch.com",
        "pay-api.re8ch.com",
    }
    for route in http_routes:
        assert len(route["spec"]["parentRefs"]) == 6
        assert all(ref["sectionName"].endswith("-https") for ref in route["spec"]["parentRefs"])

    authority = next(route for route in routes if route["kind"] == "Ingress")
    assert authority["spec"]["ingressClassName"] == "external-dns-only"
    assert authority["metadata"]["labels"] == {
        "networking.re8ch.com/dns-authority": "public-edge"
    }
    assert authority["metadata"]["annotations"][
        "external-dns.alpha.kubernetes.io/target"
    ] == "192.0.2.79"
    assert authority["metadata"]["annotations"][
        "external-dns.alpha.kubernetes.io/hostname"
    ] == "pay-api.re8ch.com"
    assert {rule["host"] for rule in authority["spec"]["rules"]} == {
        "pay-api.re8ch.com"
    }


def test_publicedge_only_advertises_hyperswitch_from_r640():
    authority = (ROOT / "clusters/re8ch/components/regional-routing/authority.yaml").read_text()
    assert '"pay.re8ch.com."' not in authority
    assert '"pay-api.re8ch.com.": {"service":"pay-api"' in authority
    assert '"pay-ui":["hyperswitch"]' not in authority
    assert '"pay-api":["hyperswitch"]' in authority


def test_pay_ui_listeners_reject_the_retired_apex_hostname():
    gateway_files = [
        ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml",
        ROOT / "clusters/re8ch/components/regional-routing/regional-gateways.yaml",
    ]
    listeners = []
    for gateway_file in gateway_files:
        for resource in yaml.safe_load_all(gateway_file.read_text()):
            if resource and resource.get("kind") == "Gateway":
                listeners.extend(
                    listener
                    for listener in resource["spec"]["listeners"]
                    if listener["name"] == "pay-ui-https"
                )
    assert len(listeners) == 6
    assert {listener["hostname"] for listener in listeners} == {
        "pay.service.re8ch.com"
    }
    assert {
        listener["tls"]["certificateRefs"][0]["name"] for listener in listeners
    } == {"re8ch-service-wildcard-tls"}


def test_pay_service_is_eligible_for_the_overseas_edge():
    release = yaml.safe_load_all(
        (ROOT / "platform/public-edge-manager/release.yaml").read_text()
    )
    helm_release = next(item for item in release if item.get("kind") == "HelmRelease")
    values = helm_release["spec"]["values"]
    assert values["services"]["pay.service.re8ch.com."]["class"] == (
        "service-overseas"
    )
    edges = {edge["name"]: edge for edge in values["publicEdges"]}
    assert "service-overseas" in edges["loudi-r640"]["serviceClasses"]
    assert "service-overseas" in edges["us-la"]["serviceClasses"]


def test_rendered_helm_values_namespace_and_job_admission():
    import subprocess

    rendered = list(yaml.safe_load_all(subprocess.check_output(
        ["kustomize", "build", str(ROOT / "platform/hyperswitch")], text=True
    )))
    secret = next(item for item in yaml.safe_load_all(
        (ROOT / "clusters/re8ch/secrets/hyperswitch-runtime-values.sops.yaml").read_text()
    ) if item and item["metadata"]["name"] == "hyperswitch-runtime-values")
    for resource in rendered:
        kind = resource["kind"]
        if kind in {"HelmRelease", "HelmRepository"}:
            assert resource["metadata"]["namespace"] == secret["metadata"]["namespace"]
        elif kind != "Namespace":
            assert resource["metadata"]["namespace"] == "payments"
        if kind == "Job":
            expected_revision = "-v11" if "bootstrap-admin" in resource["metadata"]["name"] else "-v4"
            assert resource["metadata"]["name"].endswith(expected_revision)
            pod = resource["spec"]["template"]["spec"]
            assert pod["securityContext"]["runAsNonRoot"] is True
            assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
            for container in pod["containers"]:
                assert container["securityContext"]["allowPrivilegeEscalation"] is False
                assert "ALL" in container["securityContext"]["capabilities"]["drop"]
