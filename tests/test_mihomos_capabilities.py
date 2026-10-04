import importlib.util
import json
from pathlib import Path
import pytest
from mihomos_capabilities import workloads
from runtime import Identity, OperationError


class Service:
    def __init__(self, items):
        self.result = {"exitCode": 0, "stdout": json.dumps({"items": items}), "auditRef": "audit://fixture"}
        self.calls = []

    def _kubectl(self, argv):
        self.calls.append(argv)
        return self.result


def workload(name="mihomos-node-cn", namespace="egress-fabric"):
    return {"metadata": {"name": name, "namespace": namespace, "uid": "uid", "generation": 2, "annotations": {"password": "credential"}},
            "spec": {"template": {"spec": {"containers": [{"env": [{"value": "credential"}]}]}}},
            "status": {"desiredNumberScheduled": 10, "numberReady": 8, "observedGeneration": 1, "conditions": [{"message": "credential"}]}}


def test_status_projection_preserves_readiness_and_generation_without_configs():
    service = Service([workload(), workload("unrelated"), workload(namespace="other")])
    result = workloads(service)
    assert len(result["workloads"]) == 1
    assert result["workloads"][0]["status"]["numberReady"] == 8
    assert result["workloads"][0]["generation"] == 2
    assert "mihomos-global-exit" in result["missingWorkloads"]
    assert "credential" not in json.dumps(result)
    assert result["auditRef"] == "audit://fixture"
    assert service.calls == [["get", "daemonsets", "-n", "egress-fabric", "-o", "json"]]


@pytest.mark.parametrize("failure", [{"exitCode": 1}, {"truncated": True}, {"stdout": "not JSON"}, {"stdout": "{}"}])
def test_failed_discovery_never_claims_empty_cluster(failure):
    service = Service([])
    service.result.update(failure)
    with pytest.raises(OperationError):
        workloads(service)


