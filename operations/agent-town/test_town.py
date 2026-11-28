import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import town  # noqa: E402

CONTACTS = Path(__file__).parent / "contacts"


@pytest.fixture(scope="module")
def contacts():
    return town.load_contacts(CONTACTS)


def discussion(category="General", assoc="OWNER", body="hello", sender_type="User"):
    return {"action": "created", "sender": {"login": "neo", "type": sender_type},
            "repository": {"full_name": "re8ch/service-backend"},
            "discussion": {"node_id": "D_1", "number": 7, "title": "t", "body": body,
                           "author_association": assoc, "category": {"name": category}}}


def test_registry_has_ten_valid_contacts(contacts):
    assert len(contacts) == 10
    for uid, card in contacts.items():
        assert card["email"] == f"{uid}@town.re8ch.com"
        assert card["subscribe"], uid
        assert card["handoff"] <= set(contacts) - {uid}, uid


def test_discussion_routes_mayor_and_category_filter(contacts):
    runs, _ = town.route("discussion", discussion(), contacts)
    assert [r["agent"] for r in runs] == ["mayor"]
    runs, _ = town.route("discussion", discussion("Ideas"), contacts)
    assert sorted(r["agent"] for r in runs) == ["mayor", "researcher"]
    assert runs[0]["thread"] == {"kind": "discussion", "node_id": "D_1", "number": 7}


@pytest.mark.parametrize("kwargs,reason", [
    ({"assoc": "NONE"}, "not trusted"),
    ({"sender_type": "Bot"}, "bot"),
    ({"body": "x <!-- town:msg=<a@b> agent=mayor -->"}, "town agent"),
])
def test_untrusted_or_machine_events_are_dropped(contacts, kwargs, reason):
    runs, why = town.route("discussion", discussion(**kwargs), contacts)
    assert runs == [] and reason in why


def test_mention_routes_only_mention_subscribers(contacts):
    payload = {"action": "created", "sender": {"login": "neo", "type": "User"},
               "repository": {"full_name": "r/r"},
               "issue": {"number": 3, "title": "i"},
               "comment": {"node_id": "C_1", "user": {"login": "neo"}, "author_association": "MEMBER",
                           "body": "town:treasurer what does this cost? also town:herald"}}
    runs, _ = town.route("issue_comment", payload, contacts)
    assert [r["agent"] for r in runs] == ["treasurer"]  # herald has no mention subscription


def test_label_filter_uses_only_added_label(contacts):
    payload = {"action": "labeled", "sender": {"login": "neo", "type": "User"},
               "repository": {"full_name": "r/r"}, "label": {"name": "bug"},
               "issue": {"number": 4, "title": "x", "author_association": "OWNER",
                         "labels": [{"name": "town/triage"}, {"name": "bug"}]}}
    assert town.route("issues", payload, contacts)[0] == []
    payload["label"] = {"name": "town/triage"}
    assert [r["agent"] for r in town.route("issues", payload, contacts)[0]] == ["triager"]


def test_message_id_is_stable_for_redelivery(contacts):
    a = town.route("discussion", discussion(), contacts)[0][0]["message_id"]
    b = town.route("discussion", discussion(), contacts)[0][0]["message_id"]
    assert a == b and a.endswith("@town.re8ch.com>")


def handoff(hop, frm="mayor", to="researcher"):
    msg = {"from": frm, "to": to, "hop": hop, "note": "n", "thread": None, "message_id": "<m@town.re8ch.com>"}
    return {"inputs": {"message": json.dumps(msg)}}


def test_handoff_hop_limit_and_allowlist(contacts):
    assert [r["agent"] for r in town.route("workflow_dispatch", handoff(1), contacts)[0]] == ["researcher"]
    runs, why = town.route("workflow_dispatch", handoff(town.MAX_HOP), contacts)
    assert runs == [] and "hop" in why
    runs, why = town.route("workflow_dispatch", handoff(1, frm="herald"), contacts)
    assert runs == [] and "may not hand off" in why


def test_plan_actions_bounds_agent_output(contacts):
    run = {"agent": "mayor", "hop": 0, "message_id": "<x@town.re8ch.com>",
           "thread": {"kind": "issue", "number": 1}}
    raw = 'noise {"reply": "hi", "handoff": [{"to": "researcher", "note": "go"},' \
          '{"to": "mayor"}, {"to": "nobody"}, {"to": "triager"}]} trailing'
    actions = town.plan_actions(contacts["mayor"], run, raw, contacts)
    assert actions[0]["type"] == "comment" and "town:msg=<x@town.re8ch.com> agent=mayor" in actions[0]["body"]
    targets = [a["client_payload"]["to"] for a in actions[1:]]
    assert targets == ["researcher"]  # self, unknown dropped; list truncated to MAX_HANDOFFS first
    assert actions[1]["client_payload"]["hop"] == 1
    assert town.plan_actions(contacts["mayor"], run, '{"reply": null}', contacts) == []
    with pytest.raises(ValueError):
        town.plan_actions(contacts["mayor"], run, "no json", contacts)


def test_handoff_stops_before_max_hop(contacts):
    run = {"agent": "mayor", "hop": town.MAX_HOP - 1, "message_id": "<x@t>", "thread": None}
    raw = '{"reply": null, "handoff": [{"to": "researcher"}]}'
    assert town.plan_actions(contacts["mayor"], run, raw, contacts) == []


def test_kill_switch(tmp_path, monkeypatch, contacts):
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps(discussion()))
    monkeypatch.setenv("TOWN_ENABLED", "false")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    town.main(["route", "--event", "discussion", "--payload", str(payload),
               "--contacts", str(CONTACTS), "--out", str(tmp_path / "plan")])
    assert not list((tmp_path / "plan").glob("*.json"))
    monkeypatch.setenv("TOWN_ENABLED", "true")
    town.main(["route", "--event", "discussion", "--payload", str(payload),
               "--contacts", str(CONTACTS), "--out", str(tmp_path / "plan")])
    assert (tmp_path / "plan" / "mayor.json").exists()
    assert "Untrusted event content" in (tmp_path / "plan" / "mayor.prompt.txt").read_text()
