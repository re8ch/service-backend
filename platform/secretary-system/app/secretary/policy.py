from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .model import FactEvent, Severity, parse_time


RECOVERY_TYPES = {"health.recovered", "probe.observed", "observation.recorded"}
NEGATIVE_TYPES = {"health.failed", "contradiction.recorded"}


@dataclass(frozen=True)
class ResolutionDecision:
    allowed: bool
    next_state: str
    reasons: tuple[str, ...]
    independent_evidence: int


def evaluate_resolution(
    severity: Severity,
    events: list[FactEvent],
    stable_for_seconds: int,
    now: dt.datetime | None = None,
    reversible: bool = False,
    user_data_impact: bool = True,
) -> ResolutionDecision:
    now = now or dt.datetime.now(dt.timezone.utc)
    recoveries = [
        event for event in events
        if event.type in RECOVERY_TYPES
        and event.authority in {"tool_observation", "authenticated_action"}
        and event.evidence.strength in {"supporting", "authoritative"}
        and event.evidence.confidence >= 0.8
        and bool(event.data.get("recovered", event.type == "health.recovered"))
    ]
    keys = {event.independence_key() for event in recoveries}
    latest_recovery = max((parse_time(event.observed_at) for event in recoveries), default=None)
    latest_negative = max((parse_time(event.observed_at) for event in events if event.type in NEGATIVE_TYPES), default=None)
    has_unresolved_negative = bool(latest_negative and (not latest_recovery or latest_negative >= latest_recovery))
    stable = bool(recoveries) and all((now - parse_time(event.observed_at)).total_seconds() >= stable_for_seconds for event in recoveries)
    reasons: list[str] = []
    if len(keys) < 2:
        reasons.append("fewer_than_two_independent_recovery_observations")
    if not stable:
        reasons.append("stability_window_not_met")
    if has_unresolved_negative:
        reasons.append("unresolved_negative_evidence")
    if not any(event.type == "resolution.proposed" for event in events):
        reasons.append("resolution_artifact_missing")
    evidence_ready = not reasons
    if severity == "critical":
        human_close = any(
            event.type == "human.responded"
            and event.actor.kind == "human"
            and event.authority == "authenticated_action"
            and event.data.get("action") == "confirm_resolution"
            for event in events
        )
        if not human_close:
            reasons.append("critical_requires_authenticated_human_close")
        return ResolutionDecision(evidence_ready and human_close, "resolved" if evidence_ready and human_close else "verified", tuple(reasons), len(keys))
    if severity == "high" and (not reversible or user_data_impact):
        human_close = any(event.type == "human.responded" and event.actor.kind == "human" and event.authority == "authenticated_action" and event.data.get("action") == "confirm_resolution" for event in events)
        if not human_close:
            reasons.append("high_risk_requires_authenticated_human_close")
        evidence_ready = evidence_ready and human_close
    return ResolutionDecision(evidence_ready, "resolved" if evidence_ready else "investigating", tuple(reasons), len(keys))


def delivery_is_ack(event: FactEvent) -> bool:
    return (
        event.type == "responsibility.acknowledged"
        and event.authority == "authenticated_action"
        and event.actor.kind == "human"
        and bool(event.data.get("identity_verified"))
    )
