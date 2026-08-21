#!/usr/bin/env python3
"""Small Kubernetes controller for devices.re8ch.com DeviceService objects."""
from __future__ import annotations

import ipaddress
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.getenv("KUBERNETES_SERVICE_HOST")
BASE = f"https://{API}:{os.getenv('KUBERNETES_SERVICE_PORT_HTTPS', '443')}"
TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
GROUP = "devices.re8ch.com"
VERSION = "v1alpha1"
MANAGED_BY = "device-fabric-controller.devices.re8ch.com"
INTERVAL = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "15"))


def safe_ip(value: str) -> str:
    address = ipaddress.ip_address(value)
    if address.version != 4:
        raise ValueError(f"IPv6 endpoint {value} is not supported by the v1alpha1 controller")
    if address.is_unspecified or address.is_loopback or address.is_multicast or address.is_link_local:
        raise ValueError(f"unsafe endpoint address {value}")
    if address in ipaddress.ip_network("192.0.2.148/24") or address in ipaddress.ip_network("192.0.2.139/24") or address in ipaddress.ip_network("192.0.2.19/24"):
        raise ValueError(f"documentation endpoint address {value}")
    return str(address)


def desired_objects(service: dict, device: dict) -> list[dict]:
    spec = service["spec"]
    namespace, name = service["metadata"]["namespace"], service["metadata"]["name"]
    connection = next((x for x in device["spec"]["connections"] if x["name"] == spec["connectionName"]), None)
    if not connection or connection["type"] != "Network":
        raise ValueError("referenced Network connection not found")
    addresses = [safe_ip(x) for x in connection["network"]["addresses"]]
    protocol = spec["protocol"]
    if protocol not in {"HTTP", "HTTPS", "TCP", "UDP", "TLS"}:
        raise ValueError(f"protocol {protocol} is handled by a dedicated adapter")
    exposure = spec["exposure"]
    if exposure.get("visibility") == "Authenticated":
        authentication = spec.get("authentication") or {}
        if authentication.get("mode") != "Upstream":
            raise ValueError("only explicit upstream authentication is supported by the stable controller")
    transport = "UDP" if protocol == "UDP" else "TCP"
    app_protocol = "https" if protocol == "HTTPS" else ("http" if protocol == "HTTP" else None)
    labels = {"app.kubernetes.io/managed-by": "device-fabric-controller", "devices.re8ch.com/service": name}
    owner = [{"apiVersion": f"{GROUP}/{VERSION}", "kind": "DeviceService", "name": name,
              "uid": service["metadata"]["uid"], "controller": True, "blockOwnerDeletion": True}]
    port = {"name": "device", "protocol": transport, "port": spec["port"], "targetPort": spec["port"]}
    if app_protocol:
        port["appProtocol"] = app_protocol
    objects = [{"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": namespace,
                "labels": labels, "ownerReferences": owner}, "spec": {"ports": [port]}},
               {"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice",
                "metadata": {"name": f"{name}-ipv4", "namespace": namespace,
                  "labels": {**labels, "kubernetes.io/service-name": name,
                             "endpointslice.kubernetes.io/managed-by": MANAGED_BY}, "ownerReferences": owner},
                "addressType": "IPv4", "ports": [{"name": "device", "protocol": transport, "port": spec["port"]}],
                "endpoints": [{"addresses": addresses, "conditions": {"ready": True}}]}]
    if exposure["mode"] == "Gateway":
        gateway = exposure["gatewayRef"]
        parent = {"name": gateway["name"], "namespace": gateway["namespace"]}
        if gateway.get("sectionName"):
            parent["sectionName"] = gateway["sectionName"]
        if protocol in {"HTTP", "HTTPS"}:
            objects.append({"apiVersion": "gateway.networking.k8s.io/v1", "kind": "HTTPRoute",
              "metadata": {"name": name, "namespace": namespace, "labels": labels, "ownerReferences": owner},
              "spec": {"parentRefs": [parent], "hostnames": [exposure["hostname"]],
                       "rules": [{"backendRefs": [{"name": name, "port": spec["port"]}]}]}})
        else:
            route_kind = {"TLS": "TLSRoute", "TCP": "TCPRoute", "UDP": "UDPRoute"}[protocol]
            objects.append({"apiVersion": "gateway.networking.k8s.io/v1alpha2", "kind": route_kind,
              "metadata": {"name": name, "namespace": namespace, "labels": labels, "ownerReferences": owner},
              "spec": {"parentRefs": [parent], "rules": [{"backendRefs": [{"name": name, "port": spec["port"]}]}]}})
    return objects


