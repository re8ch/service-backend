import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "operations/tenant-provider-acceptance/contract.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("tenant_provider_contract", SCRIPT)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def descriptor(name, required=()):
    return {
        "name": "tenant-api-backend_" + name,
        "inputSchema": {
            "type": "object",
            "properties": {field: {"type": "string"} for field in required},
            "required": list(required),
        },
    }


def test_published_schema_requires_every_provider_lifecycle_tool():
    tools = [descriptor(name) for name in sorted(contract.REQUIRED_TOOLS)]
    assert set(contract.inspect_tools(tools)) == contract.REQUIRED_TOOLS
    with pytest.raises(contract.AcceptanceError, match="tools missing"):
        contract.inspect_tools(tools[:-1])
    with pytest.raises(contract.AcceptanceError, match="duplicate"):
        contract.inspect_tools(tools + tools[:1])


def test_generated_cases_are_schema_invalid():
    schema = descriptor("service_instance_get", ("resource_ref",))["inputSchema"]
    assert contract.invalid_cases(schema) == [
        ("missing resource_ref", {}),
        ("wrong type resource_ref", {"resource_ref": ["wrong-type"]}),
    ]


def test_generated_cases_only_target_read_only_operations():
    assert not contract.READ_ONLY_GENERATION.intersection({
        "service_instance_create", "service_instance_suspend", "service_instance_resume", "service_instance_delete",
    })


def test_boundary_runner_rejects_unhandled_schema_invalid_input():
    tools = {name: (name, descriptor(name, ("resource_ref",))["inputSchema"])
             for name in contract.READ_ONLY_GENERATION}

    class MCP:
        def call(self, _method, _params, **_kwargs):
            return {"isError": False}

    with pytest.raises(contract.AcceptanceError, match="accepted schema-invalid input"):
        contract.generated_boundary_checks(MCP(), tools)
