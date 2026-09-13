from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import urllib.request
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from .ledger import PostgresLedger
from .model import FactEvent, utc_now
from .policy import delivery_is_ack, evaluate_resolution


app = FastAPI(title="RE8CH Secretary", version="0.1.0")
ledger: PostgresLedger | None = None


def db() -> PostgresLedger:
    global ledger
    if ledger is None:
        ledger = PostgresLedger(os.environ["SECRETARY_DATABASE_URL"])
    return ledger


def verify_hmac(raw: bytes, timestamp: str | None, signature: str | None) -> None:
    if not timestamp or not signature:
        raise HTTPException(401, "missing signature")
    expected = hmac.new(os.environ["SECRETARY_WEBHOOK_SECRET"].encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "invalid signature")


def restate_signal(incident_id: str, handler: str, payload: dict[str, Any]) -> None:
    url = f"{os.environ.get('RESTATE_INGRESS_URL', 'http://restate:8080').rstrip('/')}/IncidentWorkflow/{incident_id}/{handler}"
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def identity_bindings() -> dict[str, str]:
    try:
        value = json.loads(os.environ.get("SECRETARY_IDENTITY_BINDINGS", "{}"))
        return {str(key): str(recipient) for key, recipient in value.items()}
    except json.JSONDecodeError as exc:
        raise RuntimeError("SECRETARY_IDENTITY_BINDINGS must be JSON") from exc


def valid_action_code(incident_id: str, recipient_id: str, code: str) -> bool:
    digest = hashlib.sha256(code.encode()).hexdigest()
    return any(
        event.type == "responsibility.assigned"
        and event.data.get("recipient_id") == recipient_id
        and hmac.compare_digest(str(event.data.get("action_code_sha256", "")), digest)
        for event in db().events(incident_id)
    )


@app.get("/healthz")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/v1/events", status_code=202)
async def ingest(request: Request, x_secretary_timestamp: str | None = Header(None), x_secretary_signature: str | None = Header(None)) -> dict[str, Any]:
    raw = await request.body()
    verify_hmac(raw, x_secretary_timestamp, x_secretary_signature)
    event = FactEvent.from_dict(json.loads(raw))
    inserted = db().append(event)
    if inserted and delivery_is_ack(event):
        restate_signal(event.incident_id, "acknowledge", event.to_dict())
    return {"event_id": event.event_id, "inserted": inserted}