class Kube:
    def __init__(self):
        self.token = open(TOKEN_FILE).read().strip()
        self.context = ssl.create_default_context(cafile=CA_FILE)

    def call(self, path, method="GET", value=None, content_type="application/json"):
        data = None if value is None else json.dumps(value).encode()
        req = urllib.request.Request(BASE + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": content_type, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=10) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError(f"Kubernetes API {method} {path}: {exc.code} {exc.read().decode()[:500]}") from exc

    def apply(self, obj):
        ns, name = obj["metadata"]["namespace"], obj["metadata"]["name"]
        kind = obj["kind"]
        mapping = {
          "Service": ("/api/v1", "services"), "EndpointSlice": ("/apis/discovery.k8s.io/v1", "endpointslices"),
          "HTTPRoute": ("/apis/gateway.networking.k8s.io/v1", "httproutes"),
          "TLSRoute": ("/apis/gateway.networking.k8s.io/v1alpha2", "tlsroutes"),
          "TCPRoute": ("/apis/gateway.networking.k8s.io/v1alpha2", "tcproutes"),
          "UDPRoute": ("/apis/gateway.networking.k8s.io/v1alpha2", "udproutes")}
        prefix, plural = mapping[kind]
        path = f"{prefix}/namespaces/{ns}/{plural}/{name}?fieldManager=device-fabric-controller&force=true"
        return self.call(path, "PATCH", obj, "application/apply-patch+yaml")

    def patch_status(self, service, ready, reason, message, refs=None):
        ns, name = service["metadata"]["namespace"], service["metadata"]["name"]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        status = {"observedGeneration": service["metadata"].get("generation", 0),
                  "conditions": [{"type": "Ready", "status": "True" if ready else "False", "reason": reason,
                                  "message": message[:1024], "observedGeneration": service["metadata"].get("generation", 0),
                                  "lastTransitionTime": now}]}
        status.update(refs or {})
        path = f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/deviceservices/{name}/status"
        self.call(path, "PATCH", {"status": status}, "application/merge-patch+json")


def reconcile(kube: Kube):
    response = kube.call(f"/apis/{GROUP}/{VERSION}/deviceservices") or {"items": []}
    for service in response["items"]:
        ns, device_name = service["metadata"]["namespace"], service["spec"]["deviceRef"]["name"]
        try:
            device = kube.call(f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/edgedevices/{device_name}")
            if not device:
                raise ValueError(f"EdgeDevice {device_name} not found")
            objects = desired_objects(service, device)
            for obj in objects:
                kube.apply(obj)
            refs = {"serviceRef": service["metadata"]["name"], "endpointSliceRef": f"{service['metadata']['name']}-ipv4"}
            route = next((x for x in objects if x["kind"].endswith("Route")), None)
            if route:
                refs["routeRef"] = route["metadata"]["name"]
            kube.patch_status(service, True, "Reconciled", "native resources applied", refs)
        except Exception as exc:
            print(json.dumps({"level": "error", "service": f"{ns}/{service['metadata']['name']}", "error": str(exc)}), flush=True)
            kube.patch_status(service, False, "ReconcileFailed", str(exc))


def main():
    kube = Kube()
    while True:
        reconcile(kube)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
