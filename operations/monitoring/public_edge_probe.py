"""External, one-shot public edge probe for GitHub-hosted runners."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from secretary_observation import publish


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def targets(release: Path) -> list[dict[str, str]]:
    docs = list(yaml.safe_load_all(release.read_text(encoding="utf-8")))
    helm = next(doc for doc in docs if doc and doc.get("kind") == "HelmRelease")
    services = helm["spec"]["values"]["services"]
    result = []
    for hostname, service in services.items():
        host = hostname.rstrip(".")
        result.append({"service": service["service"], "host": host,
                       "url": f"https://{host}{service.get('probePath', '/') or '/'}"})
    if not result or len({target["host"] for target in result}) != len(result):
        raise ValueError("empty or duplicate public edge service catalog")
    return result


def probe(target: dict[str, str], timeout: float) -> dict[str, str | int | bool]:
    outcome: dict[str, str | int | bool] = dict(target)
    try:
        socket.getaddrinfo(target["host"], 443, type=socket.SOCK_STREAM)
        request = urllib.request.Request(target["url"], headers={"User-Agent": "secretary-public-edge-monitor/1"})
        try:
            response = urllib.request.build_opener(NoRedirect).open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            outcome["status"] = response.status
            outcome["ok"] = 200 <= response.status < 400
            if not outcome["ok"]:
                outcome["error"] = f"HTTP {response.status}"
    except Exception as exc:
        outcome["ok"] = False
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, default=Path("platform/public-edge-manager/release.yaml"))
    parser.add_argument("--output", type=Path, default=Path("public-edge-probe-results.json"))
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    catalog = targets(args.release)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda item: probe(item, args.timeout), catalog))
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    failures = [result for result in results if not result["ok"]]
    print(f"Public edge: {len(catalog)} probed, {len(failures)} failed")
    for result in failures:
        print(f"FAIL {result['url']}: {result.get('error')}")
    if failures:
        run_id = os.environ.get("GITHUB_RUN_ID", "local")
        for result in failures:
            try:
                publish(kind="public_edge.probe", subject=f"public/{result['host']}", run_id=run_id,
                        summary=f"{result['host']}: {result.get('error', 'probe failed')}",
                        artifact_ref=f"github-actions:{run_id}:public-edge-probe-results",
                        metadata={"url": str(result["url"])})
            except Exception as exc:
                print(f"Secretary observation failed for {result['host']}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
