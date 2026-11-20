import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "agent_presence", Path(__file__).parents[1] / "operations/agent-presence/presence.py")
presence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(presence)

NOW = dt.datetime(2026, 11, 20, 2, 0, tzinfo=dt.timezone.utc)


def claim(agent, *resources, mode="exclusive", ttl=1800, now=NOW):
    return presence.new_claim(agent, "test", list(resources), "intent", ttl, mode, now)


@pytest.mark.parametrize("a,b,expected", [
    ("repo:re8ch/service-backend", "path:re8ch/service-backend:clusters/x.yaml", True),
    ("repo:re8ch/service-backend", "gh:re8ch/service-backend#12", True),
    ("repo:re8ch/service-backend", "repo:8iops/agent-gitops", False),
    ("gh:re8ch/service-backend#12", "gh:RE8CH/service-backend#12", True),
    ("gh:re8ch/service-backend#12", "gh:re8ch/service-backend#13", False),
    ("path:o/r:clusters/re8ch/**", "path:o/r:clusters/re8ch/apps/a.yaml", True),
    ("path:o/r:clusters/re8ch", "path:o/r:clusters/re8ch/apps/a.yaml", True),
    ("path:o/r:clusters/re8ch-old/a", "path:o/r:clusters/re8ch/a", False),
    ("path:o/r:docs/*.md", "path:o/r:clusters/a.yaml", False),
    ("k8s:re8ch/agent-system", "k8s:re8ch/agent-system/deployment/runner", True),
    ("k8s:re8ch/agent-system/deployment/a", "k8s:re8ch/agent-system/deployment/b", False),
    ("branch:o/r@feat/x", "branch:o/r@feat/x", True),
    ("branch:o/r@feat/x", "path:o/r:a", False),
    ("dns:re8ch.com", "dns:re8ch.com", True),
])
def test_keys_overlap(a, b, expected):
    assert presence.keys_overlap(a, b) is expected
    assert presence.keys_overlap(b, a) is expected


def test_invalid_keys_rejected():
    for key in ("nokind", "gh:o/r", "branch:o/r", "path:o/r"):
        with pytest.raises(ValueError):
            presence.parse_key(key)


def test_ttl_bounds():
    assert presence.parse_ttl("30m") == 1800
    assert presence.parse_ttl("2h") == 7200
    for bad in ("10s", "9h", "soon"):
        with pytest.raises(ValueError):
            presence.parse_ttl(bad)


def test_body_round_trip():
    original = claim("claude-cloud/s1", "gh:o/r#1", "path:o/r:a/**")
    assert presence.parse_body(presence.render_body(original)) == original
    assert presence.parse_body("no block") is None


def test_lowest_number_wins_and_rules():
    a = claim("codex/t1", "path:o/r:apps/**")
    b = claim("claude-cloud/s1", "path:o/r:apps/web.yaml")
    board = [{"number": 7, "claim": a}, {"number": 9, "claim": b}]
    assert [x["number"] for x in presence.blockers(9, b, board, NOW)] == [7]
    assert presence.blockers(7, a, board, NOW) == []
    # Unfiled probe is blocked by every active overlapping claim.
    assert len(presence.blockers(None, claim("x", "repo:o/r"), board, NOW)) == 2
    # Expired, shared and same-agent claims never block.
    stale = claim("codex/t1", "path:o/r:apps/**", now=NOW - dt.timedelta(hours=1))
    assert presence.blockers(9, b, [{"number": 7, "claim": stale}], NOW) == []
    shared = claim("codex/t1", "path:o/r:apps/**", mode="shared")
    assert presence.blockers(9, b, [{"number": 7, "claim": shared}], NOW) == []
    same = claim("claude-cloud/s1", "path:o/r:apps/**")
    assert presence.blockers(9, b, [{"number": 7, "claim": same}], NOW) == []


