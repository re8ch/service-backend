#!/usr/bin/env python3
"""Collect coarse, provider-independent facts about governed public sites."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
SITE_CATALOG = ROOT / "inventory/public-sites.yaml"
SOURCE_CATALOG = ROOT / "operations/biziops/sources.yaml"
USER_AGENT = "RE8CH-BiziOps-Observer/0.1 (+https://github.com/re8ch/service-backend)"
POSTHOG_QUERY = """SELECT
    uniqExactIf($session_id, timestamp >= now() - interval 30 day) AS sessions_30d,
    uniqExactIf(person_id, timestamp >= now() - interval 30 day) AS visitors_30d,
    countIf(event = '$pageview' AND timestamp >= now() - interval 30 day) AS pageviews_30d,
    countIf(timestamp >= now() - interval 30 day) AS events_30d,
    count() AS events_all_time,
    max(timestamp) AS latest_event_at
FROM events"""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def governed_deployments(catalog: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for site in catalog["spec"]["sites"]:
        for deployment in site["deployments"]:
            rows.append({
                "site": site["id"],
                "displayName": site["displayName"],
                "deployment": deployment["id"],
                "region": deployment["region"],
                "url": deployment["primaryUrl"],
                "deliveryProvider": deployment["delivery"]["provider"],
            })
    return rows


def fetch(url: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(131072)
            return {
                "status": response.status,
                "finalUrl": response.geturl(),
                "contentType": response.headers.get("Content-Type"),
                "contentLengthObserved": len(body),
                "sha256Prefix": hashlib.sha256(body).hexdigest()[:16],
                "latencyMs": round((time.monotonic() - started) * 1000),
            }
    except urllib.error.HTTPError as error:
        return {
            "status": error.code,
            "error": "http-error",
            "latencyMs": round((time.monotonic() - started) * 1000),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {
            "status": None,
            "error": type(getattr(error, "reason", error)).__name__,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }


def posthog_request(
    host: str,
    token: str,
    path: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{host.rstrip('/')}{path}",
        data=body,
        method="POST" if body is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("unreachable")


def posthog_projects(host: str, token: str, timeout: float) -> list[dict[str, Any]]:
    response = posthog_request(host, token, "/api/projects/?limit=100", timeout)
    projects = response.get("results", response) if isinstance(response, dict) else response
    return [
        {"id": item["id"], "name": item["name"]}
        for item in projects
        if isinstance(item, dict) and item.get("id") and item.get("name")
    ]


def posthog_observations(host: str, token: str, timeout: float) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    for project in posthog_projects(host, token, timeout):
        try:
            response = posthog_request(
                host,
                token,
                f"/api/projects/{project['id']}/query/",
                timeout,
                {"query": {"kind": "HogQLQuery", "query": POSTHOG_QUERY}},
            )
            row = (response.get("results") or [[None] * 6])[0]
            latest_event = None if row[4] == 0 else row[5]
            observations.append({
                **project,
                "state": "observed",
                "sessions30d": row[0],
                "visitors30d": row[1],
                "pageviews30d": row[2],
                "events30d": row[3],
                "eventsAllTime": row[4],
                "latestEventAt": latest_event,
            })
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            observations.append({**project, "state": "error", "error": type(error).__name__})
    return {"state": "active", "host": host, "projects": observations}


def tls_fact(hostname: str, timeout: float) -> dict[str, Any]:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as wrapped:
                certificate = wrapped.getpeercert()
        expires = dt.datetime.strptime(certificate["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
        return {
            "issuer": dict(item[0] for item in certificate.get("issuer", ())).get("organizationName"),
            "expiresAt": expires.isoformat(),
            "daysRemaining": (expires - utc_now()).days,
        }
    except (OSError, ssl.SSLError, KeyError, ValueError) as error:
        return {"error": type(error).__name__}


def observe(deployment: dict[str, str], timeout: float) -> dict[str, Any]:
    base = deployment["url"].rstrip("/")
    hostname = urllib.parse.urlparse(base).hostname or ""
    paths = {
        "home": "/",
        "robots": "/robots.txt",
        "sitemap": "/sitemap.xml",
        "llms": "/llms.txt",
    }
    facts = {name: fetch(base + path, timeout) for name, path in paths.items()}
    return {**deployment, "facts": facts, "tls": tls_fact(hostname, timeout)}


def source_readiness(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": source["id"],
            "method": source["method"],
            "enabled": source["enabled"],
            "state": "active" if source["enabled"] else "declared-unconfigured",
            "facts": source["facts"],
            "note": source.get("note"),
        }
        for source in catalog["spec"]["sources"]
    ]


def render_summary(payload: dict[str, Any]) -> str:
    observations = payload["observations"]
    healthy = sum(item["facts"]["home"].get("status") == 200 for item in observations)
    lines = [
        "# BiziOps public product observation",
        "",
        f"Observed at: `{payload['observedAt']}`",
        "",
        f"Healthy deployments: **{healthy}/{len(observations)}**",
        "",
        "| Product | Surface | Region | HTTP | TLS days | robots | sitemap | llms.txt |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in observations:
        fact = item["facts"]
        lines.append(
            "| {displayName} | {deployment} | {region} | {home} | {tls} | {robots} | {sitemap} | {llms} |".format(
                displayName=item["displayName"],
                deployment=item["deployment"],
                region=item["region"],
                home=fact["home"].get("status") or "unknown",
                tls=item["tls"].get("daysRemaining", "unknown"),
                robots=fact["robots"].get("status") or "unknown",
                sitemap=fact["sitemap"].get("status") or "unknown",
                llms=fact["llms"].get("status") or "unknown",
            )
        )
    lines.extend(["", "## Analysis source readiness", ""])
    for source in payload["sources"]:
        lines.append(f"- `{source['id']}`: **{source['state']}** via `{source['method']}`")
    posthog = payload.get("providerObservations", {}).get("posthog")
    if posthog:
        lines.extend([
            "",
            "## PostHog organization projects",
            "",
            "| Project | ID | Sessions 30d | Visitors 30d | Pageviews 30d | Events 30d | All-time events | Latest event |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ])
        for project in posthog.get("projects", []):
            if project["state"] == "observed":
                lines.append(
                    "| {name} | {id} | {sessions30d} | {visitors30d} | {pageviews30d} | "
                    "{events30d} | {eventsAllTime} | {latestEventAt} |".format(**project)
                )
            else:
                lines.append(
                    f"| {project['name']} | {project['id']} | error | error | error | error | error | {project['error']} |"
                )
    lines.extend([
        "",
        "This report records observed facts. It does not interpret missing provider data as zero.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=12)
    args = parser.parse_args()

    sites = read_yaml(SITE_CATALOG)
    sources = read_yaml(SOURCE_CATALOG)
    provider_observations: dict[str, Any] = {}
    posthog_token = os.environ.get("POSTHOG_PERSONAL_API_KEY")
    if posthog_token:
        provider_observations["posthog"] = posthog_observations(
            os.environ.get("POSTHOG_HOST", "https://eu.posthog.com"),
            posthog_token,
            float(os.environ.get("POSTHOG_TIMEOUT", "90")),
        )
    payload = {
        "schemaVersion": "biziops.re8ch.com/observation-v1alpha1",
        "observedAt": utc_now().isoformat(),
        "catalogRevision": hashlib.sha256(SITE_CATALOG.read_bytes()).hexdigest(),
        "sources": source_readiness(sources),
        "providerObservations": provider_observations,
        "observations": [observe(item, args.timeout) for item in governed_deployments(sites)],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.summary.write_text(render_summary(payload), encoding="utf-8")


if __name__ == "__main__":
    main()

