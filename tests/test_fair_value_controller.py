import datetime as dt
import importlib.util
import pathlib
import json


ROOT = pathlib.Path(__file__).parents[1]
PATH = ROOT / "platform/observability/opencost-recovery/files/fair-value-controller.py"
SPEC = importlib.util.spec_from_file_location("fair_value", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_quantity_normalizes_resource_amounts_and_never_uses_zero_offer():
    target = {"cpuCores": 4, "memoryGiB": 8, "storageGiB": 80}
    offer = {"cpuCores": 2, "memoryGiB": 4, "storageGiB": 40, "monthlyCNY": 50}
    assert MODULE.quantity_for(target, offer) == 2
    assert MODULE.cheapest_equivalent(target, [offer])[:2] == (100.0, 2)


def test_fair_value_requires_two_fresh_independent_sources():
    now = dt.datetime(2026, 9, 21, tzinfo=dt.timezone.utc).timestamp()
    target = {"name": "node", "cpuCores": 4, "memoryGiB": 8, "storageGiB": 40, "bookMonthlyCNY": 80}
    observation = {"source": "one", "independenceGroup": "one", "monthlyCNY": 100,
                   "observedAt": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()}
    result = MODULE.aggregate(target, [observation], 2, now, 30)
    assert result["status"] == "unknown"
    assert result["reason"] == "insufficient-independent-sources"
    assert result["fairMonthlyCNY"] is None


def test_fair_value_is_median_and_preserves_book_variance():
    now = dt.datetime(2026, 9, 21, tzinfo=dt.timezone.utc).timestamp()
    target = {"name": "node", "cpuCores": 4, "memoryGiB": 8, "storageGiB": 40, "bookMonthlyCNY": 80}
    observations = [
        {"source": source, "independenceGroup": source, "monthlyCNY": value,
         "observedAt": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()}
        for source, value in (("azure", 100), ("linode", 120), ("vultr", 500))
    ]
    result = MODULE.aggregate(target, observations, 2, now, 30)
    assert result["status"] == "usable"
    assert result["fairMonthlyCNY"] == 120
    assert result["varianceCNY"] == 40
    assert result["varianceRatio"] == 0.5


def test_stale_prices_do_not_become_zero_or_chargeable():
    now = dt.datetime(2026, 9, 21, tzinfo=dt.timezone.utc).timestamp()
    target = {"name": "node", "cpuCores": 1, "memoryGiB": 1, "storageGiB": 20, "bookMonthlyCNY": 0}
    stale = dt.datetime.fromtimestamp(now - 31 * 3600, dt.timezone.utc).isoformat()
    observations = [{"source": source, "independenceGroup": source, "monthlyCNY": 10, "observedAt": stale}
                    for source in ("azure", "linode")]
    result = MODULE.aggregate(target, observations, 2, now, 30)
    assert result["status"] == "unknown"
    assert result["fairMonthlyCNY"] is None


def test_incomplete_inventory_is_explicitly_unknown():
    now = dt.datetime(2026, 9, 21, tzinfo=dt.timezone.utc).timestamp()
    target = {"name": "node", "cpuCores": None, "memoryGiB": 12, "storageGiB": 300, "bookMonthlyCNY": 0}
    result = MODULE.aggregate(target, [], 2, now, 30)
    assert result["status"] == "unknown"
    assert result["reason"] == "resource-quantity-incomplete"
    assert result["fairMonthlyCNY"] is None


def test_config_covers_the_full_book_catalog_and_has_three_independent_sources():
    config = json.loads((ROOT / "platform/observability/opencost-recovery/files/fair-value-config.json").read_text())
    book = json.loads((ROOT / "clusters/re8ch/components/network/public-mesh/node-costs.yaml").read_text())
    assert {item["name"] for item in config["targets"]} == {item["name"] for item in book["nodes"]}
    configured = {item["name"]: item["bookMonthlyCNY"] for item in config["targets"]}
    assert configured == {item["name"]: item.get("fixedMonthlyCost") for item in book["nodes"]}
    assert {"azure", "linode", "vultr"} <= set(config["sources"])
    assert config["minimumIndependentSources"] >= 2
    assert config["maximumObservationAgeHours"] < 2 * config["refreshIntervalSeconds"] / 3600 + 24
