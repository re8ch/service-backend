from __future__ import annotations

import hashlib
import html
import hmac
import json
import os
import re
import urllib.parse
import urllib.request
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .channels import EmailAdapter
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


def fact(incident_id: str, event_type: str, subject: str, *, actor_kind: str, actor_id: str,
         source: str, authority: str, strength: str, failure_domain: str, method: str,
         idempotency_key: str, data: dict[str, Any], causation_id: str = "root") -> FactEvent:
    event_id = str(uuid.uuid4())
    return FactEvent.from_dict({
        "event_id": event_id, "incident_id": incident_id, "type": event_type, "subject": subject,
        "actor": {"kind": actor_kind, "id": actor_id}, "observed_at": utc_now(), "source": source,
        "authority": authority, "evidence": {"strength": strength, "failure_domain": failure_domain,
        "artifact_ref": data.get("artifact_ref", ""), "confidence": 1, "method": method},
        "causation_id": causation_id, "correlation_id": incident_id,
        "idempotency_key": idempotency_key, "data": data,
    })


def send_email_notification(incident_id: str, body: dict[str, Any]) -> dict[str, Any]:
    request_key = str(body["idempotency_key"])
    recipient_id, recipient_email = str(body["recipient_id"]), str(body["to"])
    existing = db().events(incident_id)
    completed = next((event for event in existing if event.type == "delivery.provider_accepted" and event.data.get("request_key") == request_key), None)
    if completed:
        return {"accepted": True, "replayed": True, "event_id": completed.event_id}

    code = hmac.new(os.environ["SECRETARY_WEBHOOK_SECRET"].encode(), f"email-ack:{incident_id}:{recipient_id}:{request_key}".encode(), hashlib.sha256).hexdigest()[:16]
    assignment = fact(incident_id, "responsibility.assigned", recipient_id, actor_kind="system", actor_id="secretary",
        source="secretary", authority="authenticated_action", strength="authoritative", failure_domain=f"human-{recipient_id}",
        method="responsibility-contract", idempotency_key=f"{request_key}:assignment",
        data={"recipient_id": recipient_id, "action_code_sha256": hashlib.sha256(code.encode()).hexdigest(), "channel": "email"})
    if not db().append(assignment):
        assignment = next(event for event in db().events(incident_id) if event.idempotency_key == f"{request_key}:assignment")

    accepted = fact(incident_id, "delivery.accepted", recipient_email, actor_kind="system", actor_id="secretary",
        source="secretary", authority="authenticated_action", strength="authoritative", failure_domain="secretary-system",
        method="email-dispatch", idempotency_key=f"{request_key}:accepted", causation_id=assignment.event_id,
        data={"channel": "email", "recipient_id": recipient_id, "request_key": request_key})
    db().append(accepted)
    public_base = os.environ.get("SECRETARY_PUBLIC_BASE_URL", "https://tools.re8ch.com").rstrip("/")
    query = urllib.parse.urlencode({"incident_id": incident_id, "recipient_id": recipient_id, "code": code})
    ack_url = f"{public_base}/v1/channels/email/ack?{query}"
    message_id = f"<secretary.{incident_id}.{hashlib.sha256(request_key.encode()).hexdigest()[:16]}@phonaid.com>"
    adapter = EmailAdapter(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")),
        os.environ.get("SMTP_USERNAME", ""), os.environ.get("SMTP_PASSWORD", ""), os.environ["SMTP_FROM"],
        os.environ.get("SMTP_STARTTLS", "true").lower() == "true", os.environ.get("SMTP_TLS_FINGERPRINT_SHA256", ""))
    try:
        result = adapter.send(recipient=recipient_email, subject=str(body.get("subject") or f"[Secretary] {incident_id}"),
            text=str(body.get("text") or "An incident needs your acknowledgement.") + f"\n\nAcknowledge responsibility: {ack_url}\nIncident: {incident_id}\n",
            message_id=message_id)
        provider = fact(incident_id, "delivery.provider_accepted", recipient_email, actor_kind="provider", actor_id="stalwart",
            source="stalwart", authority="provider_callback", strength="supporting", failure_domain="stalwart",
            method="smtp-250", idempotency_key=f"{request_key}:provider-accepted", causation_id=accepted.event_id,
            data={"channel": "email", "recipient_id": recipient_id, "request_key": request_key, "message_id": result["message_id"]})
        db().append(provider)
        return {"accepted": True, "replayed": False, "event_id": provider.event_id}
    except Exception as exc:
        failed = fact(incident_id, "delivery.failed", recipient_email, actor_kind="provider", actor_id="stalwart",
            source="stalwart", authority="provider_callback", strength="supporting", failure_domain="stalwart",
            method="smtp", idempotency_key=f"{request_key}:failed:{uuid.uuid4()}", causation_id=accepted.event_id,
            data={"channel": "email", "recipient_id": recipient_id, "request_key": request_key, "error_class": type(exc).__name__})
        db().append(failed)
        raise HTTPException(502, "email provider rejected notification") from exc


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
        url = f"{os.environ.get('RESTATE_INGRESS_URL', 'http://restate:8080').rstrip('/')}/IncidentWorkflow/{incident_id}/run/send"
        request = urllib.request.Request(url, data=json.dumps({"severity": body["severity"], "responsibility_contract": body["responsibility_contract"], "ack_timeout_seconds": body.get("ack_timeout_seconds", 180), "investigation_timeout_seconds": body.get("investigation_timeout_seconds", 900)}).encode(), method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    notification = None
    if inserted and body.get("notification_email"):
        notification = send_email_notification(incident_id, {**body["notification_email"], "idempotency_key": f"{body['idempotency_key']}:email"})
    return {"incident_id": incident_id, "inserted": inserted, "notification": notification}


@app.post("/v1/incidents/{incident_id}/notifications/email", status_code=202)
async def notify_email(incident_id: str, request: Request, x_secretary_timestamp: str | None = Header(None), x_secretary_signature: str | None = Header(None)) -> dict[str, Any]:
    raw = await request.body()
    verify_hmac(raw, x_secretary_timestamp, x_secretary_signature)
    return send_email_notification(incident_id, json.loads(raw))


@app.get("/v1/incidents/{incident_id}/events")
def incident_events(incident_id: str) -> list[dict[str, Any]]:
    return [event.to_dict() for event in db().events(incident_id)]


@app.get("/v1/event-stream")
def event_stream(after_seq: int = 0, incident_id: str | None = None, limit: int = 100,
                 x_secretary_query_token: str | None = Header(None)) -> dict[str, Any]:
    if not x_secretary_query_token or not hmac.compare_digest(x_secretary_query_token, os.environ["SECRETARY_QUERY_TOKEN"]):
        raise HTTPException(401, "invalid query token")
    items = db().stream(after_seq=after_seq, incident_id=incident_id, limit=limit)
    return {"items": items, "next_after_seq": items[-1]["seq"] if items else after_seq}


@app.get("/v1/channels/email/ack", response_class=HTMLResponse)
def email_ack_form(incident_id: str, recipient_id: str, code: str) -> str:
    if not valid_action_code(incident_id, recipient_id, code):
        raise HTTPException(403, "invalid or expired acknowledgement")
    fields = "".join(f'<input type="hidden" name="{name}" value="{html.escape(value, quote=True)}">' for name, value in
        (("incident_id", incident_id), ("recipient_id", recipient_id), ("code", code)))
    return f"<html><body><h1>Secretary incident</h1><p>Confirm that you accept responsibility for {html.escape(incident_id)}.</p><form method=\"post\" action=\"/v1/channels/email/ack\">{fields}<button type=\"submit\">Acknowledge responsibility</button></form></body></html>"


@app.post("/v1/channels/email/ack", response_class=HTMLResponse)
async def email_ack(request: Request) -> str:
    raw = (await request.body()).decode()
    form = urllib.parse.parse_qs(raw, strict_parsing=True)
    incident_id, recipient_id, code = (form[name][0] for name in ("incident_id", "recipient_id", "code"))
    if not valid_action_code(incident_id, recipient_id, code):
        raise HTTPException(403, "invalid or expired acknowledgement")
    event = fact(incident_id, "responsibility.acknowledged", recipient_id, actor_kind="human", actor_id=recipient_id,
        source="email", authority="authenticated_action", strength="authoritative", failure_domain=f"human-{recipient_id}",
        method="email-action-code", idempotency_key=f"email-ack:{incident_id}:{recipient_id}",
        data={"action": "ack", "identity_verified": True, "channel": "email"})
    inserted = db().append(event)
    if inserted:
        restate_signal(incident_id, "acknowledge", event.to_dict())
    return "<html><body><h1>Secretary acknowledgement recorded</h1><p>You accepted responsibility for this incident.</p></body></html>"


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
