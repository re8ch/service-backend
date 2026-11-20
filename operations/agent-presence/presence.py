#!/usr/bin/env python3
"""Agent presence: GitHub-native work claims with TTL, heartbeat and release.

Contract: contracts/agent-presence/README.md. Stdlib only, so it runs in any
sandbox (Claude, Codex, Cursor, in-cluster) that has a GitHub token.

Safety invariants:
- A claim is an open Issue labelled ``agent-claim`` in the board repository.
- A claim is void once ``expires_at`` has passed; readers never trust a stale
  claim, so no background sweeper is required for correctness.
- Among overlapping active exclusive claims, the lowest Issue number wins.
  GitHub assigns Issue numbers atomically, so every agent computes the same
  winner without a lock service.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SCHEMA = "presence.re8ch.com/claim/v1"
BOARD_REPO = os.getenv("AGENT_PRESENCE_REPO", "re8ch/service-backend")
CLAIM_LABEL = "agent-claim"
WORKING_LABEL = "agent:working"
MARKER = "<!-- re8ch-agent-presence v1 -->"
BLOCK_RE = re.compile(r"```re8ch-claim\s*\n(.*?)\n```", re.S)
GLOB_CHARS = "*?["
EXIT_CONFLICT = 3
EXIT_REVOKED = 4


# ---------------------------------------------------------------- time

def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_ttl(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smh]?)", value.strip())
    if not match:
        raise ValueError(f"invalid ttl {value!r}; use e.g. 900, 30m, 2h")
    amount, unit = int(match.group(1)), match.group(2) or "s"
    seconds = amount * {"s": 1, "m": 60, "h": 3600}[unit]
    if not 60 <= seconds <= 8 * 3600:
        raise ValueError("ttl must be between 1m and 8h; heartbeat to extend")
    return seconds


# ---------------------------------------------------------------- resource keys

def parse_key(key: str) -> tuple[str, tuple[str, ...]]:
    """Return (kind, scope) where scope is comparable by prefix."""
    kind, sep, rest = key.partition(":")
    if not sep or not rest:
        raise ValueError(f"resource key {key!r} must look like kind:value")
    kind = kind.lower()
    if kind == "repo":
        return kind, (rest.lower(),)
    if kind == "gh":
        repo, _, number = rest.partition("#")
        if not number.isdigit():
            raise ValueError(f"gh key {key!r} must be gh:owner/repo#N")
        return kind, (repo.lower(), number)
    if kind == "branch":
        repo, _, branch = rest.partition("@")
        if not branch:
            raise ValueError(f"branch key {key!r} must be branch:owner/repo@name")
        return kind, (repo.lower(), branch)
    if kind == "path":
        repo, _, glob = rest.partition(":")
        if not glob:
            raise ValueError(f"path key {key!r} must be path:owner/repo:glob")
        return kind, (repo.lower(), glob.lstrip("/"))
    if kind == "k8s":
        return kind, tuple(part for part in rest.split("/") if part)
    return kind, (rest,)


def _literal_prefix(glob: str) -> str:
    cut = min((glob.index(c) for c in GLOB_CHARS if c in glob), default=len(glob))
    return glob[:cut]


def _paths_overlap(a: str, b: str) -> bool:
    pa, pb = _literal_prefix(a), _literal_prefix(b)
    if pa == a and pb == b:
        return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")
    return pa.startswith(pb) or pb.startswith(pa)


def keys_overlap(a: str, b: str) -> bool:
    """Conservative overlap: false positives are acceptable, misses are not."""
    if a == b:
        return True
    (ka, sa), (kb, sb) = parse_key(a), parse_key(b)
    repo_scoped = {"repo", "gh", "branch", "path"}
    if ka in repo_scoped and kb in repo_scoped:
        if sa[0] != sb[0]:
            return False
        if "repo" in (ka, kb):
            return True
        if ka != kb:
            return False
        if ka == "path":
            return _paths_overlap(sa[1], sb[1])
        return sa == sb
    if ka == kb == "k8s":
        shorter = min(len(sa), len(sb))
        return sa[:shorter] == sb[:shorter]
    return ka == kb and sa == sb


# ---------------------------------------------------------------- claim model

def render_body(claim: dict[str, Any]) -> str:
    rows = "\n".join(f"| `{key}` |" for key in claim["resources"])
    return (
        f"{MARKER}\n"
        f"**{claim['agent']}** ({claim['runtime']}) is working on this. "
        f"Mode `{claim['mode']}`, expires **{claim['expires_at']}** unless renewed.\n\n"
        f"> {claim['intent']}\n\n"
        f"| Resource |\n|---|\n{rows}\n\n"
        "Protocol: [contracts/agent-presence](https://github.com/re8ch/service-backend/"
        "tree/main/contracts/agent-presence). Close this Issue to revoke the claim.\n\n"
        f"```re8ch-claim\n{json.dumps(claim, indent=2, sort_keys=True)}\n```\n"
    )


def parse_body(body: str | None) -> dict[str, Any] | None:
    match = BLOCK_RE.search(body or "")
    if not match:
        return None
    try:
        claim = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if claim.get("schema") != SCHEMA or not claim.get("resources"):
        return None
    return claim


def new_claim(agent: str, runtime: str, resources: list[str], intent: str,
              ttl: int, mode: str, now: dt.datetime) -> dict[str, Any]:
    for key in resources:
        parse_key(key)
    if mode not in ("exclusive", "shared"):
        raise ValueError("mode must be exclusive or shared")
    return {
        "schema": SCHEMA,
        "agent": agent,
        "runtime": runtime,
        "resources": sorted(set(resources)),
        "mode": mode,
        "intent": intent,
        "started_at": iso(now),
        "heartbeat_at": iso(now),
        "ttl_seconds": ttl,
        "expires_at": iso(now + dt.timedelta(seconds=ttl)),
    }


def is_active(claim: dict[str, Any], now: dt.datetime) -> bool:
    try:
        return parse_time(claim["expires_at"]) > now
    except (KeyError, ValueError):
        return False


def conflicts(mine: dict[str, Any], other: dict[str, Any]) -> bool:
    if mine["agent"] == other["agent"]:
        return False
    if mine["mode"] != "exclusive" or other["mode"] != "exclusive":
        return False
    return any(keys_overlap(a, b) for a in mine["resources"] for b in other["resources"])


def blockers(number: int | None, claim: dict[str, Any],
             board: list[dict[str, Any]], now: dt.datetime) -> list[dict[str, Any]]:
    """Active claims that beat ``claim``. ``number`` None means not yet filed."""
    found = []
    for item in board:
        if item["number"] == number or not is_active(item["claim"], now):
            continue
        if number is not None and item["number"] > number:
            continue
        if conflicts(claim, item["claim"]):
            found.append(item)
    return sorted(found, key=lambda item: item["number"])


# ---------------------------------------------------------------- GitHub REST

def token() -> str:
    for name in ("AGENT_PRESENCE_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.getenv(name):
            return os.environ[name]
    try:
        return subprocess.run(["gh", "auth", "token"], check=True, capture_output=True,
                              text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("agent-presence: set GH_TOKEN/GITHUB_TOKEN (issues:write on the board repo)")


class GitHub:
    def __init__(self, auth: str, api: str = "https://api.github.com"):
        self.auth, self.api = auth, api.rstrip("/")

    def call(self, method: str, path: str, payload: Any = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.api + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.auth}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "re8ch-agent-presence",
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def board(self, repo: str) -> list[dict[str, Any]]:
        items, page = [], 1
        while True:
            batch = self.call("GET", f"/repos/{repo}/issues?state=open&labels={CLAIM_LABEL}"
                                     f"&per_page=100&page={page}")
            for issue in batch:
                claim = parse_body(issue.get("body"))
                if claim and "pull_request" not in issue:
                    items.append({"number": issue["number"], "url": issue["html_url"],
                                  "claim": claim})
            if len(batch) < 100:
                return items
            page += 1

    def issue(self, repo: str, number: int) -> dict[str, Any]:
        return self.call("GET", f"/repos/{repo}/issues/{number}")


def best_effort(action):
    try:
        return action()
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"agent-presence: mirror skipped: {error}", file=sys.stderr)
        return None


def mirror(gh: GitHub, number: int, url: str, claim: dict[str, Any], released: bool) -> None:
    """Project the claim onto every gh: target so humans see it where they work."""
    marker = f"<!-- re8ch-agent-presence:{BOARD_REPO}#{number} -->"
    for key in claim["resources"]:
        kind, scope = parse_key(key)
        if kind != "gh":
            continue
        repo, target = scope[0], scope[1]
        if released:
            text = f"{marker}\nReleased: **{claim['agent']}** finished ([claim]({url}))."
            best_effort(lambda: gh.call(
                "DELETE", f"/repos/{repo}/issues/{target}/labels/"
                          f"{urllib.parse.quote(WORKING_LABEL, safe='')}"))
        else:
            text = (f"{marker}\n**{claim['agent']}** ({claim['runtime']}) is working on this "
                    f"until {claim['expires_at']} (renewed by heartbeat). Intent: "
                    f"{claim['intent']}. Check [the claim]({url}) before editing; "
                    "close it to revoke.")
            best_effort(lambda: gh.call("POST", f"/repos/{repo}/issues/{target}/labels",
                                        {"labels": [WORKING_LABEL]}))
        comments = best_effort(lambda: gh.call(
            "GET", f"/repos/{repo}/issues/{target}/comments?per_page=100")) or []
        existing = next((c for c in comments if marker in (c.get("body") or "")), None)
        if existing:
            best_effort(lambda: gh.call("PATCH", f"/repos/{repo}/issues/comments/{existing['id']}",
                                        {"body": text}))
        elif not released:
            best_effort(lambda: gh.call("POST", f"/repos/{repo}/issues/{target}/comments",
                                        {"body": text}))


# ---------------------------------------------------------------- commands

def default_agent() -> tuple[str, str]:
    runtime = os.getenv("AGENT_RUNTIME")
    agent = os.getenv("AGENT_ID")
    if not runtime:
        if os.getenv("CLAUDE_CODE_REMOTE_SESSION_ID") or os.getenv("CLAUDE_CODE_REMOTE"):
            runtime = "claude-cloud"
        elif os.getenv("CODEX_SANDBOX") or os.getenv("CODEX_HOME"):
            runtime = "codex"
        elif os.getenv("CURSOR_AGENT") or os.getenv("CURSOR_TRACE_ID"):
            runtime = "cursor"
        elif os.getenv("KUBERNETES_SERVICE_HOST"):
            runtime = "cluster"
        else:
            runtime = "local"
    if not agent:
        session = (os.getenv("CLAUDE_CODE_REMOTE_SESSION_ID") or os.getenv("CODEX_TASK_ID")
                   or os.getenv("HOSTNAME") or "unknown")
        agent = f"{runtime}/{session}"
    return agent, runtime


def sweep(gh: GitHub, board: list[dict[str, Any]], now: dt.datetime) -> list[int]:
    closed = []
    for item in board:
        if is_active(item["claim"], now):
            continue
        best_effort(lambda: gh.call("POST", f"/repos/{BOARD_REPO}/issues/{item['number']}/comments",
                                    {"body": f"Expired at {item['claim']['expires_at']} "
                                             "without heartbeat; closed by agent-presence sweep."}))
        best_effort(lambda: gh.call("PATCH", f"/repos/{BOARD_REPO}/issues/{item['number']}",
                                    {"state": "closed", "state_reason": "not_planned"}))
        mirror(gh, item["number"], item["url"], item["claim"], released=True)
        closed.append(item["number"])
    return closed


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_claim(gh: GitHub, args: argparse.Namespace) -> int:
    agent, runtime = default_agent()
    now = utcnow()
    claim = new_claim(args.agent or agent, args.runtime or runtime, args.resource,
                      args.intent, parse_ttl(args.ttl), args.mode, now)
    board = gh.board(BOARD_REPO)
    sweep(gh, board, now)
    early = blockers(None, claim, board, now)
    if early:
        emit({"status": "conflict", "blockers": early})
        return EXIT_CONFLICT
    title = f"[agent-claim] {claim['agent']} → {claim['resources'][0]}"
    issue = gh.call("POST", f"/repos/{BOARD_REPO}/issues",
                    {"title": title[:240], "body": render_body(claim),
                     "labels": [CLAIM_LABEL]})
    number, url = issue["number"], issue["html_url"]
    time.sleep(args.settle)
    beaten = blockers(number, claim, gh.board(BOARD_REPO), utcnow())
    if beaten:
        gh.call("POST", f"/repos/{BOARD_REPO}/issues/{number}/comments",
                {"body": "Yielded to " + ", ".join(f"#{b['number']}" for b in beaten)})
        gh.call("PATCH", f"/repos/{BOARD_REPO}/issues/{number}",
                {"state": "closed", "state_reason": "not_planned"})
        emit({"status": "conflict", "yielded": number, "blockers": beaten})
        return EXIT_CONFLICT
    if not args.no_mirror:
        mirror(gh, number, url, claim, released=False)
    emit({"status": "granted", "claim": number, "url": url, "expires_at": claim["expires_at"]})
    return 0


def _own(gh: GitHub, number: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    issue = gh.issue(BOARD_REPO, number)
    return issue, parse_body(issue.get("body"))


def inbox(gh: GitHub, number: int, since: str | None) -> list[dict[str, Any]]:
    """Comments other agents or humans left on a claim since ``since``."""
    query = f"?per_page=100&since={urllib.parse.quote(since)}" if since else "?per_page=100"
    comments = gh.call("GET", f"/repos/{BOARD_REPO}/issues/{number}/comments{query}") or []
    return [{"author": (c.get("user") or {}).get("login"), "at": c.get("created_at"),
             "body": c.get("body", "")}
            for c in comments
            if (not since or (c.get("created_at") or "") >= since)
            and MARKER not in (c.get("body") or "")]


def cmd_heartbeat(gh: GitHub, args: argparse.Namespace) -> int:
    issue, claim = _own(gh, args.claim)
    now = utcnow()
    messages = inbox(gh, args.claim, claim.get("heartbeat_at") if claim else None)
    if issue["state"] != "open" or claim is None or not is_active(claim, now):
        emit({"status": "revoked", "claim": args.claim, "messages": messages,
              "reason": "closed" if issue["state"] != "open" else "expired"})
        return EXIT_REVOKED
    ttl = parse_ttl(args.ttl) if args.ttl else claim["ttl_seconds"]
    claim.update(heartbeat_at=iso(now), ttl_seconds=ttl,
                 expires_at=iso(now + dt.timedelta(seconds=ttl)))
    if args.intent:
        claim["intent"] = args.intent
    gh.call("PATCH", f"/repos/{BOARD_REPO}/issues/{args.claim}", {"body": render_body(claim)})
    emit({"status": "renewed", "claim": args.claim, "expires_at": claim["expires_at"],
          "messages": messages})
    return 0


def cmd_say(gh: GitHub, args: argparse.Namespace) -> int:
    agent, _ = default_agent()
    gh.call("POST", f"/repos/{BOARD_REPO}/issues/{args.claim}/comments",
            {"body": f"**{args.agent or agent}**: {args.message}"})
    emit({"status": "sent", "claim": args.claim})
    return 0


def cmd_release(gh: GitHub, args: argparse.Namespace) -> int:
    issue, claim = _own(gh, args.claim)
    note = args.note or "Released."
    gh.call("POST", f"/repos/{BOARD_REPO}/issues/{args.claim}/comments", {"body": note})
    if issue["state"] == "open":
        gh.call("PATCH", f"/repos/{BOARD_REPO}/issues/{args.claim}",
                {"state": "closed", "state_reason": "completed"})
    if claim:
        mirror(gh, args.claim, issue["html_url"], claim, released=True)
    emit({"status": "released", "claim": args.claim})
    return 0


def cmd_list(gh: GitHub, args: argparse.Namespace) -> int:
    now = utcnow()
    board = [item for item in gh.board(BOARD_REPO) if args.all or is_active(item["claim"], now)]
    if args.resource:
        board = [item for item in board
                 if any(keys_overlap(r, k) for r in args.resource for k in item["claim"]["resources"])]
    if args.json:
        emit({"board": BOARD_REPO, "claims": board})
        return 0
    if not board:
        print(f"No active agent claims on {BOARD_REPO}.")
    for item in board:
        c = item["claim"]
        print(f"#{item['number']:<6} {c['agent']:<40} {c['mode']:<9} until {c['expires_at']}")
        print(f"        {c['intent']}")
        for key in c["resources"]:
            print(f"        - {key}")
    return 0


def cmd_check(gh: GitHub, args: argparse.Namespace) -> int:
    agent, runtime = default_agent()
    probe = new_claim(args.agent or agent, runtime, args.resource, "check", 60,
                      "exclusive", utcnow())
    found = blockers(None, probe, gh.board(BOARD_REPO), utcnow())
    emit({"status": "busy" if found else "free", "blockers": found})
    return EXIT_CONFLICT if found else 0


def cmd_sweep(gh: GitHub, _: argparse.Namespace) -> int:
    emit({"closed": sweep(gh, gh.board(BOARD_REPO), utcnow())})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="presence", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim", help="claim resources before editing them")
    claim.add_argument("--resource", "-r", action="append", required=True)
    claim.add_argument("--intent", "-i", required=True)
    claim.add_argument("--ttl", default="30m")
    claim.add_argument("--mode", choices=["exclusive", "shared"], default="exclusive")
    claim.add_argument("--agent")
    claim.add_argument("--runtime")
    claim.add_argument("--settle", type=float, default=3.0,
                       help="seconds to wait before the race re-check")
    claim.add_argument("--no-mirror", action="store_true")
    claim.set_defaults(func=cmd_claim)

    beat = sub.add_parser("heartbeat",
                          help="renew a claim and read new messages; exit 4 if revoked")
    beat.add_argument("claim", type=int)
    beat.add_argument("--ttl")
    beat.add_argument("--intent")
    beat.set_defaults(func=cmd_heartbeat)

    say = sub.add_parser("say", help="message the holder of a claim (read at its next heartbeat)")
    say.add_argument("claim", type=int)
    say.add_argument("message")
    say.add_argument("--agent")
    say.set_defaults(func=cmd_say)

    release = sub.add_parser("release", help="close a claim when done")
    release.add_argument("claim", type=int)
    release.add_argument("--note")
    release.set_defaults(func=cmd_release)

    listing = sub.add_parser("list", help="show who is working on what")
    listing.add_argument("--resource", "-r", action="append")
    listing.add_argument("--all", action="store_true", help="include expired open claims")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)

    check = sub.add_parser("check", help="exit 3 if another agent holds an overlapping claim")
    check.add_argument("--resource", "-r", action="append", required=True)
    check.add_argument("--agent")
    check.set_defaults(func=cmd_check)

    sub.add_parser("sweep", help="close expired claims").set_defaults(func=cmd_sweep)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(GitHub(token()), args)
    except ValueError as error:
        print(f"agent-presence: {error}", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as error:
        print(f"agent-presence: GitHub {error.code}: {error.read().decode(errors='replace')}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
