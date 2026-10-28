"""Single-consumer Stalwart JMAP -> GitHub triage intake. No execution authority."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.parse
import urllib.request

CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("redirect refused; configure the canonical HTTPS URL")


def request(url, auth, body=None):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("HTTPS required")
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": auth, "Content-Type": "application/json",
                                          "Accept": "application/json", "User-Agent": "re8ch-mail-intake"})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
        return json.load(response)


class Jmap:
    def __init__(self, config, password):
        self.auth = "Basic " + base64.b64encode((config["recipient"] + ":" + password).encode()).decode()
        session = request(config["session_url"], self.auth)
        self.url = urllib.parse.urljoin(config["session_url"], session["apiUrl"])
        expected = urllib.parse.urlsplit(config["session_url"])
        actual = urllib.parse.urlsplit(self.url)
        if actual.scheme != "https" or actual.netloc != expected.netloc:
            raise ValueError("JMAP API must have the configured session origin")
        self.account = session["primaryAccounts"][MAIL]

    def call(self, method, **args):
        result = request(self.url, self.auth, {"using": [CORE, MAIL], "methodCalls": [
            [method, {"accountId": self.account, **args}, "call"]]})
        responses = result.get("methodResponses", [])
        if len(responses) != 1 or responses[0][0] != method or responses[0][2] != "call":
            raise RuntimeError("JMAP method failed")
        return responses[0][1]

    def messages(self, since):
        inboxes = [m["id"] for m in self.call("Mailbox/get", properties=["id", "role"])["list"]
                   if m.get("role") == "inbox"]
        if len(inboxes) != 1:
            raise RuntimeError("exactly one inbox required")
        position = 0
        query_state = None
        while True:
            page = self.call("Email/query", filter={"inMailbox": inboxes[0], "after": since},
                             sort=[{"property": "receivedAt", "isAscending": True}],
                             position=position, limit=50)
            if query_state is not None and page["queryState"] != query_state:
                raise RuntimeError("mailbox changed during scan; restart scan")
            query_state = page["queryState"]
            ids = page["ids"]
            if not ids:
                return
            result = self.call("Email/get", ids=ids, properties=["id", "from", "to", "subject",
                "receivedAt", "textBody", "bodyValues"], fetchTextBodyValues=True, maxBodyValueBytes=24000)
            if result.get("notFound"):
                raise RuntimeError("message disappeared during scan; restart scan")
            yield from result["list"]
            position += len(ids)


def proposal(config, account, message):
    senders = message.get("from") or []
    recipients = message.get("to") or []
    if len(senders) != 1 or senders[0].get("email", "").casefold() != config["sender"].casefold():
        return None
    if config["recipient"].casefold() not in {r.get("email", "").casefold() for r in recipients}:
        return None
    # Server-assigned identity, never attacker-controlled Message-ID or subject.
    key = hashlib.sha256(json.dumps([config["session_url"], account, message["id"]]).encode()).hexdigest()
    text = []
    for part in message.get("textBody", []):
        value = message.get("bodyValues", {}).get(part["partId"], {})
        if part.get("type") != "text/plain" or value.get("isTruncated") or value.get("isEncodingProblem"):
            raise ValueError("complete plain-text mail required")
        text.append(value.get("value", ""))
    body = "\n".join(text).strip()
    if not body or len(body.encode()) > 24000:
        raise ValueError("empty or oversized mail")
    # Quoting prevents email text from injecting machine-readable command blocks.
    quoted = "\n".join("> " + line.replace("@", "@\u200b").replace("<!--", "&lt;!--") for line in body.splitlines())
    marker = "<!-- stalwart-intake:" + key + " -->"
    title = " ".join((message.get("subject") or "Untitled").split()).replace("@", "@\u200b")[:180]
    return {"key": key, "marker": marker, "title": "[Mail] " + title,
            "body": marker + "\n\n## Summary\nEmail request awaiting GitHub triage.\n\n"
            "## Authority\nThe sender address is a routing filter, not proof of identity. "
            "This message does not authorize execution. Review on GitHub before dispatch.\n\n"
            "## Request (untrusted email)\n" + quoted + "\n\n## Acceptance criteria\n"
            "To be confirmed during triage.\n\n## Audit\nTask ID: mail_" + key + "\n"}


class Github:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid repository")
        self.base = "https://api.github.com/repos/" + repository
        self.auth = "Bearer " + token
        if request(self.base, self.auth).get("private") is not True:
            raise ValueError("mail intake requires a private repository")

    def reconcile(self, item):
        # Reconcile an ambiguous POST before retrying; do not use lagging search indexes.
        page = 1
        while True:
            issues = request(self.base + f"/issues?state=all&per_page=100&page={page}", self.auth)
            for issue in issues:
                if not issue.get("pull_request") and (issue.get("body") or "").startswith(item["marker"] + "\n"):
                    return issue["html_url"]
            if len(issues) < 100:
                break
            page += 1
        return request(self.base + "/issues", self.auth,
                       {"title": item["title"], "body": item["body"]})["html_url"]


def run(config, mail, github, db):
    db.execute("CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY, url TEXT NOT NULL)")
    db.commit()
    results = []
    for message in mail.messages(config["since"]):
        try:
            item = proposal(config, mail.account, message)
        except ValueError:
            results.append({"status": "rejected", "reason": "unsupported-message"})
            continue
        if item is None:
            continue
        # A persistent shared SQLite database serializes consumers. A remote failure
        # rolls back; next scan reconciles GitHub before attempting another POST.
        db.execute("BEGIN IMMEDIATE")
        try:
            existing = db.execute("SELECT url FROM deliveries WHERE key=?", (item["key"],)).fetchone()
            if not existing:
                url = github.reconcile(item)
                db.execute("INSERT INTO deliveries VALUES (?, ?)", (item["key"], url))
            db.commit()
        except Exception:
            db.rollback()
            raise
        results.append({"task_id": "mail_" + item["key"], "status": "existing" if existing else "triage"})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True, help="persistent local SQLite path shared by consumers")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    password = Path(os.environ["STALWART_PASSWORD_FILE"]).read_text().strip()
    token = Path(os.environ["GITHUB_TOKEN_FILE"]).read_text().strip()
    os.umask(0o077)
    with sqlite3.connect(args.state, timeout=60) as db:
        print(json.dumps(run(config, Jmap(config, password), Github(config["repository"], token), db)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # No provider response, email content, addresses, or credentials in logs.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1)
