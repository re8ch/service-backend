#!/usr/bin/env python3
"""RE8CH Route + Path Controller.

The controller deliberately separates decisions from data-plane mutation.  It
accepts observations, exposes auditable decisions/metrics, and continues to
drive the existing Mihomo selector.  NetBird writes are disabled unless a
future, version-specific adapter is explicitly enabled for named canaries.
"""
from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import os
import ssl
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAMESPACE = os.getenv("POD_NAMESPACE", "egress-fabric")
LABEL = os.getenv("POD_SELECTOR", "app.kubernetes.io/name=mihomo-node")
GROUP = os.getenv("MIHOMO_GROUP", "stable-egress")
PROBE_URL = os.getenv("PROBE_URL", "https://www.gstatic.com/generate_204")
INTERVAL = int(os.getenv("PROBE_INTERVAL_SECONDS", "15"))
TIMEOUT_MS = int(os.getenv("PROBE_TIMEOUT_MS", "5000"))
LOSS_PENALTY = float(os.getenv("LOSS_PENALTY_MS", "1500"))
SATURATION_PENALTY = float(os.getenv("SATURATION_PENALTY_MS", "750"))
RELAY_PENALTY = float(os.getenv("RELAY_PENALTY_MS", "200"))
REGION_PENALTY = float(os.getenv("REGION_PENALTY_MS", "500"))
ALPHA = float(os.getenv("EWMA_ALPHA", "0.35"))
MARGIN = float(os.getenv("SWITCH_MARGIN", "0.15"))
HOLD = int(os.getenv("MIN_HOLD_SECONDS", "60"))
PORT = int(os.getenv("HTTP_PORT", "8080"))
GEODB_PATH = os.getenv("GEODB_PATH", "/etc/re8ch-geodb/geodb.json.gz")
GEODB_SHA256 = os.getenv("GEODB_SHA256", "")
CANARY_CLIENTS = {x for x in os.getenv("CANARY_CLIENT_IDS", "").split(",") if x}
API = "https://kubernetes.default.svc"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


def request(url, method="GET", payload=None, headers=None, context=None, timeout=8):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, context=context, timeout=timeout) as res:
        raw = res.read()
        return json.loads(raw) if raw else {}


class Ewma:
    def __init__(self):
        self.values = {}

    def observe(self, key, value):
        old = self.values.get(key, value)
        self.values[key] = ALPHA * value + (1 - ALPHA) * old
        return self.values[key]


