"""The runner must accept a digest-pinned YAML schema with the JSON CLI path."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


BASE = Path(__file__).parents[1] / "operations/api-conformance"


def test_yaml_source_is_rendered_as_json_for_schemathesis(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(BASE))
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location("conformance_yaml_runner", BASE / "runner.py")
    )
    module.__spec__.loader.exec_module(module)
    raw = b"""openapi: 3.0.3
info: {title: Test, version: '1'}
paths:
  /health:
    get:
      operationId: commerceHealth
      responses:
        '200': {description: OK}
"""
    target = {
        "repository": "re8ch/cloud-functions", "baseUrl": "https://api.re8ch.com/commerce",
        "schema": {"url": "https://api.re8ch.com/commerce/v1/openapi.yaml",
                   "sha256": hashlib.sha256(raw).hexdigest()},
        "network": {"allowedHosts": ["api.re8ch.com"]},
        "operations": {"readOperationIds": ["commerceHealth"], "mutationOperationIds": []},
        "checks": {"exclude": ["unsupported_method"],
                   "rationale": "Envoy rejects HTTP/1.1 QUERY before routing while HTTP/2 returns 405."},
        "limits": {"maxExamples": 1, "maxRequests": 10, "ratePerSecond": 1, "timeoutSeconds": 60},
    }
    monkeypatch.setenv("TARGET_NAME", "commerce-public-contract")
    monkeypatch.setenv("RUN_ID", "yaml-regression")
    monkeypatch.setenv("TARGET_JSON", json.dumps(target))
    monkeypatch.setattr(module, "fetch_schema", lambda _: raw)
    observed = []

    def fake_run(command, **kwargs):
        schema_path = Path(command[command.index("run") + 1])
        observed.append(json.loads(schema_path.read_text()))
        assert command[command.index("--exclude-checks") + 1] == "unsupported_method"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main() == 0
    assert observed[0]["paths"]["/health"]["get"]["operationId"] == "commerceHealth"
    assert json.loads(capsys.readouterr().out)["schemaSha256"] == hashlib.sha256(raw).hexdigest()
