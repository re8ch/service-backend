#!/usr/bin/env python3
"""Deterministic checkpoint and transition guard for bounded Centurion sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


VERSION = 1
TERMINAL = {"completed", "failed", "cancelled"}
TRANSITIONS = {
    "initializing": {"planning", "failed", "cancelled"},
    "planning": {"working", "paused", "failed", "cancelled"},
    "working": {"evaluating", "paused", "failed", "cancelled"},
    "evaluating": {"working", "completed", "paused", "failed", "cancelled"},
    "paused": {"planning", "working", "failed", "cancelled"},
}
SECRET_PATTERN = re.compile(
    r"(api[_-]?key|authorization|bearer|client[_-]?secret|private[_-]?key|password|access[_-]?token|refresh[_-]?token)",
    re.IGNORECASE,
)


class ContractError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ContractError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def canonical(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "integrity"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()


def seal(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["integrity"] = {"algorithm": "sha256", "digest": hashlib.sha256(canonical(result)).hexdigest()}
    return result


def validate(payload: dict[str, Any], now: datetime | None = None) -> None:
    now = now or utcnow()
    required = {
        "version", "run_id", "issue", "objective", "state", "created_at", "deadline_at",
        "updated_at", "lease", "budget", "plan", "completed", "next_action", "evidence",
        "failures", "blocker", "events", "integrity",
    }
    missing = required - payload.keys()
    if missing:
        raise ContractError(f"missing fields: {', '.join(sorted(missing))}")
    if payload["version"] != VERSION:
        raise ContractError("unsupported checkpoint version")
    if payload["state"] not in {*TRANSITIONS, *TERMINAL}:
        raise ContractError("invalid state")
    if SECRET_PATTERN.search(json.dumps({key: value for key, value in payload.items() if key != "integrity"})):
        raise ContractError("checkpoint contains a credential-shaped field or value")
    expected = hashlib.sha256(canonical(payload)).hexdigest()
    if payload["integrity"] != {"algorithm": "sha256", "digest": expected}:
        raise ContractError("checkpoint integrity mismatch")
    created = parse_time(payload["created_at"])
    deadline = parse_time(payload["deadline_at"])
    parse_time(payload["updated_at"])
    if deadline <= created or deadline - created > timedelta(hours=24):
        raise ContractError("deadline must be within 24 hours")
    budget = payload["budget"]
    for used, maximum in (("sessions_used", "max_sessions"), ("failures_used", "max_failures")):
        if not isinstance(budget.get(used), int) or not isinstance(budget.get(maximum), int):
            raise ContractError("budget values must be integers")
        if budget[used] < 0 or budget[maximum] < 1 or budget[used] > budget[maximum]:
            raise ContractError(f"invalid budget: {used}")
    lease = payload["lease"]
    if lease is not None:
        if set(lease) != {"owner", "expires_at"} or not lease["owner"]:
            raise ContractError("invalid lease")
        parse_time(lease["expires_at"])
    if payload["state"] not in TERMINAL and now >= deadline:
        raise ContractError("wall-clock budget exhausted")


def new_checkpoint(args: argparse.Namespace, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    hours = min(max(args.hours, 1), 24)
    payload = {
        "version": VERSION,
        "run_id": args.run_id,
        "issue": args.issue,
        "objective": args.objective,
        "state": "initializing",
        "created_at": timestamp(now),
        "deadline_at": timestamp(now + timedelta(hours=hours)),
        "updated_at": timestamp(now),
        "lease": None,
        "budget": {"sessions_used": 0, "max_sessions": args.max_sessions, "failures_used": 0, "max_failures": args.max_failures},
        "plan": [], "completed": [], "next_action": "establish a reviewed plan", "evidence": [],
        "failures": [], "blocker": None, "events": [],
    }
    return seal(payload)


def transition(payload: dict[str, Any], args: argparse.Namespace, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    validate(payload, now)
    matching = [event for event in payload["events"] if event["idempotency_key"] == args.idempotency_key]
    if matching:
        event = matching[0]
        if event["state"] != args.state or event["owner"] != args.owner:
            raise ContractError("idempotency key was already used for a different transition")
        return payload
    source, target = payload["state"], args.state
    if source in TERMINAL or target not in TRANSITIONS.get(source, set()):
        raise ContractError(f"transition {source} -> {target} is not allowed")
    result = {key: value for key, value in payload.items() if key != "integrity"}
    lease = result["lease"]
    if lease and parse_time(lease["expires_at"]) > now and lease["owner"] != args.owner:
        raise ContractError(f"active lease belongs to {lease['owner']}")
    if result["budget"]["sessions_used"] >= result["budget"]["max_sessions"]:
        raise ContractError("session budget exhausted")
    if result["budget"]["failures_used"] >= result["budget"]["max_failures"]:
        raise ContractError("failure budget exhausted")
    result["state"] = target
    result["updated_at"] = timestamp(now)
    result["lease"] = None if target in TERMINAL or target == "paused" else {
        "owner": args.owner, "expires_at": timestamp(now + timedelta(minutes=args.lease_minutes))
    }
    result["next_action"] = args.next_action
    result["budget"]["sessions_used"] += 1
    if args.failure:
        result["budget"]["failures_used"] += 1
        result["failures"].append({"at": timestamp(now), "summary": args.failure})
    if args.evidence:
        result["evidence"].append(args.evidence)
    result["events"].append({
        "idempotency_key": args.idempotency_key, "state": target, "owner": args.owner, "at": timestamp(now)
    })
    return seal(result)


def read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read checkpoint: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("checkpoint root must be an object")
    return value


def write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("path", type=Path); init.add_argument("--run-id", required=True)
    init.add_argument("--issue", required=True); init.add_argument("--objective", required=True)
    init.add_argument("--hours", type=int, default=24); init.add_argument("--max-sessions", type=int, default=48)
    init.add_argument("--max-failures", type=int, default=5)
    check = commands.add_parser("validate"); check.add_argument("path", type=Path)
    move = commands.add_parser("transition"); move.add_argument("path", type=Path)
    move.add_argument("--state", required=True); move.add_argument("--owner", required=True)
    move.add_argument("--lease-minutes", type=int, default=45); move.add_argument("--next-action", required=True)
    move.add_argument("--idempotency-key", required=True)
    move.add_argument("--evidence"); move.add_argument("--failure")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "init":
            payload = new_checkpoint(args)
        else:
            payload = read(args.path)
            if args.command == "validate":
                validate(payload)
                print(f"PASS centurion checkpoint {payload['run_id']} state={payload['state']}")
                return 0
            payload = transition(payload, args)
        write(args.path, payload)
        print(f"centurion checkpoint {payload['run_id']} state={payload['state']}")
        return 0
    except ContractError as exc:
        print(f"centurion contract rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
