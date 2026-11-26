import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "operations/github-transaction-context/bulk_automation_backfill.py"
SPEC = importlib.util.spec_from_file_location("bulk_automation_backfill", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_dependency_factor_title_is_scoped():
    assert MODULE.slug_title("dependency-update:example/repo:npm_and_yarn") == "Track npm_and_yarn dependency updates for example/repo"


def test_promoter_factor_title_is_scoped():
    assert MODULE.slug_title("domain-oci-promotion:re8ch/service-backend") == "Track domain OCI promotions for re8ch/service-backend"


def test_human_factor_title_is_scoped_to_one_pull_request():
    assert MODULE.slug_title("pull-request-context:example/repo:42") == "Track example/repo#42"


def test_append_closing_preserves_body_and_is_idempotent():
    original = "Original body\n"
    first = MODULE.append_closing(original, "https://github.com/example/repo/issues/9", "dependency-update:example/repo:pip")
    second = MODULE.append_closing(first, "https://github.com/example/repo/issues/9", "dependency-update:example/repo:pip")
    assert first.startswith("Original body")
    assert first == second
    assert "Closes https://github.com/example/repo/issues/9" in first


def test_factor_body_forbids_implicit_merge_or_close():
    body = MODULE.factor_body("dependency-update:example/repo:pip", "re8ch/service-backend#3126", 4)
    assert "does not authorize merging or closing" in body
    assert "Planned PR count: 4" in body


def test_ensure_parent_is_idempotent_when_factor_is_already_attached():
    class Client:
        def __init__(self):
            self.calls = []

        def request(self, url, method="GET", data=None):
            self.calls.append((url, method, data))
            return [{"id": 42}]

    client = Client()
    MODULE.ensure_parent(client, {"id": 42}, "re8ch/service-backend#3126")
    assert client.calls == [("/repos/re8ch/service-backend/issues/3126/sub_issues?per_page=100", "GET", None)]
