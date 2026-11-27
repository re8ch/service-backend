from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
GATEWAY = ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml"
PUBLIC_MAIL = ROOT / "platform/stalwart/public-mail.yaml"
CONTEXT = ROOT / "platform/stalwart/MEMORY.md"


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
        namespaces = listener["allowedRoutes"]["namespaces"]
        assert namespaces["from"] == "Selector"
        assert namespaces["selector"]["matchLabels"] == {
            "kubernetes.io/metadata.name": "mail-system"
        }


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


def test_stalwart_context_requires_explicit_atlantic_runtime_evidence():
    context = " ".join(CONTEXT.read_text().split())
    assert "`re8ch-atlantic` is the only Kubernetes runtime authority" in context
    assert "default `re8ch` context" in context
    assert "absence from the main-cluster Node list is expected" in context
    assert "Every observation must record its `clusterRef`" in context
    assert "bounded cloud SSA executor" in context
