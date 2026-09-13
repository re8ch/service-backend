from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ActorKind = Literal["system", "agent", "human", "provider"]
Authority = Literal["provider_callback", "tool_observation", "authenticated_action", "model_inference"]
Strength = Literal["weak", "supporting", "authoritative"]
Severity = Literal["info", "warning", "high", "critical"]

EVENT_TYPES = {
    "health.failed", "health.recovered", "probe.observed",
    "delivery.accepted", "delivery.provider_accepted", "delivery.device_delivered",
    "delivery.human_seen", "delivery.failed",
    "responsibility.assigned", "responsibility.acknowledged", "responsibility.released",
    "responsibility.escalated", "hypothesis.created", "observation.recorded",
    "contradiction.recorded", "human.questioned", "human.responded", "task.started",
    "action.proposed", "action.executed", "task.progressed", "resolution.proposed",
    "resolution.verified", "incident.closed", "incident.reopened", "agent.performance",
}

MODEL_ALLOWED_TYPES = {"hypothesis.created", "human.questioned", "action.proposed", "resolution.proposed"}
OBSERVATION_AUTHORITIES = {"provider_callback", "tool_observation", "authenticated_action"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    id: str


@dataclass(frozen=True)
class Evidence:
    strength: Strength
    failure_domain: str
    artifact_ref: str
    confidence: float
    method: str

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("evidence confidence must be between 0 and 1")
        if not self.failure_domain or not self.method:
            raise ValueError("failure_domain and method are required")


@dataclass(frozen=True)
class FactEvent:
    event_id: str
    incident_id: str
    type: str
    subject: str
    actor: Actor
    observed_at: str
    received_at: str
    source: str
    authority: Authority
    evidence: Evidence
    causation_id: str
    correlation_id: str
    idempotency_key: str
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid.UUID(self.event_id)
        if not re.fullmatch(r"inc_[A-Za-z0-9_-]{3,128}", self.incident_id):
            raise ValueError("invalid incident_id")
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unsupported event type: {self.type}")
        if self.actor.kind == "agent" and self.authority != "model_inference" and self.type in MODEL_ALLOWED_TYPES:
            raise ValueError("agent proposals must use model_inference authority")
        if self.authority == "model_inference" and self.type not in MODEL_ALLOWED_TYPES | {"agent.performance"}:
            raise ValueError("model inference cannot create observation or state-transition facts")
        parse_time(self.observed_at)
        parse_time(self.received_at)
        if self.correlation_id != self.incident_id:
            raise ValueError("correlation_id must equal incident_id")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FactEvent":
        actor = value.get("actor") or {}
        evidence = value.get("evidence") or {}
        return cls(
            event_id=value.get("event_id") or str(uuid.uuid4()),
            incident_id=value["incident_id"], type=value["type"], subject=value["subject"],
            actor=Actor(kind=actor["kind"], id=actor["id"]),
            observed_at=value["observed_at"], received_at=value.get("received_at") or utc_now(),
            source=value["source"], authority=value["authority"],
            evidence=Evidence(
                strength=evidence["strength"], failure_domain=evidence["failure_domain"],
                artifact_ref=evidence.get("artifact_ref", ""), confidence=float(evidence["confidence"]),
                method=evidence["method"],
            ),
            causation_id=value.get("causation_id") or value.get("event_id") or "root",
            correlation_id=value.get("correlation_id") or value["incident_id"],
            idempotency_key=value["idempotency_key"], data=value.get("data") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def payload_hash(self) -> str:
        raw = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def independence_key(self) -> tuple[str, str, str]:
        return self.authority, self.evidence.failure_domain, self.evidence.method


@dataclass(frozen=True)
class ResponsibilityContract:
    contract_id: str
    incident_id: str
    assignee_kind: Literal["agent", "human"]
    assignee_id: str
    objective: str
    allowed_tools: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    required_evidence: tuple[str, ...]
    deadline: str
    max_model_cost_usd: float
    max_phone_calls: int
    escalation_target: str
    completion_definition: str


@dataclass(frozen=True)
class ChannelCapabilities:
    can_send: bool
    can_report_provider_acceptance: bool
    can_report_device_delivery: bool
    can_report_read: bool
    can_authenticate_reply: bool
    can_collect_ack: bool
    can_collect_structured_result: bool
    independent_failure_domain: str

