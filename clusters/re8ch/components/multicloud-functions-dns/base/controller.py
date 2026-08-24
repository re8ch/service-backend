#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"
TOKEN = os.environ["CF_API_TOKEN"]
ZONE_NAME = os.environ.get("CF_ZONE_NAME", "re8ch.com").strip()
TENANT = os.environ["TENANT"].strip().lower()
APPLY = os.environ.get("APPLY", "0").lower() in {"1", "true", "yes", "on"}
PROXIED = os.environ.get("CF_PROXIED", "false").lower() in {"1", "true", "yes", "on"}
TTL = int(os.environ.get("CF_TTL", "60"))
INTERVAL = int(os.environ.get("RUN_INTERVAL_SECONDS", "300"))
COMMENT = f"managed by {TENANT}/multicloud-functions-dns-controller"
ALLOWED_SUFFIX = f".{TENANT}.functions.re8ch.com"


def log(message):
    print(message, flush=True)


def request(method, path, payload=None, params=None):
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(5):
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code < 500 or attempt == 4:
                raise RuntimeError(f"Cloudflare {method} {path}: HTTP {exc.code}: {detail}") from exc
        except (OSError, urllib.error.URLError):
            if attempt == 4:
                raise
        time.sleep(min(2**attempt, 8))
    if not result.get("success"):
        raise RuntimeError(f"Cloudflare {method} {path}: {result.get('errors')}")
    return result.get("result")


def desired_records():
    records = []
    for raw in os.environ.get("DNS_RECORDS", "").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = [part.strip() for part in raw.split(",")]
        name, content = (part.strip().rstrip(".") for part in parts[0].split("=", 1))
        if not name.endswith(ALLOWED_SUFFIX):
            raise RuntimeError(f"refusing out-of-scope DNS name: {name}")
        proxied = PROXIED
        for option in parts[1:]:
            key, value = (part.strip() for part in option.split("=", 1))
            if key.lower() != "proxied":
                raise RuntimeError(f"unsupported DNS option: {key}")
            proxied = value.lower() in {"1", "true", "yes", "on"}
        records.append({"type": "CNAME", "name": name, "content": content, "ttl": TTL, "proxied": proxied})
    if not records:
        raise RuntimeError("DNS_RECORDS is empty")
    return records


def zone_id():
    zones = request("GET", "/zones", params={"name": ZONE_NAME, "status": "active", "per_page": 1})
    if not zones:
        raise RuntimeError(f"active Cloudflare zone not found: {ZONE_NAME}")
    return zones[0]["id"]


def reconcile(zone, desired):
    existing = request(
        "GET",
        f"/zones/{zone}/dns_records",
        params={"name": desired["name"], "per_page": 100},
    )
    payload = {**desired, "comment": COMMENT}
    if not existing:
        log(f"create CNAME {desired['name']} -> {desired['content']} proxied={desired['proxied']}")
        if APPLY:
            request("POST", f"/zones/{zone}/dns_records", payload)
        return
    primary = existing[0]
    same = (
        primary.get("type") == "CNAME"
        and primary.get("content") == desired["content"]
        and bool(primary.get("proxied")) == desired["proxied"]
        and int(primary.get("ttl") or 1) == desired["ttl"]
    )
    if same:
        log(f"ok CNAME {desired['name']} -> {desired['content']}")
    else:
        log(f"update {desired['name']} -> {desired['content']} proxied={desired['proxied']}")
        if APPLY:
            request("PUT", f"/zones/{zone}/dns_records/{primary['id']}", payload)


def main():
    records = desired_records()
    zone = zone_id()
    log(f"tenant={TENANT} records={len(records)} apply={APPLY}")
    while True:
        try:
            for record in records:
                reconcile(zone, record)
        except Exception as exc:
            log(f"error: {exc}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"fatal: {exc}")
        sys.exit(1)
