import copy
import json

import pytest

from plugin_capabilities import catalog, openapi
from runtime import OperationError

KEY = "apis/anatomy.re8ch.com/v1alpha1"
LOCATION = "/openapi/v3/" + KEY + "?hash=fixture"
DOCUMENT = {"openapi": "3.0.0", "info": {"title": "Kubernetes", "version": "v1"}, "paths": {
    "/apis/anatomy.re8ch.com/v1alpha1/namespaces/{namespace}/runtimecommits": {
        "parameters": [{"name": "namespace", "in": "path", "required": True, "schema": {"type": "string"}}],
        "post": {"operationId": "createRuntimeCommit", "x-kubernetes-action": "post",
                 "x-kubernetes-group-version-kind": {"kind": "RuntimeCommit"},
                 "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/RuntimeCommit"}}}},
                 "responses": {"201": {"description": "created"}}}}},
    "components": {"schemas": {"RuntimeCommit": {"type": "object", "properties": {"data": {"type": "object"}}}}}}


class Service:
    def __init__(self):
        self.index = {"paths": {KEY: {"serverRelativeURL": LOCATION}, "apis/unknown.example/v1": {}}}
        self.calls = []
        self.failure = False
        self.truncated = False

    def _kubectl(self, argv):
        assert argv[:2] == ["get", "--raw"]
        self.calls.append(argv[-1])
        return {"exitCode": int(self.failure), "truncated": self.truncated,
                "stdout": json.dumps(self.index if argv[-1] == "/openapi/v3" else DOCUMENT),
                "auditRef": "audit://fixture"}


def test_catalog_keeps_unknown_plugins_visible_and_does_not_claim_coverage():
    result = catalog(Service())
    assert len(result["schemas"]) == 2
    assert result["schemas"][1]["plugin"] == "unclassified"
    assert result["coverage"] == "discovery_only"
    assert "other connected clusters" in result["uncoveredSurfaces"]


def test_native_openapi_preserves_schema_refs_and_mutation_operations():
    service = Service()
    original = copy.deepcopy(DOCUMENT)
    result = openapi(service, KEY)
    assert service.calls == ["/openapi/v3", LOCATION]
    assert result["openapi"] == original  # schema property 'data' must not be redacted
    assert len(result["sha256"]) == 64
    row, = result["operations"]
    assert row["plugin"] == "anatomy"
    assert row["method"] == "POST"
    assert row["operationId"] == "createRuntimeCommit"
    assert row["registration"] == "not_verified"
    assert row["schemaConformance"] == row["semanticConformance"] == "not_run"


@pytest.mark.parametrize("key", [None, "https://external/api", "api/v1/../../secrets", "apis/a/v1?x=y", "--raw=/api/v1/secrets"])
def test_invalid_group_never_reaches_cluster(key):
    service = Service()
    with pytest.raises(OperationError):
        openapi(service, key)
    assert service.calls == []


@pytest.mark.parametrize("location", ["https://external/schema", "/api/v1/secrets", "//external/openapi/v3/" + KEY, LOCATION + "#fragment"])
def test_discovery_cannot_redirect_to_data_or_external_hosts(location):
    service = Service()
    service.index["paths"][KEY]["serverRelativeURL"] = location
    with pytest.raises(OperationError):
        openapi(service, KEY)
    assert service.calls == ["/openapi/v3"]


@pytest.mark.parametrize("field", ["failure", "truncated"])
def test_failed_or_truncated_discovery_is_not_empty_success(field):
    service = Service()
    setattr(service, field, True)
    with pytest.raises(OperationError):
        catalog(service)


def test_transport_and_self_service_allowlist_register_same_tools():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("cloud_server_capabilities", root / "operations/cloud-platform/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from runtime import Identity
    tools = {tool["name"] for tool in module.tool_list(Identity("fixture", (module.ADMIN_GROUP,)))}
    registry = json.loads((root / "platform/apps/tenant-mcp/platform-modules.json").read_text())
    chart = json.loads((root / "clusters/re8ch/charts/birfucate-metering/files/tenant-platform-modules.json").read_text())
    assert registry == chart
    for name in ["plugin.capabilities.list", "plugin.openapi.get"]:
        assert name in tools
        assert registry["backends"]["native"]["allowed"][name]
    assert module.dispatch(Service(), "plugin.openapi.get", {"groupVersion": KEY}, Identity("fixture", (module.ADMIN_GROUP,)))["operations"]


class ResourceService(Service):
    def __init__(self, watch=False):
        super().__init__()
        self.document = copy.deepcopy(DOCUMENT)
        self.path = next(iter(self.document["paths"]))
        self.document["paths"][self.path]["get"] = {
            "operationId": "listRuntimeCommits", "x-kubernetes-action": "list",
            "parameters": [
                {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                {"name": "timeoutSeconds", "in": "query", "schema": {"type": "integer"}},
                {"name": "watch", "in": "query", "schema": {"type": "boolean"}},
                {"name": "labelSelector", "in": "query", "schema": {"type": "string"}}],
            "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {
                "type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/RuntimeCommit"}}}}}}}}}
        self.document["paths"][self.path + "/{name}/status"] = {
            "patch": {"operationId": "patchStatus", "responses": {"200": {"description": "OK"}}}}
        self.watch = watch

    def _kubectl(self, argv):
        if argv[-1] == "/openapi/v3":
            return super()._kubectl(argv)
        self.calls.append(argv[-1])
        if argv[-1] == LOCATION:
            return {"exitCode": 0, "stdout": json.dumps(self.document), "auditRef": "audit://schema"}
        payload = {"metadata": {"name": "safe", "annotations": {"last-applied": "credential"}},
                   "spec": {"nested": {"password": "credential"}}, "status": {"phase": "Ready"}}
        body = json.dumps({"type": "ADDED", "object": payload}) + "\n" if self.watch else json.dumps({"items": [payload], "metadata": {"continue": "opaque-next-page"}})
        return {"exitCode": 0, "stdout": body, "auditRef": "audit://read"}


