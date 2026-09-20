#!/usr/bin/env python3
"""Per-node weighted routing controller for the accelerated service prefix."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


TELEMETRY = {"utilizationPercent": None, "updatedAt": 0}


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def discover_source(config):
    network = ipaddress.ip_network(config["acceleratedPrefix"])
    result = run("ip", "-j", "address", "show", "dev", "lo")
    for interface in json.loads(result.stdout):
        for info in interface.get("addr_info", []):
            if info.get("family") == "inet" and ipaddress.ip_address(info["local"]) in network:
                return info["local"]
    raise RuntimeError(f"no local accelerated identity in {network}")


def local_addresses():
    result = run("ip", "-j", "address", "show")
    return {
        info["local"]
        for interface in json.loads(result.stdout)
        for info in interface.get("addr_info", [])
        if info.get("family") == "inet"
    }


def interface_exists(name):
    return bool(name) and run("ip", "link", "show", "dev", name, check=False).returncode == 0


def gateway_nexthop(gateway):
    """Prefer a node's own transit tunnel; workers fall back to the ZT gateway."""
    interface = gateway.get("interface")
    if interface_exists(interface):
        return ["dev", interface]
    return ["via", gateway["address"]]


def ensure_rule(priority, rule_args, expected, dry_run=False):
    """Install a policy rule only when the live rule is missing or stale."""
    command = ["ip", "rule", "add", "priority", str(priority), *rule_args]
    if dry_run:
        print("DRY-RUN", " ".join(command))
        return
    current = run("ip", "rule", "show", "priority", str(priority), check=False).stdout
    if all(fragment in current for fragment in expected):
        return
    run("ip", "rule", "del", "priority", str(priority), check=False)
    run(*command)


def ensure_policy(config, dry_run=False):
    prefix = config["acceleratedPrefix"]
    for gateway in config["gateways"]:
        route = ["ip", "route", "replace", prefix, *gateway_nexthop(gateway), "table", str(gateway["probeTable"])]
        priority = str(11000 + gateway["probeTable"] % 100)
        if dry_run:
            print("DRY-RUN", " ".join(route))
        else:
            run(*route)
        ensure_rule(
            priority,
            ["fwmark", gateway["mark"], "lookup", str(gateway["probeTable"])],
            [f"fwmark {gateway['mark']}", f"lookup {gateway['probeTable']}"],
            dry_run=dry_run,
        )
    priority = str(config["decisionRulePriority"])
    ensure_rule(
        priority,
        ["to", prefix, "lookup", str(config["decisionTable"])],
        [f"to {prefix}", f"lookup {config['decisionTable']}"],
        dry_run=dry_run,
    )


def probe(target, gateway, source, count=5, timeout=1):
    result = run(
        "ping", "-n", "-q", "-I", source, "-m", str(int(gateway["mark"], 0)),
        "-i", "0.2", "-w", str(max(1, timeout + 1)),
        "-c", str(count), "-W", str(timeout), target["address"], check=False,
    )
    loss_match = re.search(r"([0-9.]+)% packet loss", result.stdout)
    rtt_match = re.search(r"= [0-9.]+/([0-9.]+)/", result.stdout)
    loss = float(loss_match.group(1)) if loss_match else 100.0
    rtt = float(rtt_match.group(1)) if rtt_match else None
    return {"lossPercent": loss, "rttMs": rtt}


def read_interface_bytes(interface):
    if not interface_exists(interface):
        return None
    base = Path("/sys/class/net") / interface / "statistics"
    try:
        return (int((base / "rx_bytes").read_text()), int((base / "tx_bytes").read_text()))
    except (OSError, ValueError):
        return None


