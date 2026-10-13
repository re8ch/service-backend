#!/usr/bin/env python3
"""Validate public website desired state beyond JSON Schema constraints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "inventory/public-sites.yaml"
SCHEMA = ROOT / "schemas/public-site-catalog.schema.json"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate(catalog: dict) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(catalog, schema, format_checker=jsonschema.FormatChecker())
    spec = catalog["spec"]
    quality_profiles = spec["qualityProfiles"]
    health_profiles = spec["healthProfiles"]
    site_ids: set[str] = set()
    host_owners: dict[str, str] = {}
    origins: dict[tuple[str, str, str], str] = {}

    for site in spec["sites"]:
        site_id = site["id"]
        assert site_id not in site_ids, f"duplicate site id: {site_id}"
        site_ids.add(site_id)
        assert site["qualityProfile"] in quality_profiles, f"unknown quality profile: {site_id}"
        assert site["healthProfile"] in health_profiles, f"unknown health profile: {site_id}"
        deployment_ids: set[str] = set()
        for deployment in site["deployments"]:
            deployment_id = deployment["id"]
            identity = f"{site_id}/{deployment_id}"
            assert deployment_id not in deployment_ids, f"duplicate deployment id: {identity}"
            deployment_ids.add(deployment_id)
            primary_host = urlparse(deployment["primaryUrl"]).hostname
            assert primary_host in deployment["hostnames"], f"primary URL host missing from hostnames: {identity}"
            for host in deployment["hostnames"]:
                assert host not in host_owners, f"hostname {host} is already owned by {host_owners.get(host)}"
                host_owners[host] = identity
            origin = deployment["origin"]
            origin_key = (origin["provider"], origin["resource"], origin.get("region", ""))
            assert origin_key not in origins, f"isolated origin reused by {identity} and {origins.get(origin_key)}"
            origins[origin_key] = identity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    catalog = load(args.catalog)
    validate(catalog)
    deployments = sum(len(site["deployments"]) for site in catalog["spec"]["sites"])
    print(f"Validated {len(catalog['spec']['sites'])} public site(s) and {deployments} deployment(s)")


if __name__ == "__main__":
    main()
