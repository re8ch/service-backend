import importlib.util
from pathlib import Path
from unittest import mock


PATH = Path(__file__).parents[1] / "platform/public-edge-manager/nameserver-election.py"
SPEC = importlib.util.spec_from_file_location("nameserver_election", PATH)
election = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(election)


def node(name, ip=None, ready="True", annotations=None, region=""):
    return {"metadata": {"name": name, "annotations": annotations or {},
                         "labels": {"topology.kubernetes.io/region": region} if region else {}},
            "status": {"addresses": [{"type": "ExternalIP", "address": ip}] if ip else [],
                       "conditions": [{"type": "Ready", "status": ready}]}}


def test_external_ip_discovery_does_not_require_kubernetes_ready():
    nodes = {"items": [node("one", "192.0.2.72", "False",
                            {election.NS_ANNOTATION: "ns-one.re8ch.com."}, "cn-shanghai"),
                       node("private", "192.0.2.88"), node("no-ip")]}
    candidates = election.discover(nodes, {"items": []})
    assert candidates == [{"node": "one", "ip": "192.0.2.72",
                           "ns": "ns-one.re8ch.com.", "capacityMbps": 1,
                           "region": "cn-shanghai"}]


def test_election_uses_available_count_up_to_three_and_spreads_regions():
    candidates = [
        {"node": "cn-fast", "ns": "ns-cn-fast.re8ch.com.", "region": "CN", "capacityMbps": 100},
        {"node": "cn-slow", "ns": "ns-cn-slow.re8ch.com.", "region": "CN", "capacityMbps": 3},
        {"node": "us", "ns": "ns-us.re8ch.com.", "region": "US", "capacityMbps": 20},
        {"node": "my", "ns": "ns-my.re8ch.com.", "region": "MY", "capacityMbps": 10},
    ]
    assert [item["node"] for item in election.elect(candidates[:2])] == ["cn-fast", "cn-slow"]
    assert [item["node"] for item in election.elect(candidates)] == ["cn-fast", "us", "my"]


def test_candidate_needs_authoritative_soa_on_both_transports_for_all_zones():
    candidate = {"node": "one", "ip": "192.0.2.72"}
    with mock.patch.object(election, "dns_query", return_value=["ok"]) as query, \
         mock.patch.object(election.time, "sleep"):
        assert election.probe(candidate) is candidate
    assert query.call_count == len(election.CHILD_ZONES) * 2 * 2
    assert {call.kwargs["tcp"] for call in query.call_args_list} == {True, False}
    with mock.patch.object(election, "dns_query", side_effect=ValueError("not authoritative")):
        assert election.probe(candidate) is None
