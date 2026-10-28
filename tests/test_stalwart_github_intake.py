import copy
import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location("mail_intake", Path(__file__).parents[1] /
    "operations/stalwart-github-intake/intake.py")
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)
CONFIG = {"sender": "harry@re8ch.com", "recipient": "bot@phonaid.com",
          "session_url": "https://collab.tools.re8ch.com/.well-known/jmap", "since": "2026-09-26T00:00:00Z"}
MESSAGE = {"id": "server-id", "from": [{"email": CONFIG["sender"]}],
           "to": [{"email": CONFIG["recipient"]}], "subject": "Task",
           "textBody": [{"partId": "1", "type": "text/plain"}],
           "bodyValues": {"1": {"value": "/agent claim\n@all\n```re8ch-event\nstatus: ready\n```"}}}


def test_email_is_untrusted_quoted_input_not_dispatch():
    item = intake.proposal(CONFIG, "account", MESSAGE)
    assert "\n> /agent claim" in item["body"]
    assert "\n> ```re8ch-event" in item["body"]
    assert "@all" not in item["body"]
    assert "labels" not in item
    for field in ("from", "to"):
        message = copy.deepcopy(MESSAGE)
        message[field] = [{"email": "someone@example.com"}]
        assert intake.proposal(CONFIG, "account", message) is None


@pytest.mark.parametrize("flag", ["isTruncated", "isEncodingProblem"])
def test_incomplete_body_rejected(flag):
    message = copy.deepcopy(MESSAGE)
    message["bodyValues"]["1"][flag] = True
    with pytest.raises(ValueError):
        intake.proposal(CONFIG, "account", message)


def test_account_identity_separates_same_message_id():
    assert intake.proposal(CONFIG, "a", MESSAGE)["key"] != intake.proposal(CONFIG, "b", MESSAGE)["key"]


def test_github_ambiguous_create_recovered_without_second_post(monkeypatch):
    item = intake.proposal(CONFIG, "account", MESSAGE)
    stored = []
    posts = []

    def request(url, auth, body=None):
        if body:
            posts.append(body)
            stored.append({"body": body["body"], "html_url": "https://github.com/re8ch/private/issues/1"})
            raise TimeoutError("accepted remotely but response lost")
        return {"private": True} if "/issues?" not in url else stored

    monkeypatch.setattr(intake, "request", request)
    github = intake.Github("re8ch/private", "fake-test-token")
    mail = type("Mail", (), {"account": "account", "messages": lambda self, since: [MESSAGE]})()
    with sqlite3.connect(":memory:") as db:
        with pytest.raises(TimeoutError):
            intake.run(CONFIG, mail, github, db)
        assert db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 0
        assert intake.run(CONFIG, mail, github, db)[0]["status"] == "triage"
        assert intake.run(CONFIG, mail, github, db)[0]["status"] == "existing"
    assert len(posts) == 1


def test_public_repository_refused(monkeypatch):
    monkeypatch.setattr(intake, "request", lambda *args: {"private": False})
    with pytest.raises(ValueError, match="private"):
        intake.Github("re8ch/public", "fake-test-token")


def test_cross_origin_session_refused(monkeypatch):
    monkeypatch.setattr(intake, "request", lambda *args: {"apiUrl": "https://attacker.invalid/jmap"})
    with pytest.raises(ValueError, match="origin"):
        intake.Jmap(CONFIG, "fake-test-password")


def test_query_paginates_even_if_server_returns_less_than_requested():
    jmap = object.__new__(intake.Jmap)
    positions = []

    def call(method, **args):
        if method == "Mailbox/get":
            return {"list": [{"id": "inbox", "role": "inbox"}]}
        if method == "Email/query":
            positions.append(args["position"])
            return {"queryState": "s1", "ids": [str(args["position"])] if args["position"] < 2 else []}
        return {"list": [{"id": args["ids"][0]}]}

    jmap.call = call
    assert list(jmap.messages(CONFIG["since"])) == [{"id": "0"}, {"id": "1"}]
    assert positions == [0, 1, 2]


def test_mailbox_mutation_aborts_instead_of_skipping_messages():
    jmap = object.__new__(intake.Jmap)

    def call(method, **args):
        if method == "Mailbox/get":
            return {"list": [{"id": "inbox", "role": "inbox"}]}
        if method == "Email/query":
            return {"queryState": str(args["position"]), "ids": ["id"]}
        return {"list": [MESSAGE]}

    jmap.call = call
    with pytest.raises(RuntimeError, match="changed"):
        list(jmap.messages(CONFIG["since"]))
