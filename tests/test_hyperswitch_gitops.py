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
    assert job["metadata"]["name"] == "hyperswitch-bootstrap-admin-v9"
    command = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
    assert "HYPERSWITCH_HYPERSWITCH_SERVER_SERVICE_HOST" in command
    assert "HYPERSWITCH_HYPERSWITCH_SERVER_SERVICE_PORT" in command
    assert "/user/tenant_signup" in command
    assert '"$base_url/accounts/$MERCHANT_ID"' in command
    assert '"$base_url/accounts"' in command
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
    assert app["redis"]["master"]["persistence"]["storageClass"] == "rbd-r640"
    assert values["hyperswitch-ucs"]["enabled"] is False


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
    assert "https://pay.re8ch.com" in cors["origins"]
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
        "pay.re8ch.com",
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


def test_publicedge_only_advertises_hyperswitch_from_r640():
    authority = (ROOT / "clusters/re8ch/components/regional-routing/authority.yaml").read_text()
    assert '"pay.re8ch.com.": {"service":"pay-ui"' in authority
    assert '"pay-api.re8ch.com.": {"service":"pay-api"' in authority
    assert '"pay-ui":["hyperswitch"],"pay-api":["hyperswitch"]' in authority


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
            expected_revision = "-v9" if "bootstrap-admin" in resource["metadata"]["name"] else "-v4"
            assert resource["metadata"]["name"].endswith(expected_revision)
            pod = resource["spec"]["template"]["spec"]
            assert pod["securityContext"]["runAsNonRoot"] is True
            assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
            for container in pod["containers"]:
                assert container["securityContext"]["allowPrivilegeEscalation"] is False
                assert "ALL" in container["securityContext"]["capabilities"]["drop"]
