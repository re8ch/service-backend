#!/usr/bin/env python3
"""Cost-aware, observe-only routing advisor with Prometheus output and JSON audit logs."""

import argparse
import http.server
import json
import math
import threading
import time
import urllib.parse
import urllib.request


METRICS = (
    "re8ch_path_probe_rtt_milliseconds",
    "re8ch_path_probe_packet_loss_ratio",
    "re8ch_path_probe_success",
    "re8ch_route_selected_info",
    "re8ch_frr_bgp_session_up",
    "re8ch_frr_bfd_session_up",
    "re8ch_wireguard_peer_handshake_age_seconds",
    "re8ch_wireguard_peer_transmit_bytes_total",
)


def load_json_yaml(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def esc(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def prom(name, value, labels=None):
    labels = labels or {}
    suffix = ""
    if labels:
        suffix = "{" + ",".join(f'{key}="{esc(val)}"' for key, val in sorted(labels.items())) + "}"
    return f"{name}{suffix} {value}"


def query_series(base_url, metric):
    url = base_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": metric})
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.load(response)
    return payload.get("data", {}).get("result", [])


def cost_for_path(catalog, path):
    for entry in catalog.get("paths", []):
        if entry.get("match", {}).get("pathType") == path and entry.get("status") != "inactive":
            return entry.get("pricePerGiB"), entry.get("budgetMonthly"), entry.get("id")
    return None, None, "unconfigured"


def evaluate_path(policy, catalog, path, facts, budget_spent=0.0):
    gates = policy["hardGates"]
    reasons = []
    if facts.get("success", 0) < 1:
        reasons.append("probe-failed")
    if facts.get("loss", 1) > gates["maximumLossRatio"]:
        reasons.append("loss-gate")
    if path == "public_wg":
        if gates.get("requireBgpForPublicWireGuard") and facts.get("bgp", 0) < 1:
            reasons.append("bgp-down")
        if gates.get("requireBfdForPublicWireGuard") and facts.get("bfd", 0) < 1:
            reasons.append("bfd-down")
        if facts.get("handshake_age", math.inf) > gates["maximumHandshakeAgeSeconds"]:
            reasons.append("stale-handshake")
    price, budget, cost_id = cost_for_path(catalog, path)
    budget_cfg = policy.get("budget", {})
    if budget_cfg.get("enforced"):
        if price is None and gates.get("rejectUnknownPriceWhenBudgetEnforced"):
            reasons.append("unknown-price")
        if budget and budget_spent / budget >= budget_cfg.get("hardLimitRatio", 1.0):
            reasons.append("budget-exhausted")
    weights = policy["scoreWeights"]
    score = policy["pathPreference"].get(path, 0) * weights["preference"]
    score += facts.get("rtt", 10000) * weights["rttMilliseconds"]
    score += facts.get("jitter", 0) * weights["jitterMilliseconds"]
    score += facts.get("loss", 1) * weights["lossRatio"]
    if price is not None:
        score += price * weights["pricePerGiB"]
    return {"eligible": not reasons, "reasons": reasons, "score": score, "price": price, "cost_id": cost_id}


def advance_recommendation_state(policy, state, candidate, now):
    """Apply consecutive-win and hold-down hysteresis to advisory state."""
    state = dict(state or {"recommended": "none", "candidate": None, "wins": 0, "last_change": 0})
    if candidate == state["recommended"]:
        state.update(candidate=None, wins=0)
        return state, False, "already-recommended"
    if now - state["last_change"] < policy["holdDownSeconds"]:
        return state, False, "hold-down"
    if candidate != state.get("candidate"):
        state.update(candidate=candidate, wins=1)
    else:
        state["wins"] += 1
    if state["wins"] < policy["consecutiveWinsRequired"]:
        return state, False, "consecutive-wins"
    state.update(recommended=candidate, candidate=None, wins=0, last_change=now)
    return state, True, "accepted"


def route_map_recommendation(policy, node, peer, current, recommended, results, now):
    """Return a structured, non-executable FRR route-map recommendation."""
    return {
        "apiVersion": "networking.re8ch.com/v1alpha1",
        "kind": "FRRRouteMapRecommendation",
        "metadata": {"generatedAtUnix": int(now), "policyVersion": policy["metadata"]["version"]},
        "spec": {
            "node": node,
            "peer": peer,
            "currentPath": current,
            "recommendedPath": recommended,
            "recommendedLocalPreference": policy["pathPreference"].get(recommended),
            "routeMapName": f"RE8CH-ADVISORY-{peer.upper().replace('.', '-')}",
            "apply": False,
            "evaluations": results,
        },
    }


class Advisor:
    def __init__(self, metrics_url, policy, catalog):
        if policy.get("applyEnabled") or policy.get("mode") != "observe-only":
            raise ValueError("this advisor is intentionally observe-only; applyEnabled must be false")
        self.metrics_url = metrics_url
        self.policy = policy
        self.catalog = catalog
        self.lock = threading.Lock()
        self.output = ""
        self.recommendations = []
        self.states = {}
        self.suppression_signatures = {}
        self.last_success = 0

    def snapshot(self):
        series = {}
        for name in METRICS:
            series[name] = query_series(self.metrics_url, name)
        pairs = {}
        for name, rows in series.items():
            for row in rows:
                labels = row.get("metric", {})
                node, peer = labels.get("node"), labels.get("peer")
                if not node or not peer:
                    continue
                pair = pairs.setdefault((node, peer), {"paths": {}, "current": None})
                path = labels.get("path", "public_wg")
                facts = pair["paths"].setdefault(path, {})
                value = float(row.get("value", [0, 0])[1])
                if name.endswith("rtt_milliseconds"):
                    facts["rtt"] = value
                elif name.endswith("packet_loss_ratio"):
                    facts["loss"] = value
                elif name.endswith("probe_success"):
                    facts["success"] = value
                elif name.endswith("bgp_session_up"):
                    facts["bgp"] = value
                elif name.endswith("bfd_session_up"):
                    facts["bfd"] = value
                elif name.endswith("handshake_age_seconds"):
                    facts["handshake_age"] = value
                elif name.endswith("route_selected_info"):
                    pair["current"] = labels.get("path")
                elif name.endswith("transmit_bytes_total"):
                    facts["transmit_bytes"] = value
        return pairs

    def evaluate(self):
        pairs = self.snapshot()
        lines = [prom("re8ch_routing_advisor_build_info", 1, {"version": "0.1.0", "mode": "observe-only", "policy_version": self.policy["metadata"]["version"]})]
        now = time.time()
        recommendations = []
        for (node, peer), pair in sorted(pairs.items()):
            results = {path: evaluate_path(self.policy, self.catalog, path, facts) for path, facts in pair["paths"].items()}
            eligible = [(path, result) for path, result in results.items() if result["eligible"]]
            candidate = max(eligible, key=lambda item: item[1]["score"])[0] if eligible else "none"
            current = pair.get("current") or "unknown"
            key = (node, peer)
            old = self.states.get(key)
            state, changed, transition_reason = advance_recommendation_state(self.policy, old, candidate, now)
            recommended = state["recommended"]
            if recommended not in ("none", current):
                recommendations.append(route_map_recommendation(self.policy, node, peer, current, recommended, results, now))
            if changed or not old:
                event = {
                    "event": "routing-recommendation-change",
                    "ledgerAction": "observe",
                    "node": node,
                    "peer": peer,
                    "currentPath": current,
                    "previousRecommendation": old["recommended"] if old else None,
                    "recommendedPath": recommended,
                    "candidatePath": candidate,
                    "transitionReason": transition_reason,
                    "policyVersion": self.policy["metadata"]["version"],
                    "applyEnabled": False,
                    "facts": pair["paths"],
                    "evaluations": results,
                    "timestamp": int(now),
                }
                print(json.dumps(event, sort_keys=True), flush=True)
            elif self.policy.get("audit", {}).get("logSuppressedSwitches") and transition_reason in ("hold-down", "consecutive-wins"):
                signature = (candidate, transition_reason, state.get("wins"))
                if self.suppression_signatures.get(key) != signature:
                    print(json.dumps({
                        "event": "routing-recommendation-suppressed",
                        "ledgerAction": "observe",
                        "node": node,
                        "peer": peer,
                        "currentPath": current,
                        "recommendedPath": recommended,
                        "candidatePath": candidate,
                        "transitionReason": transition_reason,
                        "consecutiveWins": state.get("wins", 0),
                        "policyVersion": self.policy["metadata"]["version"],
                        "applyEnabled": False,
                        "timestamp": int(now),
                    }, sort_keys=True), flush=True)
                    self.suppression_signatures[key] = signature
            self.states[key] = state
            labels = {"node": node, "peer": peer, "current_path": current, "recommended_path": recommended, "candidate_path": candidate}
            lines.append(prom("re8ch_routing_recommendation_info", 1, labels))
            lines.append(prom("re8ch_routing_recommendation_change_needed", 1 if recommended not in ("none", current) else 0, labels))
            for path, result in results.items():
                path_labels = {"node": node, "peer": peer, "path": path, "cost_id": result["cost_id"], "price_known": str(result["price"] is not None).lower()}
                lines.append(prom("re8ch_routing_path_eligible", 1 if result["eligible"] else 0, path_labels))
                lines.append(prom("re8ch_routing_path_score", result["score"], path_labels))
                lines.append(prom("re8ch_network_cost_fact_status", 1 if result["price"] is not None else 0, path_labels))
                if result["price"] is not None:
                    lines.append(prom("re8ch_network_price_per_gib", result["price"], path_labels))
                    transmitted = pair["paths"].get(path, {}).get("transmit_bytes", 0)
                    lines.append(prom("re8ch_network_estimated_egress_cost", transmitted / (1024 ** 3) * result["price"], path_labels))
        lines.append(prom("re8ch_routing_advisor_last_success_unixtime", int(now)))
        with self.lock:
            self.output = "\n".join(lines) + "\n"
            self.recommendations = recommendations
            self.last_success = now

    def loop(self):
        print(json.dumps({"event": "advisor-start", "ledgerAction": "observe", "mode": "observe-only", "policyVersion": self.policy["metadata"]["version"], "timestamp": int(time.time())}, sort_keys=True), flush=True)
        while True:
            try:
                self.evaluate()
            except Exception as exc:
                print(json.dumps({"event": "advisor-error", "ledgerAction": "observe", "error": str(exc), "timestamp": int(time.time())}, sort_keys=True), flush=True)
            time.sleep(self.policy["evaluationIntervalSeconds"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-url", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--costs", required=True)
    parser.add_argument("--listen", default="0.0.0.0:9790")
    args = parser.parse_args()
    advisor = Advisor(args.metrics_url, load_json_yaml(args.policy), load_json_yaml(args.costs))
    threading.Thread(target=advisor.loop, daemon=True).start()
    host, port = args.listen.rsplit(":", 1)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/recommendations":
                with advisor.lock:
                    body = json.dumps({"items": advisor.recommendations}, sort_keys=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path not in ("/", "/metrics"):
                self.send_error(404)
                return
            with advisor.lock:
                body = advisor.output.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    http.server.ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
