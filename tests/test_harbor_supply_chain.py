import importlib.util
import json
from pathlib import Path

import pytest


MODULE = Path(__file__).parents[1] / "clusters/re8ch/components/ai-operations/harbor_supply_chain.py"
spec = importlib.util.spec_from_file_location("harbor_supply_chain", MODULE)
supply = importlib.util.module_from_spec(spec); spec.loader.exec_module(supply)


class Response:
    status = 202
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps(self.value).encode()


def test_build_contract_forces_attestations_and_tenant_target():
    captured = {}
    def opener(request, timeout):
        captured.update(json.loads(request.data)); return Response({"operationId": "build-1"})
    client = supply.SupplyChainClient("https://worker.example", "service-token", opener)
    result = client.start_build("canary", "canary", {"context": "https://git.example/a.git", "revision": "abc", "dockerfile": "Dockerfile", "repository": "demo", "tag": "v1", "credential_ref": "credential://harbor/canary/" + "a" * 32}, "idem-1")
    assert result["operationId"] == "build-1"
    assert captured["target"] == "canary/demo:v1"
    assert captured["attestations"] == {"sbom": True, "provenance": "mode=max"}


@pytest.mark.parametrize("context,dockerfile", [("file:///tmp/a", "Dockerfile"), ("https://git/a", "../Dockerfile")])
def test_build_rejects_local_or_traversing_context(context, dockerfile):
    client = supply.SupplyChainClient("https://worker.example", "service-token", lambda *_a, **_k: None)
    with pytest.raises(ValueError):
        client.start_build("canary", "canary", {"context": context, "dockerfile": dockerfile, "repository": "demo", "tag": "v1"}, "idem")