class GeoDB:
    """Small runtime reader for a centrally built, immutable JSON snapshot."""

    def __init__(self):
        self.version = "builtin-empty"
        self.sha256 = ""
        self.cn_networks = []
        self.cn_suffixes = set()
        self.reload()

    def reload(self):
        if not os.path.exists(GEODB_PATH):
            return False
        with open(GEODB_PATH, "rb") as source:
            raw = source.read()
        digest = hashlib.sha256(raw).hexdigest()
        if GEODB_SHA256 and digest != GEODB_SHA256.lower():
            raise ValueError("GeoDB SHA-256 mismatch")
        decoded = gzip.decompress(raw) if GEODB_PATH.endswith(".gz") else raw
        data = json.loads(decoded)
        self.cn_networks = [ipaddress.ip_network(x) for x in data.get("cn_cidrs", [])]
        self.cn_suffixes = {x.lower().lstrip(".") for x in data.get("cn_domains", [])}
        self.version, self.sha256 = data.get("version", digest[:12]), digest
        return True

    def classify(self, destination):
        host = destination.rsplit(":", 1)[0].strip("[]").lower().rstrip(".")
        try:
            addr = ipaddress.ip_address(host)
            return "CN" if any(addr in net for net in self.cn_networks) else "GLOBAL"
        except ValueError:
            labels = host.split(".")
            suffixes = (".".join(labels[index:]) for index in range(len(labels)))
            return "CN" if any(suffix in self.cn_suffixes for suffix in suffixes) else "GLOBAL"


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.geo = GeoDB()
        self.latency, self.loss = Ewma(), Ewma()
        self.peers, self.paths = {}, {}
        self.decisions = deque(maxlen=2000)
        self.switches = deque(maxlen=500)
        self.current = {}
        self.changed_at = {}
        self.failures = Counter()

    def peer_score(self, peer, client_region):
        latency = float(peer.get("rtt_ewma_ms", TIMEOUT_MS))
        loss = float(peer.get("loss_ewma", 1.0))
        util = max(0.0, min(1.0, float(peer.get("utilization", 1.0))))
        relay = RELAY_PENALTY if peer.get("mode") == "RELAY" else 0
        region = 0 if peer.get("region") == client_region else REGION_PENALTY
        cost = float(peer.get("optional_cost", 0))
        unhealthy = 0 if peer.get("health", False) else TIMEOUT_MS
        return latency + LOSS_PENALTY * loss + SATURATION_PENALTY * util + relay + region + cost + unhealthy

    def choose_peer(self, client_id, client_region, now):
        scores = {name: self.peer_score(p, client_region) for name, p in self.peers.items() if p.get("health")}
        current = self.current.get(client_id)
        best = min(scores, key=scores.get) if scores else None
        if current in scores and best != current:
            age = now - self.changed_at.get(client_id, 0)
            if age < HOLD or scores[best] >= scores[current] * (1 - MARGIN):
                best = current
        if best != current:
            self.current[client_id] = best
            self.changed_at[client_id] = now
            self.switches.append({"timestamp": int(now), "client_id": client_id, "from": current, "to": best,
                                  "reason": "initial" if current is None else "score-threshold"})
        return best, scores.get(best)

    def decide(self, payload):
        now = time.time()
        client_id = payload["client_id"]
        client_region = payload["client_region"]
        if client_region not in ("CN", "GLOBAL"):
            raise ValueError("client_region must be CN or GLOBAL")
        destination = payload["destination"]
        destination_class = payload.get("destination_class") or self.geo.classify(destination)
        decision = "DIRECT" if client_region == destination_class else (
            "OVERSEAS_FABRIC" if client_region == "CN" else "CN_FABRIC")
        # A CN_FABRIC decision must select a CN peer even when the originating
        # terminal is overseas. OVERSEAS_FABRIC first enters through the CN
        # client's local routing peer, then the L3 fabric selects its egress.
        peer_region = "CN" if decision == "CN_FABRIC" else client_region
        peer, score = (None, 0.0) if decision == "DIRECT" else self.choose_peer(client_id, peer_region, now)
        peer_data = self.peers.get(peer, {})
        trace_id = payload.get("trace_id") or str(uuid.uuid4())
        item = {
            "timestamp": int(now), "client_id": client_id, "client_region": client_region,
            "destination": destination, "destination_class": destination_class, "decision": decision,
            "netbird_peer": peer, "netbird_mode": peer_data.get("mode"),
            "fabric_path": payload.get("fabric_path"), "mihomo_path": payload.get("mihomo_path"),
            "egress_region": "CN" if decision == "CN_FABRIC" else payload.get("egress_region"),
            "score": round(score or 0, 2), "reason": "same-region" if decision == "DIRECT" else "geo-policy",
            "trace_id": trace_id, "shadow": client_id not in CANARY_CLIENTS,
            "geodb_version": self.geo.version,
        }
        self.decisions.append(item)
        print(json.dumps({"event": "route-decision", **item}, sort_keys=True), flush=True)
        return item

    def observe_peer(self, item):
        required = {"peer_id", "region", "health", "mode"}
        missing = sorted(required - item.keys())
        if missing:
            raise ValueError("missing fields: " + ",".join(missing))
        name = item["peer_id"]
        if "rtt_ms" in item:
            item["rtt_ewma_ms"] = round(self.latency.observe(name, float(item["rtt_ms"])), 2)
        if "loss" in item:
            item["loss_ewma"] = round(self.loss.observe(name, float(item["loss"])), 5)
        self.peers[name] = {**self.peers.get(name, {}), **item, "observed_at": int(time.time())}

    def metrics(self):
        lines = ["# HELP re8ch_route_decisions_total Route decisions by class.",
                 "# TYPE re8ch_route_decisions_total counter"]
        counts = Counter((d["client_region"], d["decision"]) for d in self.decisions)
        for (region, decision), value in counts.items():
            lines.append(f're8ch_route_decisions_total{{client_region="{region}",decision="{decision}"}} {value}')
        modes = Counter(p.get("mode", "UNKNOWN") for p in self.peers.values())
        for mode, value in modes.items():
            lines.append(f're8ch_netbird_peers{{mode="{mode}"}} {value}')
        selected = Counter(x for x in self.current.values() if x)
        for peer, value in selected.items():
            lines.append(f're8ch_netbird_selected_peer_count{{peer="{peer}"}} {value}')
        for peer, p in self.peers.items():
            labels = f'peer="{peer}",region="{p.get("region", "unknown")}",mode="{p.get("mode", "UNKNOWN")}"'
            lines.append(f're8ch_netbird_peer_rtt_ms{{{labels}}} {p.get("rtt_ewma_ms", 0)}')
            lines.append(f're8ch_netbird_peer_loss_ratio{{{labels}}} {p.get("loss_ewma", 0)}')
            lines.append(f're8ch_netbird_peer_utilization_ratio{{{labels}}} {p.get("utilization", 0)}')
        for dataplane, path in self.paths.items():
            for candidate, score in path.get("scores", {}).items():
                healthy = 1 if float(score) < TIMEOUT_MS else 0
                lines.append(f're8ch_path_health{{dataplane="{dataplane}",path="{candidate}"}} {healthy}')
                lines.append(f're8ch_path_score{{dataplane="{dataplane}",path="{candidate}"}} {score}')
        lines.append(f"re8ch_route_switch_events_total {len(self.switches)}")
        for reason, value in self.failures.items():
            lines.append(f're8ch_controller_failures_total{{reason="{reason}"}} {value}')
        lines.append(f're8ch_geodb_info{{version="{self.geo.version}",sha256="{self.geo.sha256}"}} 1')
        return "\n".join(lines) + "\n"


