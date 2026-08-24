#!/usr/bin/env python3
"""External watcher agent for K3S + VictoriaMetrics.

It polls the read-only query surface, deduplicates state changes, and sends
Feishu webhook cards. It can run outside the cluster with kubectl access.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

import ops_ai_query


DEFAULT_STATE_FILE = os.environ.get("OPS_WATCH_STATE", "/tmp/qianwen-ops-watch-state.json")
DEFAULT_WEBHOOK = os.environ.get("FEISHU_WEBHOOK_URL", "")
DEFAULT_SECRET = os.environ.get("FEISHU_WEBHOOK_SECRET", "")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_state(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"active": {}, "lastSent": {}}
    except Exception:
        return {"active": {}, "lastSent": {}}


def save_state(path: str, state: dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def issue_key(issue: dict[str, Any]) -> str:
    raw = "|".join(str(issue.get(k, "")) for k in ("source", "severity", "name", "reason", "summary"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def feishu_payload(title: str, lines: list[str], severity: str) -> dict[str, Any]:
    color = {
        "critical": "red",
        "warning": "orange",
        "info": "blue",
        "resolved": "green",
    }.get(severity, "blue")
    content = "\n".join(lines[:40])
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": content or "No details."},
                }
            ],
        },
    }


def sign_feishu_payload(payload: dict[str, Any], secret: str) -> dict[str, Any]:
    if not secret:
        return payload
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    sign = hmac.new(string_to_sign, b"", digestmod="sha256").digest()
    import base64

    payload = dict(payload)
    payload["timestamp"] = timestamp
    payload["sign"] = base64.b64encode(sign).decode("utf-8")
    return payload


def post_json(url: str, payload: dict[str, Any], timeout: int = 15) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=raw, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def send_feishu(webhook: str, secret: str, title: str, lines: list[str], severity: str, dry_run: bool) -> None:
    payload = sign_feishu_payload(feishu_payload(title, lines, severity), secret)
    if dry_run or not webhook:
        print(json.dumps({"title": title, "severity": severity, "lines": lines, "dryRun": True}, ensure_ascii=False))
        return
    post_json(webhook, payload)


def float_value(result: dict[str, Any]) -> list[tuple[dict[str, str], float]]:
    values = []
    for item in result.get("result", []):
        value = item.get("value")
        if not value or len(value) < 2:
            continue
        try:
            values.append((item.get("metric", {}), float(value[1])))
        except (TypeError, ValueError):
            continue
    return values


def collect_issues(vm_url: str, namespace: str | None, event_limit: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    nodes = ops_ai_query.k8s_nodes()["nodes"]
    for node in nodes:
        if not node.get("ready"):
            issues.append({
                "source": "k3s",
                "severity": "critical",
                "name": node.get("name"),
                "reason": "node_not_ready",
                "summary": f"Node {node.get('name')} is NotReady",
                "details": node.get("conditions", {}),
            })

    pods = ops_ai_query.k8s_pods(namespace=namespace, all_namespaces=namespace is None, selector=None)["pods"]
    for pod in pods:
        bad_container = next((c for c in pod.get("containers", []) if pod.get("phase") == "Running" and not c.get("ready")), None)
        if pod.get("phase") not in {"Running", "Succeeded"} or bad_container:
            issues.append({
                "source": "k3s",
                "severity": "warning",
                "name": f"{pod.get('namespace')}/{pod.get('name')}",
                "reason": "pod_unhealthy",
                "summary": f"Pod {pod.get('namespace')}/{pod.get('name')} phase={pod.get('phase')} restarts={pod.get('restarts')}",
                "details": pod,
            })
        elif int(pod.get("restarts") or 0) >= 5:
            issues.append({
                "source": "k3s",
                "severity": "warning",
                "name": f"{pod.get('namespace')}/{pod.get('name')}",
                "reason": "pod_restarts",
                "summary": f"Pod {pod.get('namespace')}/{pod.get('name')} restart count is {pod.get('restarts')}",
                "details": pod,
            })

    events = ops_ai_query.k8s_events(namespace=namespace, all_namespaces=namespace is None, limit=event_limit)["events"]
    for event in events:
        if event.get("type") == "Warning":
            obj = event.get("object", {})
            issues.append({
                "source": "k3s-event",
                "severity": "warning",
                "name": f"{event.get('namespace')}/{obj.get('kind')}/{obj.get('name')}",
                "reason": event.get("reason"),
                "summary": f"{event.get('reason')}: {event.get('message')}",
                "details": event,
            })

    try:
        up = ops_ai_query.vm_query(vm_url, ops_ai_query.PROMQL_TEMPLATES["up"])
        for metric, value in float_value(up):
            if value < 1:
                name = metric.get("job") or metric.get("instance") or json.dumps(metric, sort_keys=True)
                issues.append({
                    "source": "victoriametrics",
                    "severity": "critical",
                    "name": name,
                    "reason": "scrape_target_down",
                    "summary": f"Metrics target down: {name}",
                    "details": metric,
                })
    except Exception as exc:
        issues.append({
            "source": "victoriametrics",
            "severity": "warning",
            "name": vm_url,
            "reason": "query_failed",
            "summary": f"VictoriaMetrics query failed: {exc}",
            "details": {"vmUrl": vm_url},
        })

    try:
        cpu = ops_ai_query.vm_query(vm_url, ops_ai_query.PROMQL_TEMPLATES["node_cpu_percent"])
        for metric, value in float_value(cpu):
            if value >= 85:
                name = metric.get("node") or metric.get("instance") or json.dumps(metric, sort_keys=True)
                issues.append({
                    "source": "victoriametrics",
                    "severity": "warning",
                    "name": name,
                    "reason": "node_cpu_high",
                    "summary": f"Node CPU high: {name} {value:.1f}%",
                    "details": {"metric": metric, "value": value},
                })
    except Exception:
        pass

    return issues


def render_issue(issue: dict[str, Any]) -> str:
    return f"- **{issue.get('severity','info').upper()}** `{issue.get('reason')}` {issue.get('summary')}"


def notify_changes(issues: list[dict[str, Any]], state: dict[str, Any], webhook: str, secret: str, dry_run: bool) -> bool:
    active = {issue_key(issue): issue for issue in issues}
    previous = state.get("active", {})
    changed = False

    new_keys = [k for k in active if k not in previous]
    resolved_keys = [k for k in previous if k not in active]

    if new_keys:
        lines = [render_issue(active[k]) for k in new_keys]
        max_sev = "critical" if any(active[k].get("severity") == "critical" for k in new_keys) else "warning"
        send_feishu(webhook, secret, f"Ops Alert: {len(new_keys)} new issue(s)", lines, max_sev, dry_run)
        changed = True

    if resolved_keys:
        lines = [f"- RESOLVED `{previous[k].get('reason')}` {previous[k].get('summary')}" for k in resolved_keys]
        send_feishu(webhook, secret, f"Ops Resolved: {len(resolved_keys)} issue(s)", lines, "resolved", dry_run)
        changed = True

    state["active"] = active
    state["updatedAt"] = now_iso()
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="External watcher for K3S + VictoriaMetrics + Feishu.")
    parser.add_argument("--vm-url", default=os.environ.get("OPS_VM_URL", "http://127.0.0.1:8428").rstrip("/"))
    parser.add_argument("--namespace", help="Limit pod/event checks to one namespace. Default: all namespaces.")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("OPS_WATCH_INTERVAL", "60")))
    parser.add_argument("--event-limit", type=int, default=50)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--feishu-webhook", default=DEFAULT_WEBHOOK)
    parser.add_argument("--feishu-secret", default=DEFAULT_SECRET)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        state = load_state(args.state_file)
        try:
            issues = collect_issues(args.vm_url, args.namespace, args.event_limit)
            notify_changes(issues, state, args.feishu_webhook, args.feishu_secret, args.dry_run)
            save_state(args.state_file, state)
            print(json.dumps({"timestamp": now_iso(), "activeIssues": len(issues)}, ensure_ascii=False))
        except Exception as exc:
            issue = {
                "source": "ops-watch-agent",
                "severity": "critical",
                "name": "ops-watch-agent",
                "reason": "watch_failed",
                "summary": str(exc),
            }
            notify_changes([issue], state, args.feishu_webhook, args.feishu_secret, args.dry_run)
            save_state(args.state_file, state)
            print(json.dumps({"timestamp": now_iso(), "error": str(exc)}, ensure_ascii=False))
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