def test_mihomos_admin_registration_and_dispatch():
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("mihomos_server", root / "operations/cloud-platform/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = Service([workload()])
    with pytest.raises(OperationError):
        module.dispatch(service, "mihomos.workloads.list", {}, Identity("viewer", ()))
    assert not service.calls
    viewer = Identity("viewer", ())
    for name in module.MIHOMOS_BY_NAME:
        assert name not in {tool["name"] for tool in module.tool_list(viewer)}
        with pytest.raises(OperationError) as exc:
            module.dispatch(service, name, {}, viewer)
        assert exc.value.code == "AUTHORIZATION_DENIED"
    assert not service.calls
    with pytest.raises(OperationError):
        module.dispatch(service, "mihomos.workloads.list", {"namespace": "other"}, Identity("admin", (module.ADMIN_GROUP,)))
    assert not service.calls
    admin = Identity("admin", (module.ADMIN_GROUP,))
    assert module.dispatch(service, "mihomos.workloads.list", {}, admin)["workloads"]
    assert "mihomos.workloads.list" in {t["name"] for t in module.tool_list(admin)}
    for relative in ["platform/apps/tenant-mcp/platform-modules.json", "clusters/re8ch/charts/birfucate-metering/files/tenant-platform-modules.json"]:
        assert json.loads((root / relative).read_text())["backends"]["native"]["allowed"]["mihomos.workloads.list"]


def test_invalid_status_counter_fails_closed():
    item = workload()
    item["status"]["numberReady"] = "invalid"
    with pytest.raises(OperationError):
        workloads(Service([item]))


def test_empty_list_preserves_missing_evidence_without_claiming_health():
    result = workloads(Service([]))
    assert result["workloads"] == []
    assert result["missingWorkloads"]
    assert result["coverage"] == "workload_status_only"

UID = "12345678-1234-1234-1234-123456789abc"
POD = "mihomos-node-cn-fixture"


def controller_pod():
    return {"metadata": {"name": POD, "namespace": "egress-fabric", "uid": UID,
                         "labels": {"app.kubernetes.io/component": "node-proxy"},
                         "ownerReferences": [{"kind": "DaemonSet", "name": "mihomos-node-cn", "controller": True}]},
            "spec": {"nodeName": "fixture-node", "containers": [{"name": "mihomo", "ports": [{"name": "controller", "containerPort": 19090}]}]},
            "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}


class ControllerService:
    def __init__(self):
        self.pod = controller_pod()
        self.calls = []
        self.version = {"meta": True, "version": "v1.19.29", "password": "credential"}

    def _kubectl(self, argv):
        self.calls.append(argv)
        value = {"items": [self.pod]} if argv[1] == "pods" else self.pod if argv[1] == "pod" else self.version
        return {"exitCode": 0, "stdout": json.dumps(value), "auditRef": "audit://controller"}


def test_controller_discovery_and_native_version_bind_pod_uid_and_port():
    from mihomos_capabilities import controllers, controller_version
    service = ControllerService()
    targets = controllers(service)["controllers"]
    assert targets == [{"pod": POD, "uid": UID, "node": "fixture-node", "ready": True}]
    result = controller_version(service, POD, UID)
    assert result["result"] == {"meta": True, "version": "v1.19.29"}
    assert "credential" not in json.dumps(result)
    assert service.calls[-1] == ["get", "--raw", f"/api/v1/namespaces/egress-fabric/pods/{POD}:19090/proxy/version", "--request-timeout=10s"]


@pytest.mark.parametrize("change", ["owner", "uid", "port", "unready", "terminating"])
def test_changed_or_unreviewed_targets_never_receive_controller_requests(change):
    from mihomos_capabilities import controller_version
    service = ControllerService()
    if change == "owner":
        service.pod["metadata"]["ownerReferences"][0]["name"] = "unrelated"
    elif change == "uid":
        service.pod["metadata"]["uid"] = "changed"
    elif change == "port":
        service.pod["spec"]["containers"][0]["ports"][0]["containerPort"] = 9999
    elif change == "unready":
        service.pod["status"]["conditions"][0]["status"] = "False"
    else:
        service.pod["metadata"]["deletionTimestamp"] = "2026-10-04T00:00:00Z"
    with pytest.raises(OperationError):
        controller_version(service, POD, UID)
    assert len(service.calls) == 1 and service.calls[0][1] == "pod"


@pytest.mark.parametrize("pod,uid", [("../secrets", UID), ("x:123/proxy", UID), (POD, None)])
def test_controller_argument_injection_fails_before_cluster_access(pod, uid):
    from mihomos_capabilities import controller_version
    service = ControllerService()
    with pytest.raises(OperationError):
        controller_version(service, pod, uid)
    assert not service.calls


def test_invalid_version_response_fails_contract_validation():
    from mihomos_capabilities import controller_version
    service = ControllerService()
    service.version = {"meta": "true", "version": 1}
    with pytest.raises(OperationError) as exc:
        controller_version(service, POD, UID)
    assert exc.value.code == "MIHOMOS_CONTRACT_FAILED"


def proxy_fixture():
    return {"name": "节点 A", "type": "Direct", "alive": True, "udp": True,
            "history": [{"time": "2026-10-04T00:00:00Z", "delay": 1, "token": "credential"}],
            "extra": {"password": "credential"}}


def provider_fixture():
    return {"name": "fixture", "type": "Proxy", "vehicleType": "File", "proxies": [proxy_fixture()],
            "testUrl": "https://example.invalid/credential", "expectedStatus": "credential",
            "subscriptionInfo": {"token": "credential"}}


READ_CASES = [
    ("listMihomoProxies", {}, {"proxies": {"fixture": proxy_fixture()}}),
    ("getMihomoProxy", {"name": "节点 A"}, proxy_fixture()),
    ("listMihomoGroups", {}, {"proxies": [proxy_fixture()]}),
    ("getMihomoGroup", {"name": "节点 A"}, proxy_fixture()),
    ("listMihomoRules", {}, {"rules": [{"index": 0, "type": "MATCH", "payload": "", "proxy": "DIRECT", "size": 1, "token": "credential"}]}),
    ("listMihomoProxyProviders", {}, {"providers": {"fixture": provider_fixture()}}),
    ("getMihomoProxyProvider", {"providerName": "fixture"}, provider_fixture()),
    ("getMihomoProviderProxy", {"providerName": "fixture", "name": "节点 A"}, proxy_fixture()),
    ("listMihomoRuleProviders", {}, {"providers": {"fixture": {"name": "fixture", "type": "Rule", "vehicleType": "File", "behavior": "Domain", "format": "Text", "ruleCount": 2, "token": "credential"}}}),
]


@pytest.mark.parametrize("operation,parameters,body", READ_CASES)
def test_native_reads_validate_and_project_nested_responses(operation, parameters, body):
    from mihomos_capabilities import controller_read
    service = ControllerService()
    service.version = body
    result = controller_read(service, POD, UID, operation, parameters)
    assert result["operationId"] == operation
    assert "credential" not in json.dumps(result)
    assert result["auditRef"] == "audit://controller"
    assert len(service.calls) == 2
    if "name" in parameters:
        assert "%E8%8A%82%E7%82%B9%20A" in service.calls[-1][2]
    service.version = {"unexpected": True}
    with pytest.raises(OperationError) as exc:
        controller_read(service, POD, UID, operation, parameters)
    assert exc.value.code == "MIHOMOS_CONTRACT_FAILED"


@pytest.mark.parametrize("parameters", [{}, {"name": "x", "extra": "x"}, {"name": "../x"}, {"name": "%2F"}, {"name": "x?delay=1"}, {"name": "x\\y"}, {"name": ".."}, {"name": ""}, {"name": "x\n"}])
def test_native_path_rejects_injection_before_discovery(parameters):
    from mihomos_capabilities import controller_read
    service = ControllerService()
    with pytest.raises(OperationError):
        controller_read(service, POD, UID, "getMihomoProxy", parameters)
    assert not service.calls


@pytest.mark.parametrize("operation", ["updateMihomoProxy", "getMihomoProxyDelay", "unknown"])
def test_unreviewed_operations_never_reach_cluster(operation):
    from mihomos_capabilities import controller_read
    service = ControllerService()
    with pytest.raises(OperationError) as exc:
        controller_read(service, POD, UID, operation)
    assert exc.value.code == "MIHOMOS_OPERATION_NOT_READ_ONLY"
    assert not service.calls


def test_native_nil_slices_remain_null():
    from mihomos_capabilities import controller_read
    service = ControllerService()
    service.version = {"proxies": None}
    assert controller_read(service, POD, UID, "listMihomoGroups")["result"] == {"proxies": None}
    service.version = dict(provider_fixture(), proxies=None)
    assert controller_read(service, POD, UID, "getMihomoProxyProvider", {"providerName": "fixture"})["result"]["proxies"] is None


def config_summary_fixture():
    return {"port": 7890, "socks-port": 7891, "redir-port": 0, "tproxy-port": 0, "mixed-port": 0,
            "allow-lan": False, "inbound-tfo": False, "inbound-mptcp": False, "unified-delay": True,
            "ipv6": True, "geo-auto-update": False, "geodata-mode": True, "tcp-concurrent": True,
            "sniffing": False, "etag-support": True, "disable-keep-alive": False,
            "bind-address": "127.0.0.1", "mode": "rule", "log-level": "info", "interface-name": "",
            "geodata-loader": "memconservative", "geosite-matcher": "succinct", "find-process-mode": "off",
            "routing-mark": 0, "geo-update-interval": 24, "keep-alive-idle": 30, "keep-alive-interval": 30}


def connection_snapshot_fixture():
    return {"downloadTotal": 42, "uploadTotal": 21, "memory": 12345, "connections": [{
        "id": UID, "upload": 2, "download": 4, "start": "2026-10-04T00:00:00Z",
        "chains": ["DIRECT"], "providerChains": None, "rule": "Match", "rulePayload": "",
        "metadata": {"network": "tcp", "type": "Socks5", "sourceIP": "127.0.0.1",
                     "destinationIP": "192.0.2.1", "sourcePort": "12000", "destinationPort": "443",
                     "inboundIP": "127.0.0.1", "inboundPort": "7891", "inboundName": "socks",
                     "host": "example.invalid", "dnsMode": "normal"}}]}


@pytest.mark.parametrize("operation,body", [
    ("getMihomoHello", {"hello": "mihomo"}),
    ("getMihomoConfigSummary", config_summary_fixture()),
    ("getMihomoConnectionSnapshot", connection_snapshot_fixture()),
])
def test_additional_controller_reads_dispatch_through_admin_contract(operation, body):
    from mihomos_capabilities import TOOLS
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("additional_mihomos_server", root / "operations/cloud-platform/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tool = next(t for t in TOOLS if t.get("_meta", {}).get("re8ch", {}).get("operationId") == operation)
    service = ControllerService()
    service.version = body
    result = module.dispatch(service, tool["name"], {"pod": POD, "uid": UID}, Identity("admin", (module.ADMIN_GROUP,)))
    assert result["result"] == body
    assert service.calls[-1][2].endswith(tool["_meta"]["re8ch"]["path"])
    assert service.calls[-1][-1] == "--request-timeout=10s"
    # Neither upgrade headers nor an interval/query string enter this transport.
    assert "?" not in service.calls[-1][2]
    for relative in ["platform/apps/tenant-mcp/platform-modules.json", "clusters/re8ch/charts/birfucate-metering/files/tenant-platform-modules.json"]:
        assert json.loads((root / relative).read_text())["backends"]["native"]["allowed"][tool["name"]]


def test_config_projection_omits_embedded_credentials_and_arbitrary_settings():
    from mihomos_capabilities import controller_read
    service = ControllerService()
    expected = config_summary_fixture()
    service.version = dict(expected, authentication=["credential"], tun={"extra": "credential"},
                           **{"ss-config": "credential", "vmess-config": "credential",
                              "tuic-server": {"users": {"user": "credential"}, "private-key": "credential"},
                              "geox-url": {"geo-ip": "https://credential.invalid"}})
    assert controller_read(service, POD, UID, "getMihomoConfigSummary")["result"] == expected
    service.version["port"] = {"password": "credential"}
    with pytest.raises(OperationError) as exc:
        controller_read(service, POD, UID, "getMihomoConfigSummary")
    assert exc.value.code == "MIHOMOS_CONTRACT_FAILED"


def test_connections_projection_preserves_null_slices_and_omits_user_process_data():
    from mihomos_capabilities import controller_read
    service = ControllerService()
    expected = connection_snapshot_fixture()
    service.version = json.loads(json.dumps(expected))
    service.version["connections"][0]["metadata"].update(inboundUser="credential", processPath="credential", uid=123,
                                                        sourceGeoIP=["credential"], extension={"token": "credential"})
    assert controller_read(service, POD, UID, "getMihomoConnectionSnapshot")["result"] == expected
    service.version["connections"] = None
    assert controller_read(service, POD, UID, "getMihomoConnectionSnapshot")["result"]["connections"] is None
    service.version["connections"] = expected["connections"]
    service.version["connections"][0]["upload"] = "invalid"
    with pytest.raises(OperationError) as exc:
        controller_read(service, POD, UID, "getMihomoConnectionSnapshot")
    assert exc.value.code == "MIHOMOS_CONTRACT_FAILED"


def test_connection_snapshot_does_not_return_partial_truncated_evidence():
    from mihomos_capabilities import controller_read
    service = ControllerService()
    original = service._kubectl
    service._kubectl = lambda argv: dict(original(argv), truncated=argv[1] == "--raw")
    with pytest.raises(OperationError) as exc:
        controller_read(service, POD, UID, "getMihomoConnectionSnapshot")
    assert exc.value.code == "MIHOMOS_CONTROLLER_UNAVAILABLE"