def sample_utilization(gateways, previous, now):
    """Return local full-duplex tunnel utilization; remote workers report unknown."""
    utilization = {}
    current = {}
    for gateway in gateways:
        interface = gateway.get("interface")
        counters = read_interface_bytes(interface)
        if counters is None:
            utilization[gateway["name"]] = None
            continue
        current[gateway["name"]] = {"rxBytes": counters[0], "txBytes": counters[1], "time": now}
        old = previous.get(gateway["name"])
        if not old or now <= old["time"] or counters[0] < old["rxBytes"] or counters[1] < old["txBytes"]:
            utilization[gateway["name"]] = None
            continue
        interval = now - old["time"]
        rx_bits_per_second = (counters[0] - old["rxBytes"]) * 8 / interval
        tx_bits_per_second = (counters[1] - old["txBytes"]) * 8 / interval
        bits_per_second = max(rx_bits_per_second, tx_bits_per_second)
        capacity = max(1, gateway.get("capacityMbps", 100)) * 1_000_000
        utilization[gateway["name"]] = min(100.0, bits_per_second * 100 / capacity)
    return utilization, current


class TelemetryHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/utilization":
            self.send_error(404)
            return
        body = json.dumps(TELEMETRY, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def start_telemetry_server(config):
    addresses = local_addresses()
    local_gateway = next((g for g in config["gateways"] if g["address"] in addresses), None)
    if not local_gateway:
        return None
    server = ThreadingHTTPServer((local_gateway["address"], config.get("telemetryPort", 9918)), TelemetryHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return local_gateway["name"]


def fetch_gateway_utilization(config, local):
    values = dict(local)
    for gateway in config["gateways"]:
        if values.get(gateway["name"]) is not None:
            continue
        url = f'http://{gateway["address"]}:{config.get("telemetryPort", 9918)}/v1/utilization'
        try:
            with urllib.request.urlopen(url, timeout=config.get("telemetryTimeoutSeconds", 0.5)) as response:
                payload = json.load(response)
            age = time.time() - payload.get("updatedAt", 0)
            if age <= config.get("telemetryMaxAgeSeconds", 10):
                values[gateway["name"]] = payload.get("utilizationPercent")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return values


def score(target, gateway, measurement):
    if measurement["lossPercent"] >= 100 or measurement["rttMs"] is None:
        return 0
    preference = target.get("staticPreference", {}).get(gateway["name"], 100)
    capacity = max(1, gateway.get("capacityMbps", 100))
    loss_factor = max(0.0, 1.0 - measurement["lossPercent"] / 100.0) ** 2
    latency_factor = 100.0 / max(1.0, measurement["rttMs"])
    utilization = measurement.get("utilizationPercent")
    available_factor = 1.0 if utilization is None else max(0.1, 1.0 - utilization / 100.0)
    return preference * gateway.get("staticWeight", 100) * math.sqrt(capacity) * loss_factor * latency_factor * available_factor


def weights(scores):
    positive = {name: value for name, value in scores.items() if value > 0}
    if not positive:
        return {}
    floor = min(positive.values())
    return {name: max(1, min(256, round(value / floor))) for name, value in positive.items()}


def materially_changed(old, new, threshold):
    if set(old) != set(new):
        return True
    return any(abs(new[k] - old[k]) * 100 / max(1, old[k]) >= threshold for k in new)


def bgp_gateways(target):
    result = run("ip", "-j", "route", "show", f'{target["address"]}/32', "proto", "bgp", check=False)
    try:
        routes = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return set()
    found = set()
    for route in routes:
        if route.get("gateway"):
            found.add(route["gateway"])
        for nexthop in route.get("nexthops", []):
            if nexthop.get("gateway"):
                found.add(nexthop["gateway"])
    return found


def prune_with_bgp(config, state, now, dry_run=False):
    # Nodes terminating a transit WireGuard fabric validate it directly with
    # marked probes.  They do not necessarily install worker-facing BGP routes,
    # so an empty BGP view there must not withdraw an otherwise healthy tunnel.
    if any(interface_exists(gateway.get("interface")) for gateway in config["gateways"]):
        return state
    gateway_by_address = {g["address"]: g["name"] for g in config["gateways"]}
    for target in config["targets"]:
        current = state.get(target["name"], {})
        selected = current.get("weights", {})
        if not selected:
            continue
        available = {gateway_by_address[a] for a in bgp_gateways(target) if a in gateway_by_address}
        retained = {name: weight for name, weight in selected.items() if name in available}
        if retained != selected:
            apply_route(config, target, retained, dry_run=dry_run)
            current["weights"] = retained
            current["lastChange"] = now
            current["reason"] = "selected nexthop withdrawn by BGP/BFD"
            state[target["name"]] = current
    return state


def apply_route(config, target, selected, dry_run=False):
    gateway_by_name = {item["name"]: item for item in config["gateways"]}
    if not selected:
        command = ["ip", "route", "del", f'{target["address"]}/32', "table", str(config["decisionTable"])]
        if dry_run:
            print("DRY-RUN", " ".join(command))
        else:
            run(*command, check=False)
        return
    command = ["ip", "route", "replace", f'{target["address"]}/32', "table", str(config["decisionTable"]), "proto", "static"]
    for name, weight in sorted(selected.items()):
        command += ["nexthop", *gateway_nexthop(gateway_by_name[name]), "weight", str(weight)]
    if dry_run:
        print("DRY-RUN", " ".join(command))
    else:
        run(*command)


def reconcile_selected_routes(config, state, dry_run=False):
    """Restore selected routes after boot-time network managers flush tables."""
    for target in config["targets"]:
        selected = state.get(target["name"], {}).get("weights", {})
        if selected:
            apply_route(config, target, selected, dry_run=dry_run)


def iteration(config, source, state, now, dry_run=False, utilization=None):
    results = {}
    with ThreadPoolExecutor(max_workers=len(config["targets"]) * len(config["gateways"])) as executor:
        futures = {
            (target["name"], gateway["name"]): executor.submit(
                probe, target, gateway, source,
                config.get("probeCount", 3), config.get("probeTimeoutSeconds", 1),
            )
            for target in config["targets"]
            for gateway in config["gateways"]
        }
        all_measurements = {key: future.result() for key, future in futures.items()}
    for target in config["targets"]:
        measurements = {
            gateway["name"]: all_measurements[(target["name"], gateway["name"])]
            for gateway in config["gateways"]
        }
        for gateway_name, value in (utilization or {}).items():
            measurements[gateway_name]["utilizationPercent"] = value
        candidate = weights({g["name"]: score(target, g, measurements[g["name"]]) for g in config["gateways"]})
        previous = state.get(target["name"], {})
        selected = previous.get("weights", {})
        last_change = previous.get("lastChange", 0)
        changed = materially_changed(selected, candidate, config["weightChangeThresholdPercent"])
        hard_failure = bool(selected) and any(name not in candidate for name in selected)
        hold_elapsed = now - last_change >= config["minimumHoldSeconds"]
        if not selected or hard_failure or (changed and hold_elapsed):
            selected = candidate
            last_change = now
            apply_route(config, target, selected, dry_run=dry_run)
        results[target["name"]] = {
            "measurements": measurements,
            "candidateWeights": candidate,
            "weights": selected,
            "lastChange": last_change,
        }
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/re8ch/private-transit-policy-agent.json")
    parser.add_argument("--source")
    parser.add_argument("--state", default="/run/re8ch-private-transit-policy-agent.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = load(args.config)
    source = args.source or discover_source(config)
    ensure_policy(config, dry_run=args.dry_run)
    local_gateway_name = start_telemetry_server(config) if not args.dry_run else None
    state_path = Path(args.state)
    state = load(state_path) if state_path.exists() else {}
    counter_state = {}
    while True:
        # Network initialization can finish after this service starts and can
        # flush policy rules or routes. Reconcile the kernel state every cycle
        # so marked probes and the last selected paths remain self-healing.
        ensure_policy(config, dry_run=args.dry_run)
        reconcile_selected_routes(config, state, dry_run=args.dry_run)
        utilization, counter_state = sample_utilization(config["gateways"], counter_state, time.monotonic())
        if local_gateway_name and utilization.get(local_gateway_name) is not None:
            TELEMETRY.update(utilizationPercent=utilization[local_gateway_name], updatedAt=time.time())
        utilization = fetch_gateway_utilization(config, utilization)
        state = prune_with_bgp(config, state, int(time.time()), dry_run=args.dry_run)
        state = iteration(config, source, state, int(time.time()), dry_run=args.dry_run, utilization=utilization)
        print(json.dumps(state, sort_keys=True), flush=True)
        if not args.dry_run:
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.once:
            break
        time.sleep(config["probeIntervalSeconds"])


if __name__ == "__main__":
    main()
