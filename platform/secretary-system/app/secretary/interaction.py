from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Strength = Literal["weak", "supporting", "authoritative"]
PII_KEYS = {"email", "phone", "phone_number", "chat_id", "telegram_user", "username", "address"}


def supporting_evidence_is_adoptable(items: list[tuple[str, str, float]]) -> bool:
    """Require two independent evidence domains and at least 0.90 combined confidence."""
    independent: dict[str, tuple[str, float]] = {}
    for failure_domain, method, confidence in items:
        if "log" in method.lower():
            continue
        current = independent.get(failure_domain)
        if current is None or confidence > current[1]:
            independent[failure_domain] = (method, confidence)
    if len(independent) < 2:
        return False
    residual = 1.0
    for _, confidence in independent.values():
        residual *= 1 - confidence
    return 1 - residual >= 0.90


def _validate_metadata(value: dict[str, Any]) -> None:
    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).lower() in PII_KEYS:
                    raise ValueError("PII metadata is not allowed in the interaction ledger")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > 16_384:
        raise ValueError("metadata exceeds 16 KiB; store the body externally and use artifact_ref")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> None:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")


def _require_stable_ref(value: "EntityRef") -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{1,255}", value.id):
        raise ValueError("entity references must use stable opaque IDs")
    if value.kind.lower() in PII_KEYS:
        raise ValueError("PII identity kinds are not allowed in the interaction ledger")
    if value.kind.lower() in {"recipient", "person", "human"} and (
        "@" in value.id or re.fullmatch(r"\+?[0-9][0-9 -]{6,}", value.id)
    ):
        raise ValueError("recipient references must use stable opaque IDs, not direct addresses")
    if any(key in PII_KEYS for key in value.attributes):
        raise ValueError("PII attributes must be stored in the separate identity binding store")


@dataclass(frozen=True)
class EntityRef:
    kind: str
    id: str
    runtime_id: str = ""
    attributes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("entity kind is required")
        _require_stable_ref(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EntityRef":
        return cls(
            kind=str(value["kind"]), id=str(value["id"]),
            runtime_id=str(value.get("runtime_id") or ""),
            attributes={str(k): str(v) for k, v in (value.get("attributes") or {}).items()},
        )


@dataclass(frozen=True)
class InteractionEvidence:
    strength: Strength
    failure_domain: str
    method: str
    confidence: float
    artifact_ref: str = ""
    payload_hash: str = ""
    schema_ref: str = ""
    media_type: str = ""

    def __post_init__(self) -> None:
        if not self.failure_domain or not self.method:
            raise ValueError("failure_domain and method are required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.payload_hash and not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", self.payload_hash):
            raise ValueError("payload_hash must be a SHA-256 digest")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InteractionEvidence":
        return cls(
            strength=str(value["strength"]), failure_domain=str(value["failure_domain"]),
            method=str(value["method"]), confidence=float(value["confidence"]),
            artifact_ref=str(value.get("artifact_ref") or ""),
            payload_hash=str(value.get("payload_hash") or ""),
            schema_ref=str(value.get("schema_ref") or ""),
            media_type=str(value.get("media_type") or ""),
        )


@dataclass(frozen=True)
class InteractionObservation:
    observation_id: str
    interaction_id: str
    native_type: str
    native_status: str
    relation: str
    source: EntityRef
    target: EntityRef
    runtime: EntityRef
    observed_at: str
    received_at: str
    authority: str
    evidence: InteractionEvidence
    idempotency_key: str
    correlation_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid.UUID(self.observation_id)
        if not self.interaction_id or not self.native_type or not self.native_status or not self.relation:
            raise ValueError("interaction_id, native_type, native_status and relation are required")
        if not self.authority or not self.idempotency_key:
            raise ValueError("authority and idempotency_key are required")
        _parse_time(self.observed_at)
        _parse_time(self.received_at)
        _validate_metadata(self.metadata)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InteractionObservation":
        forbidden = {"payload", "raw_payload", "body", "message"} & {str(key).lower() for key in value}
        if forbidden:
            raise ValueError("raw message bodies are not accepted; use evidence.payload_hash and artifact_ref")
        return cls(
            observation_id=str(value.get("observation_id") or uuid.uuid4()),
            interaction_id=str(value["interaction_id"]), native_type=str(value["native_type"]),
            native_status=str(value["native_status"]), relation=str(value["relation"]),
            source=EntityRef.from_dict(value["source"]), target=EntityRef.from_dict(value["target"]),
            runtime=EntityRef.from_dict(value["runtime"]),
            observed_at=str(value["observed_at"]), received_at=str(value.get("received_at") or utc_now()),
            authority=str(value["authority"]), evidence=InteractionEvidence.from_dict(value["evidence"]),
            idempotency_key=str(value["idempotency_key"]),
            correlation_id=str(value.get("correlation_id") or value["interaction_id"]),
            metadata=dict(value.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def edge_key(self) -> str:
        raw = f"{self.source.kind}:{self.source.id}>{self.relation}>{self.target.kind}:{self.target.id}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def document_hash(self) -> str:
        raw = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class TopologyDeclaration:
    declaration_id: str
    source: EntityRef
    target: EntityRef
    relation: str
    owner_id: str
    declared_at: str
    idempotency_key: str
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid.UUID(self.declaration_id)
        if not self.relation or not self.owner_id or not self.idempotency_key:
            raise ValueError("relation, owner_id and idempotency_key are required")
        _parse_time(self.declared_at)
        _validate_metadata(self.metadata)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TopologyDeclaration":
        return cls(
            declaration_id=str(value.get("declaration_id") or uuid.uuid4()),
            source=EntityRef.from_dict(value["source"]), target=EntityRef.from_dict(value["target"]),
            relation=str(value["relation"]), owner_id=str(value["owner_id"]),
            declared_at=str(value.get("declared_at") or utc_now()),
            idempotency_key=str(value["idempotency_key"]), enabled=bool(value.get("enabled", True)),
            metadata=dict(value.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def edge_key(self) -> str:
        raw = f"{self.source.kind}:{self.source.id}>{self.relation}>{self.target.kind}:{self.target.id}"
        return hashlib.sha256(raw.encode()).hexdigest()
