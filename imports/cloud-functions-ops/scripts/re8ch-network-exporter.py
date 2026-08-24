#!/usr/bin/env python3
"""Read-only Prometheus exporter for the RE8CH FRR/WireGuard public mesh."""

import argparse
import concurrent.futures
import http.server
import ipaddress
import json
import re
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlparse


def run(command, timeout=5):
    try:
        return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def esc(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def metric(name, value, labels=None):
    labels = labels or {}
    label_text = ""
    if labels:
        label_text = "{" + ",".join(f'{key}="{esc(val)}"' for key, val in sorted(labels.items())) + "}"
    return f"{name}{label_text} {value}"


def parse_ping(output):
    loss = 1.0
    rtt = None
    loss_match = re.search(r"([0-9.]+)% packet loss", output or "")
    if loss_match:
        loss = float(loss_match.group(1)) / 100.0
    rtt_match = re.search(r"=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)\s*ms", output or "")
    if rtt_match:
        rtt = float(rtt_match.group(2))
    return loss, rtt


class Collector:
    def __init__(self, inventory, node_id, zt_interface, throughput_targets=None,
                 throughput_interval=900, throughput_max_bytes=524288):
        self.inventory = inventory
        self.node = next(node for node in inventory["nodes"] if str(node["id"]) == str(node_id))
        self.peers = [node for node in inventory["nodes"] if node["id"] != self.node["id"]]
        self.zt_interface = zt_interface
        self.throughput_targets = throughput_targets or []
        self.throughput_interval = max(60, throughput_interval)
        self.throughput_max_bytes = max(65536, throughput_max_bytes)
        self.throughput_lines = []
        self.output = ""
        self.lock = threading.Lock()

    def peer_from_allowed_ips(self, allowed):
        for item in allowed.split(","):
            try:
                address = str(ipaddress.ip_interface(item.strip()).ip)
            except ValueError:
                continue
            for peer in self.peers:
                if address in (peer["ztIp"], peer["transportIp"]):
                    return peer
        return None

    def wireguard(self, lines):
        result = run(["wg", "show", self.inventory["interface"], "dump"])
        if not result or result.returncode:
            lines.append(metric("re8ch_network_exporter_collection_success", 0, {"collector": "wireguard"}))
            return
        rows = result.stdout.splitlines()[1:]
        now = time.time()
        for row in rows:
            fields = row.split("\t")
            if len(fields) < 8:
                continue
            peer = self.peer_from_allowed_ips(fields[3])
            if not peer:
                continue
            labels = {"node": self.node["name"], "peer": peer["name"], "peer_id": peer["id"], "path": "public_wg"}
            handshake = int(fields[4] or 0)
            lines.append(metric("re8ch_wireguard_peer_receive_bytes_total", int(fields[5]), labels))
            lines.append(metric("re8ch_wireguard_peer_transmit_bytes_total", int(fields[6]), labels))
            lines.append(metric("re8ch_wireguard_peer_handshake_age_seconds", max(0, now - handshake) if handshake else -1, labels))
            lines.append(metric("re8ch_wireguard_peer_info", 1, {**labels, "endpoint": fields[2]}))
        lines.append(metric("re8ch_network_exporter_collection_success", 1, {"collector": "wireguard"}))

    def frr(self, lines):
        bgp = run(["vtysh", "-c", "show ip bgp summary"])
        bfd = run(["vtysh", "-c", "show bfd peers brief"])
        bgp_up = set()
        if bgp and bgp.returncode == 0:
            for row in bgp.stdout.splitlines():
                fields = row.split()
                if not fields or not fields[0].startswith("172.31.92.") or len(fields) < 10:
                    continue
                peer = next((item for item in self.peers if item["transportIp"] == fields[0]), None)
                if not peer:
                    continue
                received = fields[-2]
                up = received.isdigit()
                if up:
                    bgp_up.add(peer["id"])
                labels = {"node": self.node["name"], "peer": peer["name"], "peer_id": peer["id"]}
                lines.append(metric("re8ch_frr_bgp_session_up", 1 if up else 0, labels))
                lines.append(metric("re8ch_frr_bgp_prefixes_received", int(received) if up else 0, labels))
        bfd_up = set()
        if bfd and bfd.returncode == 0:
            for row in bfd.stdout.splitlines():
                fields = row.split()
                if len(fields) < 4 or not fields[2].startswith("172.31.92."):
                    continue
                peer = next((item for item in self.peers if item["transportIp"] == fields[2]), None)
                if peer and fields[-1].lower() == "up":
                    bfd_up.add(peer["id"])
        for peer in self.peers:
            labels = {"node": self.node["name"], "peer": peer["name"], "peer_id": peer["id"]}
            lines.append(metric("re8ch_frr_bfd_session_up", 1 if peer["id"] in bfd_up else 0, labels))
        lines.append(metric("re8ch_network_exporter_collection_success", 1 if bgp and bgp.returncode == 0 else 0, {"collector": "frr"}))

    def zerotier(self, lines):
        result = run(["zerotier-cli", "peers", "-j"])
        if not result or result.returncode:
            lines.append(metric("re8ch_network_exporter_collection_success", 0, {"collector": "zerotier"}))
            return
        try:
            rows = json.loads(result.stdout)
        except json.JSONDecodeError:
            lines.append(metric("re8ch_network_exporter_collection_success", 0, {"collector": "zerotier"}))
            return
        candidates = {}
        for row in rows:
            paths = row.get("paths") or []
            addresses = [str(path.get("address", "")).rsplit("/", 1)[0] for path in paths]
            peer = next((item for item in self.peers if item["publicIp"] in addresses or item["transportIp"] in addresses), None)
            if not peer:
                continue
            preferred = next((path for path in paths if path.get("preferred")), paths[0] if paths else {})
            endpoint_ip = str(preferred.get("address", "unknown")).rsplit("/", 1)[0]
            underlying = "public_internet"
            if endpoint_ip == peer["transportIp"]:
                underlying = "public_wg"
            elif endpoint_ip.startswith("10.42."):
                underlying = "cilium"
            elif endpoint_ip not in (peer["publicIp"], "unknown"):
                underlying = "other"
            priority = (row.get("role") == "LEAF", row.get("latency", -1) >= 0, bool(preferred))
            if peer["id"] not in candidates or priority > candidates[peer["id"]][0]:
                candidates[peer["id"]] = (priority, row, endpoint_ip, underlying)
        for peer in self.peers:
            candidate = candidates.get(peer["id"])
            labels = {"node": self.node["name"], "peer": peer["name"], "peer_id": peer["id"], "path": "zerotier"}
            if not candidate:
                lines.append(metric("re8ch_path_probe_success", 0, labels))
                continue
            _, row, endpoint_ip, underlying = candidate
            latency = int(row.get("latency", -1))
            lines.append(metric("re8ch_path_probe_success", 1 if latency >= 0 else 0, labels))
            lines.append(metric("re8ch_path_probe_packet_loss_ratio", 0 if latency >= 0 else 1, labels))
            if latency >= 0:
                lines.append(metric("re8ch_path_probe_rtt_milliseconds", latency, labels))
            lines.append(metric("re8ch_zerotier_peer_path_info", 1, {**labels, "zerotier_id": row.get("address", "unknown"), "endpoint": endpoint_ip, "underlying_path": underlying, "role": row.get("role", "unknown")}))
        lines.append(metric("re8ch_network_exporter_collection_success", 1, {"collector": "zerotier"}))

    def route_and_probe(self, lines):
        probe_jobs = []
        for peer in self.peers:
            base = {"node": self.node["name"], "peer": peer["name"], "peer_id": peer["id"]}
            route = run(["ip", "route", "get", peer["ztIp"]])
            text = route.stdout.splitlines()[0] if route and route.returncode == 0 and route.stdout else ""
            dev_match = re.search(r"\bdev\s+(\S+)", text)
            via_match = re.search(r"\bvia\s+(\S+)", text)
            dev = dev_match.group(1) if dev_match else "unknown"
            path = "public_wg" if dev == "wg-public-mesh" else "zerotier" if dev == self.zt_interface else "rescue_wg" if dev == "wg-r640" else "other"
            lines.append(metric("re8ch_route_selected_info", 1, {**base, "path": path, "device": dev, "via": via_match.group(1) if via_match else "direct"}))
            probes = [("public_wg", ["ping", "-n", "-q", "-c", "2", "-W", "1", peer["transportIp"]])]
            for path_name, command in probes:
                probe_jobs.append((base, path_name, command))
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            futures = [(base, path_name, pool.submit(run, command, 4)) for base, path_name, command in probe_jobs]
            for base, path_name, future in futures:
                probe = future.result()
                loss, rtt = parse_ping((probe.stdout if probe else "") + (probe.stderr if probe else ""))
                labels = {**base, "path": path_name}
                lines.append(metric("re8ch_path_probe_success", 1 if loss < 1 else 0, labels))
                lines.append(metric("re8ch_path_probe_packet_loss_ratio", loss, labels))
                if rtt is not None:
                    lines.append(metric("re8ch_path_probe_rtt_milliseconds", rtt, labels))

    def throughput_probe(self):
        lines = []
        targets = list(self.throughput_targets)
        targets.extend({"name": f'peer-{peer["name"]}',
                        "url": f'http://{peer["ztIp"]}:9788/probe/bytes?bytes={self.throughput_max_bytes}',
                        "path": self.path_for_ip(peer["ztIp"]), "peer": peer["name"], "peer_id": peer["id"]}
                       for peer in self.peers if peer.get("throughputProbeEnabled", True))
        for target in targets:
            name, url, path = target["name"], target["url"], target.get("path", "direct")
            labels = {"node": self.node["name"], "target": name, "path": path}
            if target.get("peer"):
                labels.update(peer=target["peer"], peer_id=target["peer_id"])
            started = time.monotonic()
            received = 0
            ok = 0
            try:
                request = urllib.request.Request(url, headers={"Range": f"bytes=0-{self.throughput_max_bytes - 1}",
                                                               "User-Agent": "re8ch-network-exporter/0.2"})
                with urllib.request.urlopen(request, timeout=12) as response:
                    while received < self.throughput_max_bytes:
                        chunk = response.read(min(65536, self.throughput_max_bytes - received))
                        if not chunk:
                            break
                        received += len(chunk)
                ok = 1 if received >= 65536 else 0
            except Exception:
                pass
            elapsed = max(0.001, time.monotonic() - started)
            lines.append(metric("re8ch_throughput_probe_success", ok, labels))
            lines.append(metric("re8ch_throughput_probe_bytes", received, labels))
            lines.append(metric("re8ch_throughput_probe_duration_seconds", elapsed, labels))
            lines.append(metric("re8ch_throughput_probe_mbps", received * 8 / elapsed / 1_000_000, labels))
            lines.append(metric("re8ch_throughput_probe_last_run_unixtime", int(time.time()), labels))
        with self.lock:
            self.throughput_lines = lines

    def path_for_ip(self, address):
        route = run(["ip", "route", "get", address])
        text = route.stdout.splitlines()[0] if route and route.returncode == 0 and route.stdout else ""
        match = re.search(r"\bdev\s+(\S+)", text)
        dev = match.group(1) if match else "unknown"
        return "public_wg" if dev == "wg-public-mesh" else "zerotier" if dev == self.zt_interface else "rescue_wg" if dev == "wg-r640" else "other"

    def background_loop(self):
        next_throughput = 0
        while True:
            now = time.time()
            if self.throughput_targets and now >= next_throughput:
                self.throughput_probe()
                next_throughput = now + self.throughput_interval
            output = self.collect()
            with self.lock:
                self.output = output
            time.sleep(30)

    def collect(self):
        lines = [
            "# HELP re8ch_network_exporter_build_info Static exporter identity.",
            "# TYPE re8ch_network_exporter_build_info gauge",
            metric("re8ch_network_exporter_build_info", 1, {"version": "0.2.0", "node": self.node["name"], "node_id": self.node["id"]}),
        ]
        self.wireguard(lines)
        self.frr(lines)
        self.zerotier(lines)
        self.route_and_probe(lines)
        with self.lock:
            lines.extend(self.throughput_lines)
        lines.append(metric("re8ch_network_exporter_last_collection_unixtime", int(time.time()), {"node": self.node["name"]}))
        return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--listen", default="192.0.2.92:9788")
    parser.add_argument("--zerotier-interface", default="zto5uhdriy")
    parser.add_argument("--throughput-targets", default="")
    parser.add_argument("--throughput-interval-seconds", type=int, default=900)
    parser.add_argument("--throughput-max-bytes", type=int, default=524288)
    args = parser.parse_args()
    with open(args.inventory, encoding="utf-8") as handle:
        inventory = json.load(handle)
    targets = []
    if args.throughput_targets:
        with open(args.throughput_targets, encoding="utf-8") as handle:
            targets = json.load(handle).get("targets", [])
    collector = Collector(inventory, args.node_id, args.zerotier_interface, targets,
                          args.throughput_interval_seconds, args.throughput_max_bytes)
    threading.Thread(target=collector.background_loop, daemon=True).start()
    host, port = args.listen.rsplit(":", 1)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/probe/bytes":
                query = dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item)
                requested = min(collector.throughput_max_bytes, int(query.get("bytes", collector.throughput_max_bytes)))
                body = b"0" * max(0, requested)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path not in ("/", "/metrics"):
                self.send_error(404)
                return
            with collector.lock:
                body = collector.output.encode()
            if not body:
                body = "re8ch_network_exporter_ready 0\n".encode()
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
