import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
CLI = ROOT / "operations/runtime-anatomy/cli.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("runtime_anatomy_cli", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_parses_volume_and_object_boundaries():
    cli = load_cli()
    assert cli.parse_volume("/workspace=home") == {"mountPath": "/workspace", "claimName": "home"}
    assert cli.parse_volume("/cache") == {"mountPath": "/cache"}
    assert cli.parse_object_scope("artifacts:runs/42") == {"bucket": "artifacts", "prefix": "runs/42"}


def test_cli_start_defaults_to_bounded_content_evidence():
    cli = load_cli()
    args = cli.build_parser().parse_args([
        "-n", "tenant-a", "analyze", "start", "--pod", "worker", "--container", "app",
    ])
    assert args.duration == 60
    assert args.max_snippet_bytes == 4096
    assert args.max_snippet_count == 100
    assert args.max_total_snippet_bytes == 1048576
    assert args.retention == 86400