def read_args(service):
    from plugin_capabilities import openapi
    return {"group_version": KEY, "operation_id": "listRuntimeCommits", "schema_hash": openapi(service, KEY)["sha256"],
            "parameters": {"path": {"namespace": "acceptance"}}}


def test_resource_read_binds_exact_native_contract_and_redacts_result():
    from plugin_capabilities import read_operation
    service = ResourceService()
    args = read_args(service)
    args["parameters"]["query"] = {"labelSelector": "app=runtime&watch=true"}
    result = read_operation(service, **args)
    assert service.calls[-1] == "/apis/anatomy.re8ch.com/v1alpha1/namespaces/acceptance/runtimecommits?labelSelector=app%3Druntime%26watch%3Dtrue&limit=100&timeoutSeconds=10"
    assert "credential" not in json.dumps(result)
    assert result["result"]["items"][0]["status"]["phase"] == "Ready"
    assert result["result"]["metadata"]["continue"] == "opaque-next-page"
    assert result["auditRef"] == "audit://read"


def test_watch_uses_bounded_native_window_and_decodes_events():
    from plugin_capabilities import read_operation
    service = ResourceService(watch=True)
    args = read_args(service)
    args["parameters"]["query"] = {"watch": True, "timeoutSeconds": 2}
    result = read_operation(service, **args)
    assert service.calls[-1].endswith("?timeoutSeconds=2&watch=true")
    assert result["watch"] is True
    assert result["result"][0]["type"] == "ADDED"
    assert "credential" not in json.dumps(result)


@pytest.mark.parametrize("parameters", [
    {}, {"path": {"namespace": "../secrets"}}, {"path": {"namespace": "a/b"}},
    {"path": {"namespace": "acceptance"}, "query": {"limit": True}},
    {"path": {"namespace": "acceptance"}, "query": {"limit": 0}},
    {"path": {"namespace": "acceptance"}, "query": {"limit": 501}},
    {"path": {"namespace": "acceptance"}, "query": {"timeoutSeconds": 21}},
    {"path": {"namespace": "acceptance"}, "query": {"watch": "false"}},
    {"path": {"namespace": "acceptance"}, "query": {"unpublished": "x"}},
    {"path": {"namespace": "acceptance"}, "headers": {"Authorization": "x"}},
])
def test_invalid_parameters_fail_before_resource_access(parameters):
    from plugin_capabilities import read_operation
    service = ResourceService()
    args = read_args(service)
    args["parameters"] = parameters
    with pytest.raises(OperationError):
        read_operation(service, **args)
    assert all(path in ["/openapi/v3", LOCATION] for path in service.calls)


@pytest.mark.parametrize("operation", ["createRuntimeCommit", "patchStatus", "doesNotExist"])
def test_read_adapter_cannot_invoke_mutation_or_unknown_operation(operation):
    from plugin_capabilities import read_operation
    service = ResourceService()
    args = read_args(service)
    args["operation_id"] = operation
    with pytest.raises(OperationError):
        read_operation(service, **args)
    assert all(path in ["/openapi/v3", LOCATION] for path in service.calls)


def test_changed_schema_and_unreviewed_groups_fail_closed():
    from plugin_capabilities import read_operation
    service = ResourceService()
    args = read_args(service)
    args["schema_hash"] = "0" * 64
    with pytest.raises(OperationError, match="Refresh"):
        read_operation(service, **args)
    service.calls.clear()
    args["group_version"] = "api/v1"
    with pytest.raises(OperationError, match="not enabled"):
        read_operation(service, **args)
    assert not service.calls


def test_catalog_classifies_controller_and_gitops_authority_separately():
    from plugin_capabilities import operations
    result = operations(ResourceService(), KEY)
    rows = {row["operationId"]: row for row in result["operations"]}
    assert rows["listRuntimeCommits"]["readTool"] == "plugin.operation.read"
    assert rows["createRuntimeCommit"]["disposition"] == "authority_review_required"
    assert rows["patchStatus"]["disposition"] == "controller_owned"
    assert rows["createRuntimeCommit"]["registration"] == "not_implemented"


