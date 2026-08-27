#!/usr/bin/env python3
"""Fetch, validate, version and atomically promote public routing rules."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(os.getenv("RULES_ROOT", "/rules"))
META = "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo"
BM = "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Shadowrocket"
UPSTREAM_REFS = {
    "MetaCubeX/meta-rules-dat": "meta",
    "blackmatrix7/ios_rule_script": "master",
}
RECIPE_REVISION = 2

MRS_SOURCES = {
    **{name: f"{META}/geosite/{name}.mrs" for name in (
        "private", "cn", "geolocation-!cn", "openai", "anthropic", "google", "youtube", "telegram", "netflix",
        "disney", "hbo", "spotify", "tiktok", "twitter", "github", "gitlab", "apple", "microsoft", "steam", "category-games")},
    "cn-ip": f"{META}/geoip/cn.mrs",
    "private-ip": f"{META}/geoip/private.mrs",
}

LIST_SOURCES = {
    "private": [f"{BM}/Lan/Lan.list"],
    "cn": [f"{BM}/ChinaMax/ChinaMax.list"],
    "cn-ip": [f"{BM}/ChinaIPs/ChinaIPs.list"],
    "adblock": [f"{BM}/AdvertisingLite/AdvertisingLite.list"],
    "direct-services": [f"{BM}/{name}/{name}.list" for name in
                        ("Alibaba", "Baidu", "BiliBili", "ChinaMedia", "Tencent", "Cloudflarecn", "SteamCN")],
    "ai": [f"{BM}/{name}/{name}.list" for name in ("OpenAI", "Anthropic", "Gemini", "Copilot")],
    "google": [f"{BM}/Google/Google.list", f"{BM}/YouTube/YouTube.list"],
    "telegram": [f"{BM}/Telegram/Telegram.list"],
    "streaming": [f"{BM}/{name}/{name}.list" for name in ("Netflix", "Disney", "HBO", "Spotify", "YouTubeMusic", "BiliBiliIntl")],
    "social": [f"{BM}/{name}/{name}.list" for name in ("TikTok", "Twitter", "Discord", "Reddit", "Whatsapp", "Instagram", "Facebook")],
    "developer": [f"{BM}/{name}/{name}.list" for name in ("GitHub", "GitLab", "Docker", "Npmjs", "Python", "Jetbrains")],
    "cloud": [f"{BM}/{name}/{name}.list" for name in ("Dropbox", "GoogleDrive", "OneDrive", "iCloud", "Notion", "Slack")],
    "speedtest": [f"{BM}/Speedtest/Speedtest.list"],
    "apple": [f"{BM}/Apple/Apple.list"],
    "microsoft": [f"{BM}/Microsoft/Microsoft.list"],
    "games": [f"{BM}/Steam/Steam.list", f"{BM}/Game/Game.list", f"{BM}/Epic/Epic.list"],
}


def github_api_raw_url(url: str) -> str | None:
    prefix = "https://raw.githubusercontent.com/"
    if not url.startswith(prefix):
        return None
    parts = url.removeprefix(prefix).split("/", 3)
    if len(parts) != 4:
        return None
    owner, repository, ref, path = parts
    return (f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/contents/"
            f"{quote(path, safe='/')}?ref={quote(ref, safe='')}")


def fetch(url: str) -> bytes:
    last_error = None
    candidates = [(url, {})]
    api_url = github_api_raw_url(url)
    if api_url:
        candidates.append((api_url, {"Accept": "application/vnd.github.raw+json"}))
    for candidate, extra_headers in candidates:
        for attempt in range(3):
            headers = {"User-Agent": "re8ch-rule-sync/1", **extra_headers}
            request = urllib.request.Request(candidate, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
    raise RuntimeError(f"failed to fetch rules after retries: {url}") from last_error


def upstream_commits() -> dict:
    commits = {}
    for repository, ref in UPSTREAM_REFS.items():
        raw = fetch(f"https://api.github.com/repos/{repository}/commits/{ref}")
        sha = str(json.loads(raw).get("sha", ""))
        if len(sha) != 40:
            raise ValueError(f"invalid upstream commit: {repository}")
        commits[repository] = {"ref": ref, "commit": sha}
    return commits


def clean_list(parts: list[bytes]) -> bytes:
    seen, lines = set(), []
    for part in parts:
        for raw in part.decode("utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "//")) or line in seen:
                continue
            seen.add(line)
            lines.append(line)
    if not lines:
        raise ValueError("empty rule list")
    return ("\n".join(lines) + "\n").encode()


def mosdns_domains(raw: bytes) -> bytes:
    domains = []
    for line in raw.decode().splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 2:
            continue
        if fields[0] == "DOMAIN":
            domains.append(f"full:{fields[1]}")
        elif fields[0] == "DOMAIN-SUFFIX":
            domains.append(f"domain:{fields[1]}")
    if not domains:
        raise ValueError("no MosDNS-compatible domains")
    return ("\n".join(dict.fromkeys(domains)) + "\n").encode()


def metric(name: str, raw: bytes) -> int:
    return len(raw.splitlines()) if name.endswith(".list") else len(raw)


def previous_manifest() -> dict:
    path = ROOT / "current" / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else {}


def validate_change(name: str, value: int, previous: dict) -> None:
    if int(previous.get("recipeRevision", 1)) != RECIPE_REVISION:
        return
    old = int(previous.get("artifacts", {}).get(name, {}).get("metric", 0) or 0)
    if old and abs(value - old) / old > 0.25:
        raise ValueError(f"abnormal rule size change: {name}: {old} -> {value}")


def sync(now: int | None = None) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now))
    versions, stage = ROOT / "versions", ROOT / f".stage-{stamp}-{os.getpid()}"
    versions.mkdir(parents=True, exist_ok=True)
    stage.mkdir(parents=True)
    previous = previous_manifest()
    artifacts = {}
    try:
        for name, url in MRS_SOURCES.items():
            raw = fetch(url)
            if len(raw) < 128:
                raise ValueError(f"invalid MRS: {name}")
            filename = f"{name}.mrs"
            value = metric(filename, raw)
            validate_change(filename, value, previous)
            (stage / filename).write_bytes(raw)
            artifacts[filename] = {"sha256": hashlib.sha256(raw).hexdigest(), "metric": value, "sources": [url]}
        for name, urls in LIST_SOURCES.items():
            raw = clean_list([fetch(url) for url in urls])
            filename = f"{name}.list"
            value = metric(filename, raw)
            validate_change(filename, value, previous)
            (stage / filename).write_bytes(raw)
            artifacts[filename] = {"sha256": hashlib.sha256(raw).hexdigest(), "metric": value, "sources": urls}
            if name in {"private", "cn"}:
                dns_raw = mosdns_domains(raw)
                dns_name = f"{name}-domains.txt"
                (stage / dns_name).write_bytes(dns_raw)
                artifacts[dns_name] = {"sha256": hashlib.sha256(dns_raw).hexdigest(), "metric": len(dns_raw.splitlines()), "sources": urls}
        manifest = {"version": stamp, "createdAt": int(time.time()), "recipeRevision": RECIPE_REVISION,
                    "upstreams": upstream_commits(), "artifacts": artifacts}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        destination = versions / stamp
        stage.rename(destination)
        link = ROOT / ".current-new"
        link.unlink(missing_ok=True)
        link.symlink_to(destination.relative_to(ROOT), target_is_directory=True)
        link.replace(ROOT / "current")
        for old in sorted((p for p in versions.iterdir() if p.is_dir()), reverse=True)[7:]:
            shutil.rmtree(old)
        return stamp
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


if __name__ == "__main__":
    print(sync())
