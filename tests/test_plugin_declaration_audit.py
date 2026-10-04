import importlib.util
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('plugin_declaration_audit', ROOT / 'operations/plugin-capabilities/audit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_declaration_report_is_current_and_preserves_unimplemented_plugins():
    result = module.audit()
    assert result == json.loads((ROOT / 'operations/plugin-capabilities/declaration-coverage-report.json').read_text())
    rows = {row['plugin']: row for row in result['plugins']}
    assert {'advanced-fabrics', 'public-edge', 'mihomos-cluster', 'rook-ceph', 'anatomy', 'cilium', 'pigsty', 'dex-oidc'} <= rows.keys()
    assert 'unverified_cli_image_digest' in rows['pigsty']['gaps']
    assert rows['pigsty']['implementedDirectoryTools'] == []
    assert 'missing_root_plugin_definition' in rows['anatomy']['gaps']
    assert len(rows['mihomos-cluster']['implementedDirectoryTools']) == 15
    assert all(row['liveRegistration'] == 'not_verified' for row in rows.values())
    assert result['completion'] == 'not_proven'
    assert result['networkRequests'] == 0


def test_external_cluster_proposals_are_not_assumed_to_grant_access():
    result = module.audit()
    external = [row for row in result['clusters'] if row['authority'] == 'external-owner']
    assert {row['cluster'] for row in external} == {'artc', 'qwen'}
    assert all(row['coverage'] == 'owner_access_not_verified' for row in external)


def test_plugin_sources_match_observed_native_contracts():
    definitions = yaml.safe_load((ROOT / 'plugins/platform-services.yaml').read_text())['items']
    definitions = {row['metadata']['name']: row['spec'] for row in definitions}
    edge = definitions['public-edge']['source']
    assert edge['apiGroups'] == ['networking.re8ch.com']
    document = json.loads((ROOT / 'operations/plugin-capabilities/openapi/apis_networking.re8ch.com_v1alpha1.json').read_text())
    assert any(operation.get('x-kubernetes-group-version-kind', {}).get('kind') == 'PublicEdge'
               for item in document['paths'].values() for operation in item.values() if isinstance(operation, dict))
    mihomos = definitions['mihomos-cluster']
    assert mihomos['source']['type'] == 'kubernetes' and 'url' not in mihomos['source']
    assert mihomos['healthCheck']['namespace'] == 'egress-fabric'
    assert (ROOT / mihomos['source']['nativeController']['partialOpenAPI']).is_file()


def test_live_discovery_counts_versioned_schemas_separately_from_metadata():
    import re
    discovery = json.loads((ROOT / 'operations/plugin-capabilities/live-discovery-report.json').read_text())
    schemas = discovery['schemas']
    metadata = discovery['nonGroupVersionEntries']
    pattern = re.compile(r'^(?:api/[a-z0-9]+|apis/[a-z0-9.-]+/[a-z0-9]+)$')
    assert discovery['indexEntries'] == len(schemas) + len(metadata) == 125
    assert discovery['groupVersionEntries'] == len(schemas) == 98
    assert all(pattern.fullmatch(row['groupVersion']) for row in schemas)
    assert all(not pattern.fullmatch(key) for key in metadata)
    assert sum(row['coverage'] == 'not_captured' for row in schemas) == 92
    result = module.audit()['nativeSchemaDiscovery']
    assert result['capturedSchemas'] == 6 and result['uncapturedSchemas'] == 92
    assert result['pluginIdentityCoverage'] == 'not_proven'


def test_live_schema_parity_has_exact_source_digests_and_independent_acceptance_states():
    import hashlib
    directory = ROOT / 'operations/plugin-capabilities'
    report = json.loads((directory / 'live-schema-parity-report.json').read_text())
    matrix = json.loads((directory / 'native-operation-matrix.json').read_text())
    expected = {}
    for source in matrix['sources']:
        document = json.loads((directory / source['schemaFile']).read_text())
        expected[source['groupVersion']] = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert len(report['operations']) == 6
    assert {row['groupVersion'] for row in report['operations']} < set(expected)
    for row in report['operations']:
        assert row['matches'] is True
        assert row['liveHash'] == row['capturedHash'] == expected[row['groupVersion']]
        assert row['source'].split('?')[0] == '/openapi/v3/' + row['groupVersion']
        assert row['auditRef'].startswith('audit://operations/')
    assert report['httpConformance'] == report['semanticConformance'] == 'not_run'