def test_every_captured_operation_has_a_contract_without_dropping_mutations():
    from pathlib import Path
    from plugin_capabilities import operation_contract
    root = Path(__file__).parents[1] / "operations/plugin-capabilities"
    matrix = json.loads((root / "native-operation-matrix.json").read_text())
    count = 0
    for source in matrix["sources"]:
        document = json.loads((root / source["schemaFile"]).read_text())
        for row in source["operations"]:
            contract = operation_contract(document, row["operationId"])
            assert contract["method"].lower() == row["method"]
            assert contract["pathTemplate"] == row["path"]
            count += 1
    assert count == 853


def test_object_reads_require_admin_before_any_backend_access():
    import importlib.util
    from pathlib import Path
    from runtime import Identity
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("cloud_server_read_auth", root / "operations/cloud-platform/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = ResourceService()
    identity = Identity("viewer", ())
    assert "plugin.operation.read" not in {t["name"] for t in module.tool_list(identity)}
    with pytest.raises(OperationError) as exc:
        module.dispatch(service, "plugin.operation.read", {}, identity)
    assert exc.value.code == "AUTHORIZATION_DENIED"
    assert not service.calls


def test_static_tool_catalog_remains_importable_and_separates_ssh_streams():
    import tool_catalog
    assert tool_catalog.TOOLS["plugin.operation.read"] == "plugin_capabilities.read_operation"
    assert isinstance(tool_catalog.SSH_STREAM_TOOLS, set)
    assert not any(name.startswith("plugin.") for name in tool_catalog.SSH_STREAM_TOOLS)


def test_generated_tools_are_callable_and_match_directory_and_self_service():
    import importlib.util
    from pathlib import Path
    from runtime import Identity
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("cloud_server_generated_tools", root / "operations/cloud-platform/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated = json.loads((root / "operations/cloud-platform/plugin-operation-tools.json").read_text())["tools"]
    names = {item["name"] for item in generated}
    assert len(names) == len(generated) == 286
    assert sum(item["_meta"]["re8ch"]["method"] == "GET" for item in generated) == 282
    assert all(len(name) <= 64 for name in names)
    admin = Identity("admin", (module.ADMIN_GROUP,))
    assert names <= {tool["name"] for tool in module.tool_list(admin)}
    assert names.isdisjoint({tool["name"] for tool in module.tool_list(Identity("viewer", ()))})
    registry = json.loads((root / "operations/plugin-capabilities/registry.json").read_text())
    metadata = registry["servers"][0]["_meta"]["io.modelcontextprotocol.registry/publisher-provided"]["io.github.stacklok"]["streamable-http"]
    assert names | set(module.MIHOMOS_BY_NAME) == {tool["name"] for tool in metadata["tool_definitions"]}
    allowed = json.loads((root / "platform/apps/tenant-mcp/platform-modules.json").read_text())["backends"]["native"]["allowed"]
    assert all(allowed[name] for name in names)
    tool = next(item for item in generated if item["operationId"] == "listAnatomyRe8chComV1alpha1NamespacedRuntimeCommit")
    service = ResourceService()
    service.document = json.loads((root / "operations/plugin-capabilities/openapi/apis_anatomy.re8ch.com_v1alpha1.json").read_text())
    result = module.dispatch(service, tool["name"], {"path": {"namespace": "acceptance"}}, admin)
    assert result["result"]["items"][0]["status"]["phase"] == "Ready"
    assert result["operationId"] == tool["operationId"]
    assert result["auditRef"] == "audit://read"
    assert "credential" not in json.dumps(result)


def test_directory_inventory_includes_every_native_operation_without_advertising_missing_tools():
    from pathlib import Path
    root = Path(__file__).parents[1]
    registry_path = root / 'operations/plugin-capabilities/registry.json'
    registry = json.loads(registry_path.read_text())
    metadata = registry['servers'][0]['_meta']['io.modelcontextprotocol.registry/publisher-provided']['io.github.stacklok']['streamable-http']
    inventory = metadata['re8ch_capabilities']
    rows = inventory['operations']
    matrix = json.loads((root / 'operations/plugin-capabilities/native-operation-matrix.json').read_text())
    controller = json.loads((root / 'operations/plugin-capabilities/mihomos-native-surfaces.json').read_text())
    expected = {(source['groupVersion'], row['method'].upper(), row['path']) for source in matrix['sources'] for row in source['operations']}
    expected |= {('controller/' + controller['version'], row['method'], row['path']) for row in controller['operations']}
    actual = {(row['groupVersion'], row['method'], row['path']) for row in rows}
    assert len(rows) == len(actual) == len(expected) == 893
    assert actual == expected
    tools = {row['name'] for row in metadata['tool_definitions']}
    implemented = [row for row in rows if row['registration'] == 'deployed']
    assert len(implemented) == 210
    assert all(row['tool'] in tools for row in implemented)
    assert all('tool' not in row for row in rows if row['registration'] == 'not_implemented')
    assert all(row['httpConformance'] == row['semanticConformance'] == 'not_run' for row in rows)
    assert all(row['disposition'] == 'controller_owned' for row in rows if row['path'].endswith('/status') and row['method'] != 'GET')
    runtime = registry_path.with_name('registry.runtime.json')
    assert json.loads(runtime.read_text())['data'] == registry
    assert runtime.stat().st_size < 1024 * 1024


def test_generator_is_byte_stable_when_a_new_mihomos_tool_is_added(tmp_path, monkeypatch):
    import importlib.util
    import shutil
    from pathlib import Path

    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("plugin_generator_idempotence", root / "operations/plugin-capabilities/generate.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    relatives = ["operations/plugin-capabilities/native-operation-matrix.json",
                 "operations/plugin-capabilities/mihomos-native-surfaces.json",
                 "operations/cloud-platform/plugin-operation-tools.json",
                 "platform/apps/tenant-mcp/platform-modules.json",
                 "clusters/re8ch/charts/birfucate-metering/files/tenant-platform-modules.json"]
    for relative in relatives:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    shutil.copytree(root / "operations/plugin-capabilities/openapi", tmp_path / "operations/plugin-capabilities/openapi")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    added = {"name": "mihomos.fixture.new", "description": "fixture", "inputSchema": {"type": "object"}}
    monkeypatch.setattr(generator, "MIHOMOS_TOOLS", [*generator.MIHOMOS_TOOLS, added])
    generator.generate()
    outputs = [*relatives[2:], "operations/plugin-capabilities/registry.json", "operations/plugin-capabilities/registry.runtime.json"]
    first = {relative: (tmp_path / relative).read_bytes() for relative in outputs}
    generator.generate()
    assert first == {relative: (tmp_path / relative).read_bytes() for relative in outputs}
    for relative in relatives[3:]:
        allowed = json.loads(first[relative])["backends"]["native"]["allowed"]
        assert allowed[added["name"]] is True


def test_anatomy_mutation_classification_tracks_controller_source_without_enabling_writes():
    import ast
    import hashlib
    from pathlib import Path
    from plugin_capabilities import ANATOMY_AUTHORITY, operation_contract, operation_disposition
    root = Path(__file__).parents[1]
    source = (root / ANATOMY_AUTHORITY['source']).read_bytes()
    assert hashlib.sha256(source).hexdigest() == ANATOMY_AUTHORITY['sourceSha256'], 'Controller changed: re-review mutation semantics'
    functions = {node.name for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}
    assert all(row['reconciler'] in functions for row in ANATOMY_AUTHORITY['kinds'].values())
    document = json.loads((root / 'operations/plugin-capabilities/openapi/apis_anatomy.re8ch.com_v1alpha1.json').read_text())
    classified = []
    for path, item in document['paths'].items():
        for method, operation in item.items():
            if method not in {'post', 'put', 'patch', 'delete'}:
                continue
            contract = operation_contract(document, operation['operationId'])
            kind = operation['x-kubernetes-group-version-kind']['kind']
            expected = 'controller_owned' if path.endswith('/status') else ANATOMY_AUTHORITY['kinds'][kind]['disposition']
            assert contract['disposition'] == expected
            assert contract['disposition'] != 'read'
            classified.append(contract)
    assert len(classified) == 49
    assert operation_disposition('apis/unknown/v1', 'RuntimeDiff', 'post', '/apis/unknown/v1/diffs') == 'authority_review_required'
    assert operation_disposition(ANATOMY_AUTHORITY['groupVersion'], 'NewKind', 'post', '/new') == 'authority_review_required'
    assert all(row['execution'] == 'not_implemented' and row['liveSourceParity'] == 'not_verified' for row in ANATOMY_AUTHORITY['kinds'].values())


def response_contract(schema):
    return {'responses': {'200': {'content': {'application/json': {'schema': schema}}}}}


def test_response_validation_handles_openapi_nullable_bounds_and_local_references():
    from plugin_capabilities import validate_response_body
    schema = {'type': 'object', 'required': ['value', 'port', 'count'], 'properties': {
        'value': {'type': 'string', 'nullable': True},
        'port': {'anyOf': [{'type': 'integer'}, {'type': 'string'}], 'x-kubernetes-int-or-string': True},
        'count': {'type': 'number', 'minimum': 0, 'exclusiveMinimum': True}}}
    document = {'components': {'schemas': {'Result': schema}}}
    contract = response_contract({'$ref': '#/components/schemas/Result'})
    result = validate_response_body(document, contract, {'value': None, 'port': 'http', 'count': 1}, False)
    assert result == {'mode': 'native_json_body', 'validatedObjects': 1, 'httpConformance': 'not_run'}
    for invalid in [{'value': None, 'port': True, 'count': 1}, {'value': None, 'port': 80, 'count': 0}, {}]:
        with pytest.raises(OperationError) as exc:
            validate_response_body(document, contract, invalid, False)
        assert exc.value.code == 'PLUGIN_RESPONSE_CONTRACT_FAILED'
    with pytest.raises(OperationError):
        validate_response_body({}, response_contract({'type': 'string', 'nullable': True, 'enum': ['allowed']}), None, False)


@pytest.mark.parametrize('reference', ['https://example.invalid/schema', '#/components/schemas/Missing'])
def test_response_schema_cannot_fetch_remote_or_unresolved_references(reference):
    from plugin_capabilities import validate_response_body
    with pytest.raises(OperationError) as exc:
        validate_response_body({}, response_contract({'$ref': reference}), {}, False)
    assert exc.value.code == 'PLUGIN_SCHEMA_UNSUPPORTED'


def test_watch_validates_objects_and_bookmarks_but_never_returns_partial_error_stream():
    from plugin_capabilities import validate_response_body
    contract = response_contract({'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'type': 'object', 'required': ['spec']}}}})
    valid = [{'type': 'ADDED', 'object': {'spec': {}}}, {'type': 'BOOKMARK', 'object': {'metadata': {'resourceVersion': '123'}}}]
    assert validate_response_body({}, contract, valid, True)['validatedObjects'] == 1
    assert validate_response_body({}, contract, [], True)['validatedObjects'] == 0
    for event in [{'type': 'DELETED', 'object': {}}, {'type': 'BOOKMARK', 'object': {'metadata': []}}, {'type': 'UNKNOWN', 'object': {}}, 'not-an-event']:
        with pytest.raises(OperationError) as exc:
            validate_response_body({}, contract, [event], True)
        assert exc.value.code == 'PLUGIN_RESPONSE_CONTRACT_FAILED'
    with pytest.raises(OperationError) as exc:
        validate_response_body({}, contract, valid + [{'type': 'ERROR', 'object': {'message': 'sensitive'}}], True)
    assert exc.value.code == 'PLUGIN_WATCH_FAILED'
    assert 'sensitive' not in str(exc.value)


def test_missing_native_response_contract_is_not_silent_success():
    from plugin_capabilities import validate_response_body
    with pytest.raises(OperationError) as exc:
        validate_response_body({}, {'responses': {'200': {}}}, {}, False)
    assert exc.value.code == 'PLUGIN_SCHEMA_UNSUPPORTED'


def test_every_generated_read_has_a_native_json_response_schema():
    from pathlib import Path
    from plugin_capabilities import local_ref, READ_GROUPS
    from jsonschema import Draft4Validator
    directory = Path(__file__).parents[1] / 'operations/plugin-capabilities/openapi'
    count = 0
    for path in directory.glob('*.json'):
        if path.stem.replace('_', '/') not in READ_GROUPS:
            continue
        document = json.loads(path.read_text())
        for item in document['paths'].values():
            if 'get' not in item:
                continue
            schema = item['get']['responses']['200']['content']['application/json']['schema']
            resolved = local_ref(document, schema)
            Draft4Validator.check_schema(resolved)
            assert resolved['type'] == 'object'
            count += 1
    assert count == 197


def test_read_rejects_successful_cli_with_invalid_resource_body():
    from plugin_capabilities import read_operation
    class InvalidBodyService(ResourceService):
        def _kubectl(self, argv):
            result = super()._kubectl(argv)
            if argv[-1] not in ['/openapi/v3', LOCATION]:
                result['stdout'] = json.dumps({'items': 'sensitive-invalid-payload'})
            return result
    service = InvalidBodyService()
    with pytest.raises(OperationError) as exc:
        read_operation(service, **read_args(service))
    assert exc.value.code == 'PLUGIN_RESPONSE_CONTRACT_FAILED'
    assert 'sensitive' not in str(exc.value)


def test_unreviewed_openapi_dialect_is_not_validated_as_30():
    from plugin_capabilities import validate_response_body
    with pytest.raises(OperationError) as exc:
        validate_response_body({'openapi': '3.1.0'}, response_contract({'type': 'object'}), {}, False)
    assert exc.value.code == 'PLUGIN_SCHEMA_UNSUPPORTED'


def test_live_sample_evidence_is_bounded_and_does_not_claim_full_acceptance():
    import hashlib
    from pathlib import Path
    root = Path(__file__).parents[1] / 'operations/plugin-capabilities'
    report = json.loads((root / 'live-body-samples-report.json').read_text())
    sources = json.loads((root / 'native-operation-matrix.json').read_text())['sources']
    schemas = {source['groupVersion']: json.loads((root / source['schemaFile']).read_text()) for source in sources}
    assert report['nativeReads'] == len(report['operations']) == 6
    assert report['rawBodiesRetained'] is False
    assert report['httpConformance'] == report['semanticConformance'] == report['deployedAdapterInvocation'] == 'not_run'
    assert report['liveSchemaParity'] == 'not_verified'
    assert {row['groupVersion'] for row in report['operations']} < set(schemas)
    assert len(report['operations']) == 6
    for row in report['operations']:
        document = schemas[row['groupVersion']]
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert row['schemaHash'] == digest
        assert document['paths'][row['path']]['get']['operationId'] == row['operationId']
        assert row['auditRef'].startswith('audit://operations/')
        assert row['sampleItems'] == 1 and row['result'] == 'passed'
        assert row['bodyValidation']['httpConformance'] == 'not_run'
        assert not {'stdout', 'resultBody', 'items', 'spec', 'status', 'annotations'} & row.keys()



def test_storage_reads_use_reviewed_groups_and_unknown_groups_remain_disabled():
    from pathlib import Path
    from plugin_capabilities import READ_GROUPS, read_operation, STORAGE_PROJECTION
    root = Path(__file__).parents[1]
    groups = {'apis/csi.ceph.io/v1', 'apis/objectbucket.io/v1alpha1'}
    generated = json.loads((root / 'operations/cloud-platform/plugin-operation-tools.json').read_text())['tools']
    assert groups <= READ_GROUPS
    assert groups == set(STORAGE_PROJECTION['groups'])
    assert len([tool for tool in generated if tool['groupVersion'] in groups]) == 27
    service = Service()
    with pytest.raises(OperationError) as exc:
        read_operation(service, 'apis/not-reviewed.example/v1', 'unreviewed', '0' * 64)
    assert exc.value.code == 'PLUGIN_GROUP_NOT_REVIEWED'
    assert not service.calls


def test_storage_projection_excludes_arbitrary_credentials_and_preserves_native_pagination():
    from plugin_capabilities import project_storage_resource
    bucket = {'apiVersion': 'objectbucket.io/v1alpha1', 'kind': 'ObjectBucket',
              'metadata': {'name': 'bucket', 'resourceVersion': '123', 'annotations': {'x': 'credential'}},
              'spec': {'authentication': {'access': 'credential'}, 'additionalState': {'x': 'credential'},
                       'endpoint': {'bucketName': 'example', 'bucketPort': 443, 'additionalConfig': {'x': 'credential'}},
                       'claimRef': {'name': 'claim', 'namespace': 'tenant', 'extra': 'credential'}},
              'status': {'phase': 'Bound', 'details': 'credential'}}
    result = project_storage_resource({'kind': 'ObjectBucketList', 'metadata': {'continue': 'opaque'}, 'items': [bucket]})
    assert result['metadata']['continue'] == 'opaque'
    item = result['items'][0]
    assert item['spec'] == {'endpoint': {'bucketName': 'example', 'bucketPort': 443}, 'claimRef': {'name': 'claim', 'namespace': 'tenant'}}
    assert item['status'] == {'phase': 'Bound'}
    assert 'credential' not in json.dumps(result)
    event = project_storage_resource([{'type': 'ADDED', 'object': bucket}, {'type': 'BOOKMARK', 'object': {'metadata': {'resourceVersion': '124'}}}])
    assert event[0]['object'] == item and event[1]['object']['metadata']['resourceVersion'] == '124'


def test_csi_projection_retains_references_but_not_mount_options_or_container_arguments():
    from plugin_capabilities import project_storage_resource
    profile = {'kind': 'ClientProfile', 'spec': {'cephConnectionRef': {'name': 'ceph'}, 'cephFs': {
        'kernelMountOptions': {'secret': 'credential'}, 'fuseMountOptions': {'x': 'credential'},
        'cephCsiSecrets': {'controllerPublishSecret': {'name': 'opaque-secret-ref', 'namespace': 'rook-ceph', 'data': 'credential'}},
        'subVolumeGroup': 'group'}}}
    result = project_storage_resource(profile)
    assert result['spec']['cephFs']['subVolumeGroup'] == 'group'
    assert result['spec']['cephFs']['cephCsiSecrets']['controllerPublishSecret']['name'] == 'opaque-secret-ref'
    driver = {'kind': 'Driver', 'spec': {'clusterName': 'ceph', 'controllerPlugin': {'replicas': 2, 'containerExtraArgs': ['credential'], 'volumes': [{'x': 'credential'}]}}}
    assert project_storage_resource(driver)['spec'] == {'clusterName': 'ceph', 'controllerPlugin': {'replicas': 2}}
    assert 'credential' not in json.dumps(result)
    with pytest.raises(OperationError):
        project_storage_resource({'kind': 'Unknown', 'spec': {'x': 'credential'}})


def test_all_storage_tools_dispatch_through_native_contract_validation_and_projection():
    import importlib.util
    from pathlib import Path
    from runtime import Identity
    from plugin_capabilities import operation_contract
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location('storage_dispatch_server', root / 'operations/cloud-platform/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    groups = {'apis/csi.ceph.io/v1', 'apis/objectbucket.io/v1alpha1'}
    for tool in server.GENERATED_PLUGIN_TOOLS.values():
        group = tool['groupVersion']
        if group not in groups:
            continue
        document = json.loads((root / 'operations/plugin-capabilities/openapi' / (group.replace('/', '_') + '.json')).read_text())
        contract = operation_contract(document, tool['operationId'])
        native = document['paths'][contract['pathTemplate']]['get']
        kind = native['x-kubernetes-group-version-kind']['kind']
        resource = {'apiVersion': group.removeprefix('apis/'), 'kind': kind, 'metadata': {'name': 'fixture', 'annotations': {'x': 'credential'}}}
        payload = {'apiVersion': resource['apiVersion'], 'kind': kind + 'List', 'items': [resource]} if native['x-kubernetes-action'] == 'list' else resource
        class NativeService:
            def __init__(self):
                self.calls = []
            def _kubectl(self, argv):
                self.calls.append(argv)
                path = argv[-1]
                body = {'paths': {group: {'serverRelativeURL': '/openapi/v3/' + group}}} if path == '/openapi/v3' else document if path == '/openapi/v3/' + group else payload
                return {'exitCode': 0, 'stdout': json.dumps(body), 'auditRef': 'audit://fixture'}
        service = NativeService()
        args = {'path': {name: 'fixture' for name in contract['inputSchema']['properties']['path']['required']}}
        result = server.dispatch(service, tool['name'], args, Identity('admin', (server.ADMIN_GROUP,)))
        assert result['bodyValidation']['mode'] == 'native_json_body'
        assert 'credential' not in json.dumps(result)
        assert len(service.calls) == 3


def test_deployment_evidence_does_not_apply_to_changed_or_missing_contracts(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    from pathlib import Path
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location('plugin_deployment_evidence', root / 'operations/plugin-capabilities/generate.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    monkeypatch.setattr(generator, 'ROOT', tmp_path)
    tool = {'name': 'fixture.read', 'inputSchema': {'type': 'object'}}
    assert generator.deployed_tools([tool]) == set()
    path = tmp_path / 'operations/plugin-capabilities/deployment-evidence.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'version': 1, 'cluster': 're8ch-k3s', 'tools': [
        {'name': tool['name'], 'inputSchemaSha256': hashlib.sha256(generator.canonical(tool['inputSchema'])).hexdigest()}]}))
    assert generator.deployed_tools([tool]) == {tool['name']}
    assert generator.deployed_tools([{**tool, 'inputSchema': {'type': 'string'}}]) == set()


def test_runtime_registry_matches_pinned_file_source_schema():
    from pathlib import Path
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    root = Path(__file__).parents[1] / 'operations/plugin-capabilities'
    schema = json.loads((root / 'registry-schema/upstream-registry.schema.json').read_text())
    server = json.loads((root / 'registry-schema/server.schema.json').read_text())
    resolver = Registry().with_resource(server['$id'], Resource.from_contents(server))
    validator = Draft202012Validator(schema, registry=resolver, format_checker=FormatChecker())
    validator.validate(json.loads((root / 'registry.runtime.json').read_text()))
    # The previous bare payload is valid for our UI tooling but not a file source.
    assert list(validator.iter_errors(json.loads((root / 'registry.json').read_text())))


def test_runtime_registry_publisher_extensions_match_pinned_schema():
    from pathlib import Path
    from jsonschema import Draft202012Validator
    root = Path(__file__).parents[1] / 'operations/plugin-capabilities'
    schema = json.loads((root / 'registry-schema/publisher-provided.schema.json').read_text())
    validator = Draft202012Validator(schema)
    runtime = json.loads((root / 'registry.runtime.json').read_text())
    for server in runtime['data']['servers']:
        extensions = server['_meta']['io.modelcontextprotocol.registry/publisher-provided']
        validator.validate(extensions)
        invalid = copy.deepcopy(extensions)
        del invalid['io.github.stacklok']['streamable-http']['status']
        assert list(validator.iter_errors(invalid))


def test_plugin_catalog_fits_configured_registry_storage_limit():
    from pathlib import Path
    import yaml
    root = Path(__file__).parents[1]
    release = list(yaml.safe_load_all((root / 'platform/connectool/release.yaml').read_text()))[1]
    limit = release['spec']['values']['toolhive']['registry']['release']['values']['config']['database']['maxMetaSize']
    assert limit == 1024 * 1024
    runtime = json.loads((root / 'operations/plugin-capabilities/registry.runtime.json').read_text())
    for server in runtime['data']['servers']:
        metadata = server['_meta']['io.modelcontextprotocol.registry/publisher-provided']
        assert len(json.dumps(metadata, separators=(',', ':'), ensure_ascii=False).encode()) < limit


def test_cilium_inventory_grants_only_reviewed_bgp_reads():
    from pathlib import Path
    from plugin_capabilities import CILIUM_BGP_READS, CILIUM_NETWORK_READS, CILIUM_POLICY_READS, CILIUM_TOPOLOGY_READS, READ_GROUPS, reviewed_read
    root = Path(__file__).parents[1] / 'operations/plugin-capabilities'
    registry = json.loads((root / 'registry.json').read_text())
    metadata = registry['servers'][0]['_meta']['io.modelcontextprotocol.registry/publisher-provided']['io.github.stacklok']['streamable-http']
    rows = [row for row in metadata['re8ch_capabilities']['operations'] if row['plugin'] == 'cilium']
    assert len(rows) == 275
    assert {row['groupVersion'] for row in rows}.isdisjoint(READ_GROUPS)
    enabled = [row for row in rows if row['registration'] == 'implemented_not_deployed']
    reviewed = CILIUM_BGP_READS | CILIUM_NETWORK_READS | CILIUM_POLICY_READS | CILIUM_TOPOLOGY_READS
    assert len(enabled) == 87 and len(reviewed) == 85
    assert len(CILIUM_BGP_READS) == 26 and len(CILIUM_NETWORK_READS) == 19 and len(CILIUM_POLICY_READS) == 10 and len(CILIUM_TOPOLOGY_READS) == 30
    reads = [row for row in enabled if row['method'] == 'GET']
    writes = [row for row in enabled if row['method'] != 'GET']
    assert {(row['groupVersion'], row['operationId']) for row in reads} == reviewed
    assert all(row['disposition'] == 'read' and row['tool'] for row in reads)
    assert {(row['method'], row['tool']) for row in writes} == {('POST', 'cilium.cidrgroup.create'), ('DELETE', 'cilium.cidrgroup.delete')}
    assert all(row['registration'] == 'not_implemented' and 'tool' not in row for row in rows if not reviewed_read(row['groupVersion'], row['operationId']) and row not in writes)
    assert all(row['disposition'] == 'resource_read_review_required' for row in rows if row['method'] == 'GET' and not reviewed_read(row['groupVersion'], row['operationId']))
    assert all(row['disposition'] == 'controller_owned' for row in rows if row['method'] != 'GET' and row['path'].endswith('/status'))


@pytest.mark.parametrize('operation_id,resource,kind', [
    ('listCiliumIoV2CiliumBGPClusterConfig', 'ciliumbgpclusterconfigs', 'CiliumBGPClusterConfigList'),
    ('listCiliumIoV2CiliumLoadBalancerIPPool', 'ciliumloadbalancerippools', 'CiliumLoadBalancerIPPoolList'),
    ('listCiliumIoV2CiliumClusterwideNetworkPolicy', 'ciliumclusterwidenetworkpolicies', 'CiliumClusterwideNetworkPolicyList'),
    ('listCiliumIoV2CiliumIdentity', 'ciliumidentities', 'CiliumIdentityList'),
])
def test_cilium_reviewed_read_is_bound_to_native_schema_and_other_reads_fail_closed(operation_id, resource, kind):
    from pathlib import Path
    from plugin_capabilities import read_operation
    root = Path(__file__).parents[1]
    key = 'apis/cilium.io/v2'
    document = json.loads((root / 'operations/plugin-capabilities/openapi/apis_cilium.io_v2.json').read_text())
    location = '/openapi/v3/' + key + '?hash=fixture'
    body = {'apiVersion': 'cilium.io/v2', 'kind': kind, 'metadata': {'resourceVersion': '1'}, 'items': []}

    class CiliumService:
        def __init__(self):
            self.calls = []

        def _kubectl(self, argv):
            self.calls.append(argv[-1])
            value = {'paths': {key: {'serverRelativeURL': location}}} if argv[-1] == '/openapi/v3' else document if argv[-1] == location else body
            return {'exitCode': 0, 'stdout': json.dumps(value), 'auditRef': 'audit://cilium'}

    service = CiliumService()
    digest = __import__('hashlib').sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result = read_operation(service, key, operation_id, digest)
    assert result['result']['items'] == []
    assert result['bodyValidation']['validatedObjects'] == 1
    assert service.calls[-1].startswith('/apis/cilium.io/v2/' + resource + '?')
    service.calls.clear()
    with pytest.raises(OperationError) as exc:
        read_operation(service, key, 'createCiliumIoV2CiliumBGPAdvertisement', digest)
    assert exc.value.code == 'PLUGIN_GROUP_NOT_REVIEWED'
    assert service.calls == []


def test_cilium_policy_projection_drops_http_header_matches_and_credentials():
    from plugin_capabilities import project_cilium_policy, sanitize_resource
    raw = {'metadata': {'annotations': {'password': 'sensitive'}},
           'spec': {'egress': [{'toFQDNs': [{'matchName': 'example.org'}],
                               'toPorts': [{'rules': {'http': [{'method': 'GET',
                                                                'headers': ['Authorization: sensitive'],
                                                                'headerMatches': [{'name': 'Authorization', 'value': 'sensitive'}]}]}}]}],
                    'token': 'sensitive'}}
    result = project_cilium_policy(sanitize_resource(raw))
    assert result['spec']['egress'][0]['toFQDNs'][0]['matchName'] == 'example.org'
    assert result['spec']['egress'][0]['toPorts'][0]['rules']['http'] == [{'method': 'GET'}]
    assert 'sensitive' not in json.dumps(result)


def test_cilium_topology_projection_keeps_identity_and_state_without_raw_config():
    from plugin_capabilities import project_cilium_topology
    raw = {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumEnvoyConfigList',
           'metadata': {'resourceVersion': '42', 'continue': 'next-page', 'annotations': {'token': 'sensitive'}},
           'items': [{'kind': 'CiliumEnvoyConfig',
                      'metadata': {'name': 'edge', 'namespace': 'system', 'uid': 'uid-1', 'resourceVersion': '43',
                                   'labels': {'token': 'sensitive'}},
                      'spec': {'resources': [{'inlineBytes': 'sensitive'}]},
                      'status': {'state': 'Ready', 'conditions': [{'type': 'Accepted', 'status': 'True',
                                                                  'reason': 'Programmed', 'message': 'sensitive'}],
                                 'configuration': {'token': 'sensitive'}}}]}
    result = project_cilium_topology(raw)
    assert result['metadata'] == {'resourceVersion': '42', 'continue': 'next-page'}
    assert result['items'][0]['metadata'] == {'name': 'edge', 'namespace': 'system', 'uid': 'uid-1', 'resourceVersion': '43'}
    assert result['items'][0]['status'] == {'state': 'Ready', 'conditions': [{'type': 'Accepted', 'status': 'True', 'reason': 'Programmed'}]}
    assert 'sensitive' not in json.dumps(result)
