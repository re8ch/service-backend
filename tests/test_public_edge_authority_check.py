import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "operations/scripts/check-public-edge-authority.py"
spec = importlib.util.spec_from_file_location("check_public_edge_authority", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def obs(transport, ok, timeout=False, record="SOA", ip="179.253.231.159", zone="api.re8ch.com"):
    return {"ip": ip, "zone": zone, "record": record, "transport": transport,
            "ok": ok, "timeout": timeout, "answers": []}


def test_udp_timeout_with_good_tcp_is_degraded_not_failure():
    observations = [obs("udp", False, timeout=True), obs("tcp", True)]
    assert check.authority_failures(observations) == []
    assert check.udp_path_degradations(observations) == [
        "179.253.231.159: api.re8ch.com SOA udp timeout (tcp authoritative ok)"]


def test_udp_timeout_with_failed_tcp_is_failure():
    observations = [obs("udp", False, timeout=True), obs("tcp", False, timeout=True)]
    assert check.authority_failures(observations) == [
        "179.253.231.159: api.re8ch.com SOA udp timeout",
        "179.253.231.159: api.re8ch.com SOA tcp timeout"]
    assert check.udp_path_degradations(observations) == []


def test_udp_wrong_answer_is_still_mismatch():
    observations = [obs("udp", False, record="NS"), obs("tcp", True, record="NS")]
    assert check.authority_failures(observations) == ["179.253.231.159: api.re8ch.com NS udp mismatch"]


def test_missing_address_record():
    observations = [obs("tcp", False, record="A")]
    assert check.authority_failures(observations) == ["179.253.231.159: api.re8ch.com A tcp missing"]


def test_tcp_success_on_other_server_does_not_excuse_timeout():
    observations = [obs("udp", False, timeout=True), obs("tcp", True, ip="2.28.106.230")]
    assert check.authority_failures(observations) == ["179.253.231.159: api.re8ch.com SOA udp timeout"]


def test_workflow_vantages_run_identical_steps():
    import yaml
    workflow = yaml.safe_load((SCRIPT.parents[2] / ".github/workflows/public-edge-authority.yml").read_text())
    jobs = workflow["jobs"]
    mainland, overseas = jobs["external-dns"], jobs["external-dns-overseas"]
    assert "r640" in mainland["runs-on"] and overseas["runs-on"] == "ubuntu-24.04"
    assert mainland["env"]["PUBLIC_EDGE_VANTAGE"] == "r640"
    assert overseas["env"]["PUBLIC_EDGE_VANTAGE"] == "github-hosted"
    strip = lambda steps: yaml.safe_dump(steps).replace("r640", "V").replace("github-hosted", "V")
    assert strip(mainland["steps"]) == strip(overseas["steps"])
