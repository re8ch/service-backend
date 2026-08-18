#!/usr/bin/env python3
"""Reconcile and audit the RouterOS side of the RE8CH dual route-server fabric.

Run this inside the ``routeros-node-controller`` pod.  The script reads the
existing ``ops/mikrotik-routeros-admin`` Secret through the pod service account;
it never accepts or prints credential material.
"""

import argparse
import base64
import json
import ssl
import time
import urllib.error
import urllib.request

KUBE_API = "https://kubernetes.default.svc"
KUBE_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
ROUTEROS_API = "http://192.0.2.240/rest"
SECRET_PATH = "/api/v1/namespaces/ops/secrets/mikrotik-routeros-admin"

CONNECTIONS = (
    ("re8ch-pod-rs-a1", "192.0.2.176/32", "4200010147"),
    ("re8ch-pod-rs-b1", "192.0.2.4/32", "4200010125"),
)

FILTER_RULES = (
    (
        "re8ch-rs-pod-in",
        "dual-rs accept remote PodCIDRs",
        "if (dst in 192.0.2.57/16 && dst-len==24 && dst!=192.0.2.140/24) { accept }",
    ),
    ("re8ch-rs-pod-in", "dual-rs reject all imports", "reject"),
    (
        "re8ch-rs-pod-out",
        "dual-rs export restricted-node-6dc3f140f9 PodCIDR only",
        "if (dst==192.0.2.140/24) { accept }",
    ),
    ("re8ch-rs-pod-out", "dual-rs reject all exports", "reject"),
)


def kubernetes_secret():
    token = open(KUBE_TOKEN, encoding="utf-8").read().strip()
    request = urllib.request.Request(
        KUBE_API + SECRET_PATH,
        headers={"Authorization": "Bearer " + token},
    )
    context = ssl.create_default_context(cafile=KUBE_CA)
    payload = json.load(urllib.request.urlopen(request, context=context, timeout=10))
    return {key: base64.b64decode(value).decode() for key, value in payload["data"].items()}


class RouterOS:
    def __init__(self):
        secret = kubernetes_secret()
        raw = f'{secret["username"]}:{secret["password"]}'.encode()
        self.auth = base64.b64encode(raw).decode()
        self.context = ssl.create_default_context()
        self.context.check_hostname = False
        self.context.verify_mode = ssl.CERT_NONE

    def request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            ROUTEROS_API + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Basic " + self.auth,
                "Content-Type": "application/json",
            },
        )
        try:
            response = urllib.request.urlopen(request, context=self.context, timeout=15)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"RouterOS {method} {path}: {detail}") from error
        raw = response.read()
        return json.loads(raw) if raw else {}


def reconcile(router):
    existing_rules = {
        (rule.get("chain"), rule.get("comment"))
        for rule in router.request("/routing/filter/rule")
    }
    for chain, comment, rule in FILTER_RULES:
        if (chain, comment) not in existing_rules:
            router.request(
                "/routing/filter/rule",
                "PUT",
                {"chain": chain, "comment": comment, "rule": rule},
            )

    existing_connections = {
        connection.get("name"): connection
        for connection in router.request("/routing/bgp/connection")
    }
    for name, remote, remote_as in CONNECTIONS:
        if name in existing_connections:
            continue
        router.request(
            "/routing/bgp/connection",
            "PUT",
            {
                "name": name,
                "comment": "RE8CH dual PodCIDR route server",
                "instance": "re8ch-pod",
                "afi": "ip",
                "as": "4200064001",
                "local.address": "192.0.2.178",
                "local.role": "ebgp",
                "remote.address": remote,
                "remote.as": remote_as,
                "input.filter": "re8ch-rs-pod-in",
                "output.filter-chain": "re8ch-rs-pod-out",
                "routing-table": "main",
            },
        )


def set_static_routes(router, disabled):
    changed = []
    routes = router.request(
        "/ip/route?.proplist=.id,dst-address,comment,disabled,dynamic"
    )
    for route in routes:
        if route.get("comment") != "re8ch-routerosnode":
            continue
        if route.get("dynamic") == "true":
            continue
        router.request(
            "/ip/route/" + route[".id"],
            "PATCH",
            {"disabled": "true" if disabled else "false"},
        )
        changed.append(route["dst-address"])
    return sorted(changed)


def set_connection(router, name, disabled):
    connection = next(
        item
        for item in router.request("/routing/bgp/connection")
        if item.get("name") == name
    )
    router.request(
        "/routing/bgp/connection/" + connection[".id"],
        "PATCH",
        {"disabled": "true" if disabled else "false"},
    )


def snapshot(router):
    sessions = [
        {
            "name": session.get("name"),
            "established": session.get("established") == "true",
            "remote": session.get("remote.address"),
            "prefixCount": int(session.get("prefix-count", "0")),
        }
        for session in router.request(
            "/routing/bgp/session?.proplist=name,established,prefix-count,remote.address"
        )
        if session.get("name", "").startswith("re8ch-")
    ]
    routes = router.request(
        "/ip/route?.proplist=dst-address,gateway,active,dynamic,disabled,comment"
    )
    dynamic = sorted(
        {
            (route.get("dst-address"), route.get("gateway"))
            for route in routes
            if route.get("dynamic") == "true"
            and route.get("dst-address", "").startswith("10.42.")
        }
    )
    disabled_static = sorted(
        route.get("dst-address")
        for route in routes
        if route.get("comment") == "re8ch-routerosnode"
        and route.get("disabled") == "true"
    )
    return {
        "sessions": sessions,
        "dynamicPodRoutes": [dict(zip(("prefix", "nextHop"), route)) for route in dynamic],
        "disabledStaticPodRoutes": disabled_static,
    }


def failover(router, connection):
    set_connection(router, connection, True)
    try:
        time.sleep(12)
        result = snapshot(router)
    finally:
        set_connection(router, connection, False)
        time.sleep(5)
    result["temporarilyDisabled"] = connection
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("observe", "apply", "disable-static", "enable-static", "test-a1", "test-b1"),
    )
    args = parser.parse_args()
    router = RouterOS()
    result = None
    if args.action == "apply":
        reconcile(router)
    elif args.action == "disable-static":
        result = {"disabled": set_static_routes(router, True)}
    elif args.action == "enable-static":
        result = {"enabled": set_static_routes(router, False)}
    elif args.action == "test-a1":
        result = failover(router, "re8ch-pod-rs-a1")
    elif args.action == "test-b1":
        result = failover(router, "re8ch-pod-rs-b1")
    print(json.dumps(result or snapshot(router), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
