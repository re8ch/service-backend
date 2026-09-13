from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request
from typing import Any

import restate
from restate import ObjectContext, ObjectSharedContext, Service, VirtualObject, Workflow, WorkflowContext, WorkflowSharedContext, select


incident = Workflow("IncidentWorkflow")
recipient = VirtualObject("RecipientObject")
channel = VirtualObject("ChannelObject")
investigator = Service("InvestigatorService")


@incident.main()
async def run(ctx: WorkflowContext, request: dict[str, Any]) -> dict[str, Any]:
    ctx.set("state", "notifying")
    ctx.set("severity", request["severity"])
    ctx.set("contract", request["responsibility_contract"])
    ack_timeout = int(request.get("ack_timeout_seconds", 180))
    match await select(ack=ctx.promise("human.ack", type_hint=dict).value(), timeout=ctx.sleep(dt.timedelta(seconds=ack_timeout))):
        case ["ack", ack]:
            ctx.set("state", "acknowledged")
            ctx.set("ack", ack)
        case ["timeout", _]:
            ctx.set("state", "escalating")
            return {"incident_id": ctx.key(), "state": "escalating", "reason": "ack_timeout"}
    ctx.set("state", "investigating")
    match await select(result=ctx.promise("resolution.result", type_hint=dict).value(), timeout=ctx.sleep(dt.timedelta(seconds=int(request.get("investigation_timeout_seconds", 900))))):
        case ["result", result]:
            ctx.set("state", result.get("next_state", "investigating"))
            return {"incident_id": ctx.key(), **result}
        case ["timeout", _]:
            ctx.set("state", "escalating")
            return {"incident_id": ctx.key(), "state": "escalating", "reason": "investigation_timeout"}


@incident.handler()
async def acknowledge(ctx: WorkflowSharedContext, event: dict[str, Any]) -> None:
    await ctx.promise("human.ack", type_hint=dict).resolve(event)


@incident.handler()
async def resolution_result(ctx: WorkflowSharedContext, decision: dict[str, Any]) -> None:
    await ctx.promise("resolution.result", type_hint=dict).resolve(decision)


@incident.handler()
async def status(ctx: WorkflowSharedContext) -> dict[str, Any]:
    return {"incident_id": ctx.key(), "state": await ctx.get("state") or "unknown", "severity": await ctx.get("severity")}


@recipient.handler()
async def configure(ctx: ObjectContext, value: dict[str, Any]) -> dict[str, Any]:
    ctx.set("profile", value)
    return value


@recipient.handler()
async def assign(ctx: ObjectContext, incident_id: str) -> dict[str, Any]:
    assigned = await ctx.get("assigned", type_hint=list) or []
    if incident_id not in assigned:
        assigned.append(incident_id)
        ctx.set("assigned", assigned)
    return {"recipient_id": ctx.key(), "active_incidents": assigned, "load": len(assigned)}


@recipient.handler(kind="shared")
async def recipient_status(ctx: ObjectSharedContext) -> dict[str, Any]:
    assigned = await ctx.get("assigned", type_hint=list) or []
    return {"recipient_id": ctx.key(), "profile": await ctx.get("profile") or {}, "active_incidents": assigned}


@channel.handler()
async def observe(ctx: ObjectContext, observation: dict[str, Any]) -> dict[str, Any]:
    ctx.set("last_observation", observation)
    failures = int(await ctx.get("failures", type_hint=int) or 0)
    failures = failures + 1 if observation.get("healthy") is False else 0
    ctx.set("failures", failures)
    return {"endpoint_id": ctx.key(), "failures": failures, "healthy": failures == 0}


@channel.handler(kind="shared")
async def channel_status(ctx: ObjectSharedContext) -> dict[str, Any]:
    return {"endpoint_id": ctx.key(), "failures": await ctx.get("failures") or 0, "last_observation": await ctx.get("last_observation")}


@investigator.handler()
async def record_proposal(ctx: restate.Context, proposal: dict[str, Any]) -> dict[str, Any]:
    # Model-generated content remains a proposal. The API/policy gate records observations separately.
    return {"accepted_as": "proposal", "proposal": proposal}


@investigator.handler()
async def investigate(ctx: restate.Context, request: dict[str, Any]) -> dict[str, Any]:
    identity = request.get("execution_identity") or {}
    if not identity.get("task_id") or not identity.get("trace_id"):
        raise restate.TerminalError("task_id and trace_id are required", status_code=400)

    def call_litellm() -> dict[str, Any]:
        prompt = {
            "role": "system",
            "content": (
                "You are the Secretary investigator. Return JSON only. You may propose exactly one of "
                "hypothesis, question, proposal. Never claim an observation, acknowledgement, action execution, "
                "verification, or closure. Base the proposal only on the supplied ledger events and graph context."
            ),
        }
        body = json.dumps({
            "model": os.environ.get("SECRETARY_MODEL", "gpt-5.6-luna"),
            "messages": [prompt, {"role": "user", "content": json.dumps(request, ensure_ascii=False)}],
            "response_format": {"type": "json_object"},
        }).encode()
        headers = {"Content-Type": "application/json"}
        if os.environ.get("LITELLM_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['LITELLM_API_KEY']}"
        req = urllib.request.Request(f"{os.environ['LITELLM_URL'].rstrip('/')}/chat/completions", data=body, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read())
        proposal = json.loads(result["choices"][0]["message"]["content"])
        if proposal.get("kind") not in {"hypothesis", "question", "proposal"}:
            raise ValueError("model returned a forbidden event kind")
        return proposal

    proposal = await ctx.run_typed("litellm-investigation", call_litellm)
    return {"accepted_as": "model_inference", "execution_identity": identity, "proposal": proposal}


app = restate.app([incident, recipient, channel, investigator])