STATE = State()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        raw = json.dumps(value, sort_keys=True).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        with STATE.lock:
            if self.path == "/healthz": return self.send_json(200, {"ok": True})
            if self.path == "/v1/route-decisions": return self.send_json(200, list(STATE.decisions))
            if self.path == "/v1/path-decisions": return self.send_json(200, list(STATE.switches))
            if self.path == "/v1/peers": return self.send_json(200, STATE.peers)
            if self.path == "/v1/geodb": return self.send_json(200, {"version": STATE.geo.version, "sha256": STATE.geo.sha256})
            if self.path == "/metrics":
                raw = STATE.metrics().encode(); self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); return self.wfile.write(raw)
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        try:
            value = self.body()
            with STATE.lock:
                if self.path == "/v1/route-decisions": return self.send_json(200, STATE.decide(value))
                if self.path == "/v1/observations/netbird-peer":
                    STATE.observe_peer(value); return self.send_json(202, {"accepted": True})
                if self.path == "/v1/geodb/reload":
                    return self.send_json(200, {"reloaded": STATE.geo.reload(), "version": STATE.geo.version, "sha256": STATE.geo.sha256})
            self.send_json(404, {"error": "not found"})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def log_message(self, fmt, *args):
        return


def k8s_pods():
    token = open(TOKEN_PATH).read().strip()
    query = urllib.parse.urlencode({"labelSelector": LABEL})
    data = request(f"{API}/api/v1/namespaces/{NAMESPACE}/pods?{query}",
                   headers={"Authorization": f"Bearer {token}"},
                   context=ssl.create_default_context(cafile=CA_PATH))
    return [x["status"]["podIP"] for x in data.get("items", [])
            if x.get("status", {}).get("phase") == "Running" and x["status"].get("podIP")]


def mihomo_loop():
    scorer, current, changed_at = Ewma(), None, 0
    loss = Ewma()
    while True:
        try:
            targets = k8s_pods()
            if not targets:
                time.sleep(INTERVAL); continue
            base = f"http://{targets[0]}:9090"
            group = request(f"{base}/proxies/{urllib.parse.quote(GROUP)}")
            names = [x for x in group.get("all", []) if x not in ("DIRECT", "REJECT", GROUP)]
            scores = {}
            for name in names:
                observed = []
                for ip in targets:
                    q = urllib.parse.urlencode({"url": PROBE_URL, "timeout": TIMEOUT_MS})
                    try:
                        latency = float(request(f"http://{ip}:9090/proxies/{urllib.parse.quote(name)}/delay?{q}")["delay"]); ok = True
                    except Exception:
                        latency, ok = float(TIMEOUT_MS), False
                    observed.append(latency); loss.observe(name, 0.0 if ok else 1.0)
                scores[name] = scorer.observe(name, sum(observed) / len(observed)) + LOSS_PENALTY * loss.values[name]
            best = min(scores, key=scores.get) if scores else current
            now = time.time()
            if current and best != current and (now - changed_at < HOLD or scores[best] >= scores.get(current, float("inf")) * (1 - MARGIN)):
                best = current
            if best != current:
                with STATE.lock:
                    STATE.switches.append({"timestamp": int(now), "dataplane": "mihomo", "from": current, "to": best, "reason": "score-threshold"})
                current, changed_at = best, now
            if current:
                for ip in targets:
                    request(f"http://{ip}:9090/proxies/{urllib.parse.quote(GROUP)}", "PUT", {"name": current}, {"Content-Type": "application/json"})
            with STATE.lock:
                STATE.paths["mihomo"] = {"selected": current, "scores": scores, "observed_at": int(now)}
            print(json.dumps({"event": "path-decision", "dataplane": "mihomo", "selected": current, "scores": scores}, sort_keys=True), flush=True)
        except Exception as exc:
            with STATE.lock: STATE.failures[type(exc).__name__] += 1
            print(json.dumps({"event": "controller-error", "error": str(exc)}), flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    threading.Thread(target=mihomo_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