class FakeGitHub(presence.GitHub):
    """Minimal in-memory Issues API, enough for claim/heartbeat/release."""

    def __init__(self):
        self.issues, self.comments, self.labels, self.next = {}, {}, {}, 1

    def call(self, method, path, payload=None):
        path = path.split("?")[0]
        if method == "GET" and path.endswith("/issues"):
            return [i for i in self.issues.values()
                    if i["state"] == "open" and presence.CLAIM_LABEL in i["labels"]]
        if method == "POST" and path.endswith("/issues"):
            number, self.next = self.next, self.next + 1
            self.issues[number] = {"number": number, "state": "open", "body": payload["body"],
                                   "labels": payload["labels"], "html_url": f"https://x/{number}"}
            return self.issues[number]
        m = re.search(r"/repos/(.+?)/issues/(\d+)(/\w+)?", path)
        if m and m.group(1) == presence.BOARD_REPO and int(m.group(2)) in self.issues:
            issue = self.issues[int(m.group(2))]
            if method == "GET" and not m.group(3):
                return issue
            if method == "PATCH":
                issue.update(payload)
                return issue
        if path.endswith("/comments"):
            bucket = self.comments.setdefault(path, [])
            if method == "POST":
                bucket.append({"id": len(bucket) + 1, "body": payload["body"],
                               "created_at": "9999-01-01T00:00:00Z", "user": {"login": "bot"}})
            return bucket
        if "/labels" in path:
            self.labels[path.split("/labels")[0]] = method
            return []
        return None


def run(gh, monkeypatch, capsys, argv):
    monkeypatch.setattr(presence.time, "sleep", lambda _: None)
    args = presence.build_parser().parse_args(argv)
    code = args.func(gh, args)
    return code, json.loads(capsys.readouterr().out)


def test_claim_conflict_heartbeat_release(monkeypatch, capsys):
    gh = FakeGitHub()
    code, out = run(gh, monkeypatch, capsys, ["claim", "-r", "gh:o/r#5", "-i", "fix",
                                               "--agent", "claude-cloud/s1"])
    assert code == 0 and out["status"] == "granted"
    assert gh.labels["/repos/o/r/issues/5"] == "POST"
    assert "claude-cloud/s1" in gh.comments["/repos/o/r/issues/5/comments"][0]["body"]

    code, out = run(gh, monkeypatch, capsys, ["claim", "-r", "repo:o/r", "-i", "refactor",
                                               "--agent", "codex/t9"])
    assert code == presence.EXIT_CONFLICT and out["blockers"][0]["number"] == 1

    code, out = run(gh, monkeypatch, capsys, ["say", "1", "please pause, I need #5",
                                               "--agent", "codex/t9"])
    assert code == 0
    code, out = run(gh, monkeypatch, capsys, ["heartbeat", "1"])
    assert code == 0 and out["status"] == "renewed"
    assert [m["body"] for m in out["messages"]] == ["**codex/t9**: please pause, I need #5"]

    code, out = run(gh, monkeypatch, capsys, ["release", "1", "--note", "PR #9 opened"])
    assert code == 0 and gh.issues[1]["state"] == "closed"
    assert gh.labels["/repos/o/r/issues/5"] == "DELETE"

    code, out = run(gh, monkeypatch, capsys, ["heartbeat", "1"])
    assert code == presence.EXIT_REVOKED


def test_race_loser_yields(monkeypatch, capsys):
    gh = FakeGitHub()
    rival = claim("codex/t1", "path:o/r:apps/**", now=presence.utcnow())
    original_board = gh.board

    gh.issues[1] = {"number": 1, "state": "open", "labels": [presence.CLAIM_LABEL],
                    "body": presence.render_body(rival), "html_url": "https://x/1"}
    gh.next = 2
    calls = {"n": 0}

    def board(repo):
        calls["n"] += 1
        items = original_board(repo)
        return [i for i in items if i["number"] != 1] if calls["n"] == 1 else items

    monkeypatch.setattr(gh, "board", board)
    code, out = run(gh, monkeypatch, capsys, ["claim", "-r", "path:o/r:apps/web.yaml",
                                               "-i", "edit", "--agent", "claude-cloud/s1"])
    assert code == presence.EXIT_CONFLICT and out["yielded"] == 2
    assert gh.issues[2]["state"] == "closed" and gh.issues[2]["state_reason"] == "not_planned"


def test_claim_matches_published_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).parents[1]
                         / "contracts/agent-presence/claim.schema.json").read_text())
    jsonschema.validate(claim("claude-cloud/s1", "gh:o/r#1", "k8s:re8ch/ns"), schema)
