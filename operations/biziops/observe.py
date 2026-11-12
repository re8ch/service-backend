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
from typing import Any, Callable, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[2]
SITE_CATALOG = ROOT / "inventory/public-sites.yaml"
SOURCE_CATALOG = ROOT / "operations/biziops/sources.yaml"
USER_AGENT = "RE8CH-BiziOps-Observer/0.1 (+https://github.com/re8ch/service-backend)"


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


FetchJson = Callable[[str, float], dict[str, Any]]


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    """GET a JSON document. Errors never echo the URL, which may carry an API key."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(1048576).decode("utf-8"))


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def collect_youtube(accounts: list[dict[str, str]], env: Mapping[str, str], get: FetchJson, timeout: float) -> list[dict[str, Any]]:
    key = env.get("YOUTUBE_API_KEY")
    results = []
    for account in accounts:
        handle = account["id"]
        selector = {"forHandle": handle} if handle.startswith("@") else {"id": handle}
        query = urllib.parse.urlencode({"part": "statistics", **selector, "key": key})
        try:
            items = get(f"https://www.googleapis.com/youtube/v3/channels?{query}", timeout).get("items") or []
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            results.append({"error": type(getattr(error, "reason", error)).__name__})
            continue
        if not items:
            results.append({"error": "channel-not-found"})
            continue
        stats = items[0].get("statistics", {})
        results.append({
            "followers": None if stats.get("hiddenSubscriberCount") else as_int(stats.get("subscriberCount")),
            "views": as_int(stats.get("viewCount")),
            "videos": as_int(stats.get("videoCount")),
        })
    return results


def collect_bilibili(accounts: list[dict[str, str]], env: Mapping[str, str], get: FetchJson, timeout: float) -> list[dict[str, Any]]:
    results = []
    for account in accounts:
        query = urllib.parse.urlencode({"vmid": account["id"]})
        try:
            body = get(f"https://api.bilibili.com/x/relation/stat?{query}", timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            results.append({"error": type(getattr(error, "reason", error)).__name__})
            continue
        if body.get("code") != 0:
            results.append({"error": f"bilibili-code-{body.get('code')}"})
            continue
        results.append({"followers": as_int((body.get("data") or {}).get("follower"))})
    return results


COLLECTORS = {"youtube": collect_youtube, "bilibili": collect_bilibili}


def video_state(source: dict[str, Any], env: Mapping[str, str]) -> str:
    if not source.get("enabled"):
        return "declared-unconfigured"
    if not source.get("accounts"):
        return "no-accounts"
    if any(not env.get(name) for name in source.get("credentialEnvironment", [])):
        return "missing-credential"
    if source.get("collector") not in COLLECTORS:
        return "declared-unconfigured"
    return "active"


def observe_video(catalog: dict[str, Any], env: Mapping[str, str], get: FetchJson, timeout: float) -> list[dict[str, Any]]:
    rows = []
    for source in catalog["spec"].get("videoSources", []):
        state = video_state(source, env)
        accounts = source.get("accounts") or []
        if state == "active":
            facts = COLLECTORS[source["collector"]](accounts, env, get, timeout)
        else:
            facts = [{} for _ in accounts]
        rows.append({
            "id": source["id"],
            "displayName": source.get("displayName", source["id"]),
            "method": source["method"],
            "state": state,
            "accounts": [{**account, "facts": fact} for account, fact in zip(accounts, facts)],
            "note": source.get("note"),
        })
    return rows


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
    video = payload.get("video", [])
    if video:
        lines.extend([
            "",
            "## Video platforms",
            "",
            "| Platform | Account | State | Followers | Views | Videos |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ])
        for source in video:
            accounts = source["accounts"] or [{"label": "—", "facts": {}}]
            for account in accounts:
                fact = account["facts"]
                cell = lambda name: "unknown" if fact.get(name) is None else f"{fact[name]:,}"
                lines.append(
                    f"| {source['displayName']} | {account.get('label') or account.get('id')} "
                    f"| {fact.get('error') or source['state']} | {cell('followers')} | {cell('views')} | {cell('videos')} |"
                )
    lines.extend(["", "## Analysis source readiness", ""])
    for source in payload["sources"]:
        lines.append(f"- `{source['id']}`: **{source['state']}** via `{source['method']}`")
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
    payload = {
        "schemaVersion": "biziops.re8ch.com/observation-v1alpha1",
        "observedAt": utc_now().isoformat(),
        "catalogRevision": hashlib.sha256(SITE_CATALOG.read_bytes()).hexdigest(),
        "sources": source_readiness(sources),
        "observations": [observe(item, args.timeout) for item in governed_deployments(sites)],
        "video": observe_video(sources, os.environ, fetch_json, args.timeout),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.summary.write_text(render_summary(payload), encoding="utf-8")


if __name__ == "__main__":
    main()

