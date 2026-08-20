#!/usr/bin/env python3
import json, os, socket, ssl, time, urllib.error, urllib.request

API = "https://kubernetes.default.svc"
NS = os.getenv("POD_NAMESPACE", "egress-fabric")
NODE = os.environ["NODE_NAME"]
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read().strip()
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
CTX = ssl.create_default_context(cafile=CA)
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
KUBE_API = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=CTX)
)

def request(url, opener=DIRECT, timeout=8, expected_status=None):
    started = time.monotonic()
    try:
        with opener.open(url, timeout=timeout) as response:
            ok = response.status == expected_status if expected_status is not None else 200 <= response.status < 400
            return {"ok": ok, "status": response.status, "latencyMs": round((time.monotonic()-started)*1000)}
    except urllib.error.HTTPError as exc:
        ok = exc.code == expected_status if expected_status is not None else exc.code in (401, 403)
        return {"ok": ok, "status": exc.code, "latencyMs": round((time.monotonic()-started)*1000)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "latencyMs": round((time.monotonic()-started)*1000)}

def api(path, method="GET", body=None):
    req = urllib.request.Request(API + path, method=method, headers=HEADERS,
                                 data=None if body is None else json.dumps(body).encode())
    with KUBE_API.open(req, timeout=8) as response:
        payload = response.read()
        return json.loads(payload) if payload else {}

def publish(status):
    name = "mihomos-status-" + NODE.lower().replace("_", "-")
    body = {"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":name,"namespace":NS,
            "labels":{"app.kubernetes.io/component":"mihomos-node-status","mihomos.re8ch.com/node":NODE}},
            "data":{"status.json":json.dumps(status, sort_keys=True)}}
    path = f"/api/v1/namespaces/{NS}/configmaps/{name}"
    try:
        current = api(path)
        body["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
        api(path, "PUT", body)
    except urllib.error.HTTPError as exc:
        if exc.code != 404: raise
        api(f"/api/v1/namespaces/{NS}/configmaps", "POST", body)

def collect():
    node = api(f"/api/v1/nodes/{NODE}")
    labels = node.get("metadata", {}).get("labels", {})
    geo = os.getenv("DECLARED_GEO") or labels.get("networking.re8ch.com/geo", "unknown")
    try:
        with DIRECT.open(os.getenv("IP_LOOKUP_URL", "https://ipinfo.io/json"), timeout=8) as response:
            identity = json.load(response)
    except Exception as exc:
        identity = {"error": type(exc).__name__}
    proxy = urllib.request.build_opener(urllib.request.ProxyHandler({"http":os.environ["LOCAL_PROXY"], "https":os.environ["LOCAL_PROXY"]}))
    probes = {}
    for target_geo, targets in json.loads(os.environ["PROBES_JSON"]).items():
        probes[target_geo] = {item["name"]: request(item["url"], proxy, expected_status=item.get("expectedStatus")) for item in targets}
    controller = request(os.environ["LOCAL_CONTROLLER"])
    probe_results = [result for group in probes.values() for result in group.values()]
    successful = [result for result in probe_results if result.get("ok")]
    consumer = {"ok": bool(successful), "controllerOk": controller.get("ok", False),
                "latencyMs": min((result.get("latencyMs", 0) for result in successful), default=controller.get("latencyMs"))}
    return {"node":NODE,"timestamp":int(time.time()),"declaredGeo":geo,"region":os.getenv("NODE_REGION") or labels.get("topology.kubernetes.io/region"),
            "publicIdentity":identity,"consumer":consumer,"controller":controller,
            "isCnExit":os.getenv("CN_EXIT") == "enabled" or labels.get("networking.re8ch.com/mihomo-exit-cn") == "enabled",
            "isGlobalExit":os.getenv("GLOBAL_EXIT") == "enabled" or labels.get("networking.re8ch.com/mihomo-exit") == "enabled","probes":probes}

while True:
    try: publish(collect())
    except Exception as exc: print(json.dumps({"node":NODE,"error":repr(exc)}), flush=True)
    time.sleep(int(os.getenv("INTERVAL_SECONDS", "60")))
