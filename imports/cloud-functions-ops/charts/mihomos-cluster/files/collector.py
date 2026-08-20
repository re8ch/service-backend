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

def request(url, opener=DIRECT, timeout=8):
    started = time.monotonic()
    try:
        with opener.open(url, timeout=timeout) as response:
            return {"ok": True, "status": response.status, "latencyMs": round((time.monotonic()-started)*1000)}
    except urllib.error.HTTPError as exc:
        return {"ok": exc.code in (401, 403), "status": exc.code, "latencyMs": round((time.monotonic()-started)*1000)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "latencyMs": round((time.monotonic()-started)*1000)}

def api(path, method="GET", body=None):
    req = urllib.request.Request(API + path, method=method, headers=HEADERS,
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, context=CTX, timeout=8) as response:
        return json.load(response) if response.length else {}

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
    geo = labels.get("networking.re8ch.com/geo", "unknown")
    try:
        with DIRECT.open(os.getenv("IP_LOOKUP_URL", "https://ipinfo.io/json"), timeout=8) as response:
            identity = json.load(response)
    except Exception as exc:
        identity = {"error": type(exc).__name__}
    proxy = urllib.request.build_opener(urllib.request.ProxyHandler({"http":os.environ["LOCAL_PROXY"], "https":os.environ["LOCAL_PROXY"]}))
    probes = {}
    for target_geo, targets in json.loads(os.environ["PROBES_JSON"]).items():
        probes[target_geo] = {item["name"]: request(item["url"], proxy) for item in targets}
    return {"node":NODE,"timestamp":int(time.time()),"declaredGeo":geo,"region":labels.get("topology.kubernetes.io/region"),
            "publicIdentity":identity,"consumer":request(os.environ["LOCAL_CONTROLLER"]),
            "isCnExit":labels.get("networking.re8ch.com/mihomo-exit-cn") == "enabled",
            "isGlobalExit":labels.get("networking.re8ch.com/mihomo-exit") == "enabled","probes":probes}

while True:
    try: publish(collect())
    except Exception as exc: print(json.dumps({"node":NODE,"error":repr(exc)}), flush=True)
    time.sleep(int(os.getenv("INTERVAL_SECONDS", "60")))
