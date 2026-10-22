import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/github-maintenance/bot_merge.py"
SPEC = importlib.util.spec_from_file_location("bot_merge", SCRIPT)
assert SPEC and SPEC.loader
BOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOT)
SHA = "a" * 40


class FakeGitHub:
    def __init__(self, *, sha=SHA, verified=True, author="Woomou", review="APPROVED"):
        self.sha = sha
        self.verified = verified
        self.author = author
        self.review = review
        self.merge_payload = None

    def request(self, path, *, method="GET", data=None):
        if path.endswith("/pulls/42"):
            return {
                "state": "open", "draft": False, "title": "Safe change",
                "base": {"ref": "main"},
                "head": {"sha": self.sha, "repo": {"full_name": "re8ch/service-backend"}},
                "user": {"login": "Woomou"},
            }
        if path.endswith("/merge"):
            self.merge_payload = data
            return {"merged": True}
        raise AssertionError(path)

    def pages(self, path):
        if path.endswith("/commits"):
            return [{"commit": {"verification": {"verified": self.verified}}, "author": {"login": self.author}}]
        if path.endswith("/reviews"):
            return [{"user": {"login": "reviewer"}, "state": self.review}]
        raise AssertionError(path)


def test_validates_singleton_human_dispatch(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "re8ch/service-backend")
    monkeypatch.setenv("GITHUB_ACTOR", "Woomou")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    assert BOT.validate_invocation() == "re8ch/service-backend"
    monkeypatch.setenv("GITHUB_ACTOR", "someone-else")
    with pytest.raises(ValueError, match="dispatched by Woomou"):
        BOT.validate_invocation()


def test_merges_only_the_exact_verified_head():
    api = FakeGitHub()
    BOT.merge(api, "re8ch/service-backend", 42, SHA)
    assert api.merge_payload == {
        "sha": SHA,
        "merge_method": "squash",
        "commit_title": "Safe change (#42)",
    }


@pytest.mark.parametrize(
    ("api", "expected_sha", "message"),
    [
        (FakeGitHub(sha="b" * 40), SHA, "head changed"),
        (FakeGitHub(verified=False), SHA, "verified"),
        (FakeGitHub(author="someone-else"), SHA, "attributed to Woomou"),
        (FakeGitHub(review="CHANGES_REQUESTED"), SHA, "change request"),
    ],
)
def test_refuses_stale_unsigned_or_rejected_prs(api, expected_sha, message):
    with pytest.raises(ValueError, match=message):
        BOT.merge(api, "re8ch/service-backend", 42, expected_sha)
    assert api.merge_payload is None


def test_workflow_uses_app_identity_without_repository_write_token():
    text = (ROOT / ".github/workflows/github-bot-merge.yml").read_text()
    assert "GITOPS_MAINTENANCE_APP_ID" in text
    assert "GITOPS_MAINTENANCE_PRIVATE_KEY" in text
    assert "permission-contents: write" in text
    assert "permission-pull-requests: write" in text
    assert "permissions: {contents: read}" in text
    assert "workflow_dispatch:" in text
    assert "pull_request_target" not in text
