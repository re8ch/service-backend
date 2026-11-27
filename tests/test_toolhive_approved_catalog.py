import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).parents[1]
CATALOG = ROOT / "operations/toolhive-catalog/registry.json"


def test_approved_catalog_is_the_authenticated_re8ch_entry():
    catalog = json.loads(CATALOG.read_text())

    assert len(catalog["data"]["servers"]) == 1
    server = catalog["data"]["servers"][0]
    assert (server["name"], server["title"]) == ("com.re8ch/re8ch", "RE8CH")
    assert server["remotes"] == [{
        "type": "streamable-http",
        "url": "https://tools.service.re8ch.com/tenant-native/mcp",
    }]
    assert "organization, membership, grants" in server["description"]
    stream = server["_meta"]["io.modelcontextprotocol.registry/publisher-provided"]["io.github.stacklok"]["streamable-http"]
    assert len(stream["re8ch_capabilities"]["operations"]) == 893
    assert stream["tool_definitions"]
    serialized = json.dumps(catalog)
    assert "cluster-plugin-operations" not in serialized
    assert "operate.tools.re8ch.com" not in serialized


def test_approved_catalog_matches_the_pinned_toolhive_file_source_schema():
    schemas = ROOT / "operations/plugin-capabilities/registry-schema"
    schema = json.loads((schemas / "upstream-registry.schema.json").read_text())
    server = json.loads((schemas / "server.schema.json").read_text())
    resolver = Registry().with_resource(server["$id"], Resource.from_contents(server))
    validator = Draft202012Validator(schema, registry=resolver, format_checker=FormatChecker())

    validator.validate(json.loads(CATALOG.read_text()))


def test_connectool_approved_registry_uses_only_the_unified_file_source():
    release = list(yaml.safe_load_all((ROOT / "platform/connectool/release.yaml").read_text()))[1]
    registry = release["spec"]["values"]["toolhive"]["registry"]
    expected = [{
        "name": "re8ch-unified",
        "file": {"path": "/etc/re8ch-approved-catalog/registry.json"},
        "syncPolicy": {"interval": "1m"},
    }]

    assert registry["sources"] == expected
    values = registry["release"]["values"]
    assert values["config"]["sources"] == expected
    assert values["config"]["registries"] == [{
        "name": "approved",
        "sources": ["re8ch-unified"],
    }]
    assert values["extraVolumes"] == [{
        "name": "re8ch-approved-catalog",
        "configMap": {"name": "re8ch-approved-tool-catalog"},
    }]
