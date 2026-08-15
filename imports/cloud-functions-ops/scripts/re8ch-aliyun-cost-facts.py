#!/usr/bin/env python3
"""Read Alibaba Cloud bill facts and emit a review-only network cost candidate.

The adapter never edits network-costs.yaml. With --input it parses a captured
DescribeInstanceBill response; with --fetch it runs the read-only aliyun CLI.
"""

import argparse
import datetime as dt
import json
import subprocess


NETWORK_CODES = {"cdt", "cdt_DataTransfer_public_cn", "cdt_internet_public_cn", "cdt_cdtir_public_cn"}


def rows_from_payload(payload):
    data = payload.get("Data", payload.get("data", {}))
    items = data.get("Items", data.get("items", []))
    if isinstance(items, dict):
        items = items.get("Item", items.get("item", []))
    return items or []


def number(row, *names):
    for name in names:
        if row.get(name) not in (None, ""):
            return float(row[name])
    return None


def normalize(payload, billing_cycle):
    facts = []
    for row in rows_from_payload(payload):
        code = str(row.get("ProductCode") or row.get("PipCode") or row.get("ProductType") or "")
        item_code = str(row.get("BillingItemCode") or row.get("CommodityCode") or "")
        text = " ".join(str(row.get(key, "")) for key in ("ProductName", "ProductDetail", "BillingItem", "ItemName"))
        if code not in NETWORK_CODES and "流量" not in text.lower() and "traffic" not in text.lower():
            continue
        usage = number(row, "Usage", "UsageAmount", "UsageQuantity")
        amount = number(row, "PretaxAmount", "PaymentAmount")
        unit = str(row.get("UsageUnit") or row.get("UsageUnitName") or "unknown")
        price = amount / usage if amount is not None and usage and unit.lower() in {"gb", "gib", "gb-month"} else None
        facts.append({
            "provider": "aliyun",
            "billingCycle": billing_cycle,
            "productCode": code,
            "billingItemCode": item_code,
            "instanceId": str(row.get("InstanceID") or row.get("InstanceId") or ""),
            "region": str(row.get("Region") or row.get("RegionCode") or ""),
            "usage": usage,
            "usageUnit": unit,
            "pretaxAmount": amount,
            "currency": str(row.get("Currency") or "CNY"),
            "realizedPricePerGiB": price,
        })
    return facts


def fetch(billing_cycle):
    command = [
        "aliyun", "bssopenapi", "DescribeInstanceBill",
        "--BillingCycle", billing_cycle,
        "--IsBillingItem", "true",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout), command


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="captured Alibaba BSS JSON response")
    source.add_argument("--fetch", action="store_true", help="call the read-only aliyun CLI")
    parser.add_argument("--billing-cycle", required=True, help="YYYY-MM")
    args = parser.parse_args()
    dt.datetime.strptime(args.billing_cycle, "%Y-%m")
    if args.fetch:
        payload, command = fetch(args.billing_cycle)
        source_info = {"kind": "aliyun-bssopenapi", "operation": "DescribeInstanceBill", "command": command}
    else:
        with open(args.input, encoding="utf-8") as handle:
            payload = json.load(handle)
        source_info = {"kind": "captured-json", "path": args.input}
    output = {
        "apiVersion": "networking.re8ch.com/v1alpha1",
        "kind": "NetworkCostFactCandidate",
        "metadata": {"generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "reviewRequired": True},
        "source": source_info,
        "facts": normalize(payload, args.billing_cycle),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
