import importlib.util
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "k6_summary", Path(__file__).resolve().parents[1] / "operations/k6/summarize.py"
)
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def test_summary_shows_real_k6_fields_and_per_contract_result():
    data = {
        "metrics": {
            "checks": {"passes": 2, "fails": 1},
            "contract_failures": {"value": 1 / 3},
            "http_req_duration": {"p(95)": 1234.5},
            "http_reqs": {"count": 3},
        },
        "root_group": {"checks": {"auth-discovery contract": {"passes": 2, "fails": 1}}},
    }
    result = summary.render(data, "standard")
    assert "2 passed / 1 failed" in result
    assert "33.3%" in result
    assert "1234 ms" in result
    assert "| auth-discovery contract | 2 | 1 |" in result
