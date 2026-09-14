import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).parents[1]

def test_pipeline_archive_schema_accepts_empty_export():
    schema = json.loads((ROOT / "schemas/pipeline-archive-v1.schema.json").read_text())
    value = {
        "schema": "collaboration.re8ch.com/pipeline-archive/v1",
        "sourceProject": "reachievellc/service-backend",
        "exportedAt": "2026-09-14T00:00:00+00:00",
        "cutoffPipelineId": None,
        "pipelines": [],
        "assets": [],
        "summary": {"pipelineCount": 0, "jobCount": 0, "downloadedArtifacts": 0, "missingArtifacts": 0, "errors": 0},
    }
    jsonschema.validate(value, schema, format_checker=jsonschema.FormatChecker())

def test_exporter_never_accepts_token_as_cli_argument():
    source = (ROOT / "operations/migrations/github-first-control-plane/export_gitlab_pipelines.py").read_text()
    assert 'os.environ.get("GITLAB_TOKEN")' in source
    assert 'shutil.which("glab")' in source
    assert 'class Glab:' in source
    assert 'add_argument("--token"' not in source
    assert "PRIVATE-TOKEN" in source
