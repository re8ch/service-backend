#!/usr/bin/env python3
"""Join desired public websites with the read-only provider catalog for Headlamp."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from textwrap import indent

import yaml

from validate import load, validate


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "inventory/public-sites.yaml"
OBSERVED = ROOT / "platform/headlamp/public-edge-catalog.yaml"
OUTPUT = ROOT / "platform/headlamp/public-site-governance.yaml"
PROVIDER_NAMES = {
    "cloudflare-pages": "Cloudflare Pages",
    "cloudflare-workers": "Cloudflare Workers",
    "tencent-edgeone": "Tencent EdgeOne",
    "aliyun-esa": "Aliyun ESA",
}


def observed_sites(path: Path) -> list[dict]:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    return json.loads(manifest["data"]["sites.json"])["sites"]


def reconcile(catalog: dict, observed: list[dict]) -> dict:
    by_host: dict[str, list[dict]] = {}
    for item in observed:
        for hostname in item.get("hostnames", []):
            by_host.setdefault(hostname.lower().rstrip("."), []).append(item)

    declared_hosts: set[str] = set()
    sites = []
    for site in catalog["spec"]["sites"]:
        deployments = []
        for deployment in site["deployments"]:
            expected_provider = PROVIDER_NAMES.get(deployment["delivery"]["provider"])
            matches = []
            for hostname in deployment["hostnames"]:
                normalized = hostname.lower().rstrip(".")
                declared_hosts.add(normalized)
                matches.extend(by_host.get(normalized, []))
            provider_matches = [item for item in matches if item.get("provider") == expected_provider]
            if not matches:
                status = "declared-but-missing"
            elif expected_provider and not provider_matches:
                status = "drifted"
            elif any(str(item.get("delivery", "")).lower() in {"offline", "disabled"} for item in provider_matches):
                status = "drifted"
            else:
                status = "healthy"
            deployments.append({
                "id": deployment["id"],
                "region": deployment["region"],
                "primaryUrl": deployment["primaryUrl"],
                "hostnames": deployment["hostnames"],
                "origin": deployment["origin"],
                "delivery": deployment["delivery"],
                "status": status,
                "observed": provider_matches or matches,
            })
        sites.append({
            "id": site["id"],
            "displayName": site["displayName"],
            "owner": site["owner"],
            "repository": site["repository"],
            "qualityProfile": site["qualityProfile"],
            "healthProfile": site["healthProfile"],
            "agentDiscovery": site["agentDiscovery"],
            "deployments": deployments,
        })

    unmanaged = [
        item for item in observed
        if not any(host.lower().rstrip(".") in declared_hosts for host in item.get("hostnames", []))
    ]
    return {"sites": sites, "unmanagedObservedRecords": unmanaged}


def render(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "public-site-governance",
            "namespace": "kube-system",
            "annotations": {
                "delivery.re8ch.com/description": "Desired public websites reconciled with read-only provider observations."
            },
        },
    }
    return (
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=120)
        + "data:\n  governance.json: |\n"
        + indent(body, "    ")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--observed", type=Path, default=OBSERVED)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = load(args.catalog)
    validate(catalog)
    expected = render(reconcile(catalog, observed_sites(args.observed)))
    if args.check:
        assert args.output.is_file() and args.output.read_text(encoding="utf-8") == expected, "public site governance snapshot is stale"
    else:
        args.output.write_text(expected, encoding="utf-8")
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
