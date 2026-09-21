#!/usr/bin/env python3
"""Continuously reconcile book cost with independent public market prices."""

import argparse
import datetime as dt
import http.server
import json
import math
import os
import statistics
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


HOURS_PER_MONTH = 730.0


def fetch_json(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "re8ch-fair-value/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_ecb_usd_cny(url):
    request = urllib.request.Request(url, headers={"User-Agent": "re8ch-fair-value/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())
    rates = {element.attrib["currency"]: float(element.attrib["rate"])
             for element in root.iter() if "currency" in element.attrib}
    return rates["CNY"] / rates["USD"]


def quantity_for(target, offer):
    ratios = []
    for field in ("cpuCores", "memoryGiB", "storageGiB"):
        requested = target.get(field)
        if requested is None:
            continue
        supplied = offer.get(field)
        if supplied in (None, 0):
            return None
        ratios.append(float(requested) / float(supplied))
    return max(1, math.ceil(max(ratios))) if ratios else None


def cheapest_equivalent(target, offers):
    candidates = []
    for offer in offers:
        quantity = quantity_for(target, offer)
        if quantity is None or offer.get("monthlyCNY") is None:
            continue
        candidates.append((float(offer["monthlyCNY"]) * quantity, quantity, offer))
    return min(candidates, key=lambda item: item[0]) if candidates else None


def linode_offers(payload, usd_cny, region):
    offers = []
    for item in payload.get("data", []):
        monthly = item.get("price", {}).get("monthly")
        for regional in item.get("region_prices", []):
            if regional.get("id") == region:
                monthly = regional.get("monthly", monthly)
        if monthly is None:
            continue
        offers.append({
            "sku": item.get("id"), "region": region,
            "cpuCores": item.get("vcpus"),
            "memoryGiB": float(item.get("memory", 0)) / 1024,
            "storageGiB": item.get("disk"),
            "monthlyCNY": float(monthly) * usd_cny,
        })
    return offers


def vultr_offers(payload, usd_cny, region):
    offers = []
    for item in payload.get("plans", []):
        if region not in item.get("locations", []):
            continue
        monthly = item.get("monthly_cost")
        if monthly is None or float(monthly) <= 0:
            continue
        offers.append({
            "sku": item.get("id"), "region": region,
            "cpuCores": item.get("vcpu_count"),
            "memoryGiB": float(item.get("ram", 0)) / 1024,
            "storageGiB": item.get("disk"),
            "monthlyCNY": float(monthly) * usd_cny,
        })
    return offers


def azure_offers(payload, sku_specs):
    offers = []
    for item in payload.get("Items", []):
        sku = item.get("armSkuName")
        spec = sku_specs.get(sku)
        if not spec or item.get("type") != "Consumption":
            continue
        meter = str(item.get("meterName", ""))
        product = str(item.get("productName", ""))
        if "Spot" in meter or "Windows" in product or item.get("unitOfMeasure") != "1 Hour":
            continue
        offers.append({
            "sku": sku, "region": item.get("armRegionName"),
            "cpuCores": spec["cpuCores"], "memoryGiB": spec["memoryGiB"],
            # Managed OS disk is deliberately normalized as a separate 40 GiB
            # equivalent so Azure compute is not mistaken for storage-inclusive.
            "storageGiB": spec.get("storageGiB", 40),
            "monthlyCNY": float(item["retailPrice"]) * HOURS_PER_MONTH,
        })
    return offers


def aggregate(target, observations, minimum_sources, now, max_age_hours):
    fresh = [item for item in observations
             if (now - dt.datetime.fromisoformat(item["observedAt"]).timestamp()) <= max_age_hours * 3600]
    groups = {item["independenceGroup"] for item in fresh}
    result = {
        "node": target["name"], "bookMonthlyCNY": target.get("bookMonthlyCNY"),
        "status": "unknown", "reason": None, "observations": fresh,
        "fairMonthlyCNY": None, "varianceCNY": None, "varianceRatio": None,
        "sourceCount": len(groups), "effectiveAt": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
    }
    if not all(target.get(field) is not None for field in ("cpuCores", "memoryGiB", "storageGiB")):
        result["reason"] = "resource-quantity-incomplete"
        return result
    if len(groups) < minimum_sources:
        result["reason"] = "insufficient-independent-sources"
        return result
    value = statistics.median(item["monthlyCNY"] for item in fresh)
    if value <= 0:
        result["reason"] = "non-positive-market-value"
        return result
    result.update(status="usable", fairMonthlyCNY=value)
    book = target.get("bookMonthlyCNY")
    if book is not None:
        result["varianceCNY"] = value - float(book)
        result["varianceRatio"] = None if float(book) == 0 else value / float(book) - 1
    return result


def esc(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def prom(name, value, labels):
    suffix = "{" + ",".join(f'{key}="{esc(val)}"' for key, val in sorted(labels.items())) + "}"
    return f"{name}{suffix} {value}"


class Controller:
    def __init__(self, config, history_path, fetcher=fetch_json, clock=time.time):
        self.config = config
        self.history_path = history_path
        self.fetcher = fetcher
        self.clock = clock
        self.lock = threading.Lock()
        self.snapshot = {"status": "starting", "items": []}
        self.metrics = ""

    def refresh(self):
        now = self.clock()
        sources = self.config["sources"]
        usd_cny = fetch_ecb_usd_cny(sources["ecb"]["url"])
        linode = self.fetcher(sources["linode"]["url"])
        vultr = self.fetcher(sources["vultr"]["url"])
        azure_payloads = {}
        for region in sorted(set(item["azure"] for item in self.config["regionMappings"].values())):
            query = "serviceName eq 'Virtual Machines' and armRegionName eq '%s' and priceType eq 'Consumption'" % region
            url = sources["azure"]["url"] + "?" + urllib.parse.urlencode({"currencyCode": "CNY", "$filter": query})
            azure_payloads[region] = self.fetcher(url)
        observed_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()
        items = []
        for target in self.config["targets"]:
            mapping = self.config["regionMappings"][target["regionGroup"]]
            inventory_complete = all(target.get(field) is not None for field in ("cpuCores", "memoryGiB", "storageGiB"))
            source_offers = {
                "linode": linode_offers(linode, usd_cny, mapping["linode"]),
                "vultr": vultr_offers(vultr, usd_cny, mapping["vultr"]),
                "azure": azure_offers(azure_payloads[mapping["azure"]], self.config["azureSkuSpecs"]),
            }
            observations = []
            for source_id, offers in source_offers.items() if inventory_complete else ():
                selected = cheapest_equivalent(target, offers)
                if not selected:
                    continue
                amount, quantity, offer = selected
                observations.append({
                    "source": source_id, "independenceGroup": source_id,
                    "region": offer["region"], "sku": offer["sku"], "quantity": quantity,
                    "monthlyCNY": round(amount, 6), "observedAt": observed_at,
                    "resourceClass": target["resourceClass"],
                    "resourceQuantity": {key: target[key] for key in ("cpuCores", "memoryGiB", "storageGiB")},
                })
            items.append(aggregate(target, observations, self.config["minimumIndependentSources"], now,
                                   self.config["maximumObservationAgeHours"]))
        snapshot = {"status": "ready", "generatedAt": observed_at, "currency": "CNY", "items": items}
        lines = [prom("re8ch_fair_value_refresh_success", 1, {})]
        for item in items:
            labels = {"node": item["node"], "status": item["status"], "reason": item["reason"] or "none"}
            lines.append(prom("re8ch_fair_value_source_count", item["sourceCount"], labels))
            lines.append(prom("re8ch_fair_value_usable", 1 if item["status"] == "usable" else 0, labels))
            if item["bookMonthlyCNY"] is not None:
                lines.append(prom("re8ch_book_value_monthly_cny", item["bookMonthlyCNY"], labels))
            if item["fairMonthlyCNY"] is not None:
                lines.append(prom("re8ch_fair_value_monthly_cny", item["fairMonthlyCNY"], labels))
                if item["varianceCNY"] is not None:
                    lines.append(prom("re8ch_fair_value_variance_cny", item["varianceCNY"], labels))
                if item["varianceRatio"] is not None:
                    lines.append(prom("re8ch_fair_value_variance_ratio", item["varianceRatio"], labels))
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        with open(self.history_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n")
        with self.lock:
            self.snapshot = snapshot
            self.metrics = "\n".join(lines) + "\n"

    def loop(self):
        while True:
            try:
                self.refresh()
            except Exception as exc:
                with self.lock:
                    self.snapshot = {"status": "degraded", "error": str(exc), "items": self.snapshot.get("items", [])}
                    self.metrics = prom("re8ch_fair_value_refresh_success", 0, {}) + "\n"
            time.sleep(self.config["refreshIntervalSeconds"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--history", required=True)
    parser.add_argument("--listen", default="0.0.0.0:9789")
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as handle:
        controller = Controller(json.load(handle), args.history)
    threading.Thread(target=controller.loop, daemon=True).start()
    host, port = args.listen.rsplit(":", 1)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            with controller.lock:
                if self.path == "/metrics":
                    body, content_type = controller.metrics.encode(), "text/plain; version=0.0.4"
                elif self.path == "/api/v1/valuations":
                    body, content_type = json.dumps(controller.snapshot, ensure_ascii=False).encode(), "application/json"
                elif self.path == "/healthz":
                    body, content_type = json.dumps({"status": controller.snapshot["status"]}).encode(), "application/json"
                else:
                    self.send_error(404)
                    return
            self.send_response(200 if self.path != "/healthz" or controller.snapshot["status"] == "ready" else 503)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    http.server.ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
