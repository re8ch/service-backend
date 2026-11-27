from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
GATEWAY = ROOT / "operations/network/manifests/cilium/gateway-api-canary.yaml"
PUBLIC_MAIL = ROOT / "platform/stalwart/public-mail.yaml"


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_public_mail_routes_reference_matching_tcp_gateway_listeners():
    gateway = next(item for item in documents(GATEWAY) if item["kind"] == "Gateway")
    listeners = {item["name"]: item for item in gateway["spec"]["listeners"]}
    routes = documents(PUBLIC_MAIL)

    assert {item["kind"] for item in routes} == {"TCPRoute"}
    assert len(routes) == 5

    for route in routes:
        parent = route["spec"]["parentRefs"][0]
        assert parent["name"] == gateway["metadata"]["name"]
        assert parent["namespace"] == gateway["metadata"]["namespace"]

        listener = listeners[parent["sectionName"]]
        backend = route["spec"]["rules"][0]["backendRefs"][0]
        assert listener["protocol"] == "TCP"
        assert listener["port"] == backend["port"]
        assert listener["allowedRoutes"]["namespaces"]["from"] == "All"


def test_public_mail_declares_all_required_transport_ports():
    gateway = next(item for item in documents(GATEWAY) if item["kind"] == "Gateway")
    listeners = {item["name"]: item["port"] for item in gateway["spec"]["listeners"]}
    assert {name: listeners[name] for name in (
        "mail-smtp",
        "mail-smtps",
        "mail-submission",
        "mail-imaps",
        "mail-sieve",
    )} == {
        "mail-smtp": 25,
        "mail-smtps": 465,
        "mail-submission": 587,
        "mail-imaps": 993,
        "mail-sieve": 4190,
    }