@app.post("/v1/incidents", status_code=202)
async def create_incident(request: Request, x_secretary_timestamp: str | None = Header(None), x_secretary_signature: str | None = Header(None)) -> dict[str, Any]:
    raw = await request.body()
    verify_hmac(raw, x_secretary_timestamp, x_secretary_signature)
    body = json.loads(raw)
    incident_id = body.get("incident_id") or f"inc_{uuid.uuid4().hex}"
    event = FactEvent.from_dict({
        "event_id": str(uuid.uuid4()), "incident_id": incident_id, "type": "task.started",
        "subject": body["service"], "actor": {"kind": "system", "id": body.get("source", "secretary-api")},
        "observed_at": body.get("observed_at") or utc_now(), "source": body.get("source", "secretary-api"),
        "authority": "tool_observation",
        "evidence": {"strength": "authoritative", "failure_domain": body.get("failure_domain", "secretary-api"), "artifact_ref": body.get("artifact_ref", ""), "confidence": 1, "method": "incident-ingest"},
        "idempotency_key": body["idempotency_key"], "data": {"severity": body["severity"], "responsibility_contract": body["responsibility_contract"]},
    })
    inserted = db().append(event)
    if inserted:
        url = f"{os.environ.get('RESTATE_INGRESS_URL', 'http://restate:8080').rstrip('/')}/IncidentWorkflow/{incident_id}/run"
        request = urllib.request.Request(url, data=json.dumps({"severity": body["severity"], "responsibility_contract": body["responsibility_contract"], "ack_timeout_seconds": body.get("ack_timeout_seconds", 180), "investigation_timeout_seconds": body.get("investigation_timeout_seconds", 900)}).encode(), method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    return {"incident_id": incident_id, "inserted": inserted}


@app.get("/v1/incidents/{incident_id}/events")
def incident_events(incident_id: str) -> list[dict[str, Any]]:
    return [event.to_dict() for event in db().events(incident_id)]


@app.post("/v1/incidents/{incident_id}/evaluate")
def evaluate(incident_id: str, body: dict[str, Any]) -> dict[str, Any]:
    decision = evaluate_resolution(
        severity=body["severity"], events=db().events(incident_id),
        stable_for_seconds=int(body.get("stable_for_seconds", 300)),
        reversible=bool(body.get("reversible", False)), user_data_impact=bool(body.get("user_data_impact", True)),
    )
    result = {"allowed": decision.allowed, "next_state": decision.next_state, "reasons": decision.reasons, "independent_evidence": decision.independent_evidence}
    restate_signal(incident_id, "resolution_result", result)
    return result


@app.post("/v1/channels/bluebubbles/{token}", status_code=202)
async def bluebubbles(token: str, request: Request) -> dict[str, Any]:
    if not hmac.compare_digest(token, os.environ["BLUEBUBBLES_WEBHOOK_TOKEN"]):
        raise HTTPException(404, "not found")
    payload = await request.json()
    message = payload.get("data") or payload
    text = str(message.get("text") or "")
    # BlueBubbles 1.9.x can emit played voice-note UUIDs as new messages; never interpret those as replies.
    if re.fullmatch(r"[0-9A-Fa-f-]{36}", text):
        return {"ignored": "uuid-shaped-message"}
    action_match = re.search(r"\b(ACK|HELP|VERIFY)\s+(inc_[A-Za-z0-9_-]{3,128})\s+([A-Za-z0-9]{6,32})\b", text, re.IGNORECASE)
    if not action_match:
        return {"ignored": "not-a-secretary-action"}
    action, incident_id, code = action_match.group(1).upper(), action_match.group(2), action_match.group(3)
    identity = str(message.get("handle", {}).get("address") or message.get("address") or "unknown")
    recipient_id = identity_bindings().get(f"imessage:{identity}", "")
    verified = bool(recipient_id) and valid_action_code(incident_id, recipient_id, code)
    event_type = "responsibility.acknowledged" if action == "ACK" and verified else "human.responded"
    event = FactEvent.from_dict({
        "event_id": str(uuid.uuid4()), "incident_id": incident_id, "type": event_type,
        "subject": identity, "actor": {"kind": "human", "id": recipient_id or identity}, "observed_at": utc_now(),
        "source": "bluebubbles", "authority": "authenticated_action",
        "evidence": {"strength": "authoritative" if verified else "supporting", "failure_domain": f"human-{recipient_id or identity}", "artifact_ref": "", "confidence": 1 if verified else .5, "method": "imessage-bound-reply"},
        "idempotency_key": f"bluebubbles:{message.get('guid') or hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}",
        "data": {"action": {"ACK": "ack", "HELP": "help", "VERIFY": "confirm_resolution"}[action], "identity_verified": verified},
    })
    inserted = db().append(event)
    if inserted and delivery_is_ack(event):
        restate_signal(incident_id, "acknowledge", event.to_dict())
    return {"event_id": event.event_id, "inserted": inserted}


@app.post("/v1/channels/voice/{token}", status_code=202)
async def voice_event(token: str, request: Request) -> dict[str, Any]:
    if not hmac.compare_digest(token, os.environ["VOICE_WEBHOOK_TOKEN"]):
        raise HTTPException(404, "not found")
    payload = await request.json()
    incident_id = payload["incident_id"]
    digit = str(payload.get("digit", ""))
    identity_verified = bool(payload.get("destination_verified"))
    voice_type = payload.get("type")
    if voice_type == "call.dtmf":
        event_type = "responsibility.acknowledged" if digit == "1" and identity_verified else "human.responded"
        authority, actor_kind, actor_id, method = "authenticated_action", "human", payload.get("recipient_id", "unknown"), "dtmf"
    else:
        failed = payload.get("status") == "failed"
        event_type = "delivery.failed" if failed else "delivery.provider_accepted"
        authority, actor_kind, actor_id, method = "provider_callback", "provider", "re8ch-voice-gateway", "voice-call-state"
    event = FactEvent.from_dict({
        "event_id": str(uuid.uuid4()), "incident_id": incident_id, "type": event_type,
        "subject": payload["call_id"], "actor": {"kind": actor_kind, "id": actor_id},
        "observed_at": utc_now(), "source": "freeswitch", "authority": authority,
        "evidence": {"strength": "authoritative" if identity_verified and digit else "supporting", "failure_domain": "r640-voice", "artifact_ref": payload["call_id"], "confidence": 1 if identity_verified and digit else .6, "method": method},
        "idempotency_key": f"voice:{payload['call_id']}:{payload.get('seq', digit)}",
        "data": {"digit": digit, "action": {"1": "ack", "2": "help", "3": "confirm_resolution"}.get(digit, "unknown"), "identity_verified": identity_verified},
    })
    inserted = db().append(event)
    if inserted and delivery_is_ack(event):
        restate_signal(incident_id, "acknowledge", event.to_dict())
    return {"event_id": event.event_id, "inserted": inserted}
