"""Fail-closed tests for the public-organization App reviewer."""

import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "operations/github-policy-reviewer/review.py"
SPEC = importlib.util.spec_from_file_location("github_policy_reviewer", PATH)
reviewer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reviewer)


class FakeAPI:
    def __init__(self):
        self.approvals = []
        self.checks_posted = []
        self.pr = {
            "number": 25,
            "draft": False,
            "state": "open",
            "created_at": "2026-09-22T00:00:00Z",
            "base": {"ref": "main"},
            "head": {"sha": "abc", "repo": {"full_name": "public-edge/public-edge-manager"}},
            "user": {"login": "Woomou"},
        }
        self.commit = {
            "author": {"login": "Woomou"},
            "commit": {"verification": {"verified": True}},
        }
        self.checks = {"total_count": 1, "check_runs": [
            {"name": "test", "app": {"id": 15368}, "status": "completed", "conclusion": "success"}
        ]}
        self.status = {"state": "pending", "statuses": []}
        self.reviews = []

    def pages(self, path):
        if path == "/installation/repositories":
            return [{"full_name": "public-edge/public-edge-manager", "owner": {"login": "public-edge"},
                     "default_branch": "main", "archived": False, "disabled": False}]
        if path.endswith("/commits"):
            return [self.commit]
        if path.endswith("/reviews"):
            return self.reviews
        if "pulls?state=open" in path:
            return [self.pr]
        raise AssertionError(path)

    def get(self, path):
        if path.endswith("/check-runs?filter=latest&per_page=100"):
            return self.checks
        if path.endswith("/status"):
            return self.status
        if path.endswith("/pulls/25"):
            return self.pr
        raise AssertionError(path)

    def request(self, path, *, method="GET", data=None):
        assert method == "POST"
        if path.endswith("/check-runs"):
            self.checks_posted.append(data)
            return {}, {}
        assert data["event"] == "APPROVE"
        self.approvals.append(path)
        return {}, {}


def test_approves_only_eligible_pr():
    api = FakeAPI()
    assert reviewer.run(api, "public-edge") == 1
    assert api.approvals == ["/repos/public-edge/public-edge-manager/pulls/25/reviews"]
    assert api.checks_posted[0]["conclusion"] == "success"


def test_dry_run_never_approves():
    api = FakeAPI()
    assert reviewer.run(api, "public-edge", dry_run=True) == 1
    assert api.approvals == []
    assert api.checks_posted == []


def test_bifurcate_organization_uses_same_fail_closed_policy():
    api = FakeAPI()
    api.pr["head"]["repo"]["full_name"] = "bifurcate-systems/bifurcate"
    original_pages = api.pages

    def bifurcate_pages(path):
        if path == "/installation/repositories":
            return [{"full_name": "bifurcate-systems/bifurcate",
                     "owner": {"login": "bifurcate-systems"},
                     "default_branch": "main", "archived": False, "disabled": False}]
        return original_pages(path)

    api.pages = bifurcate_pages
    assert reviewer.run(api, "bifurcate-systems") == 1
    assert api.checks_posted[0]["conclusion"] == "success"


def test_untrusted_author_is_not_approved():
    api = FakeAPI()
    api.pr["user"]["login"] = "outsider"
    assert reviewer.run(api, "public-edge") == 0
    assert api.approvals == []
    assert api.checks_posted[0]["conclusion"] == "failure"


def test_preexisting_pr_is_not_approved():
    api = FakeAPI()
    api.pr["created_at"] = "2026-09-20T00:00:00Z"
    assert reviewer.run(api, "public-edge") == 0
    assert api.checks_posted == []


def test_unsigned_commit_is_not_approved():
    api = FakeAPI()
    api.commit["commit"]["verification"]["verified"] = False
    assert reviewer.run(api, "public-edge") == 0


def test_pending_check_is_not_approved():
    api = FakeAPI()
    api.checks["check_runs"][0]["status"] = "in_progress"
    api.checks["check_runs"][0]["conclusion"] = None
    assert reviewer.run(api, "public-edge") == 0
    assert api.checks_posted[0]["status"] == "in_progress"


def test_change_request_is_not_approved():
    api = FakeAPI()
    api.reviews = [{"user": {"login": "maintainer"}, "state": "CHANGES_REQUESTED"}]
    assert reviewer.run(api, "public-edge") == 0


def test_changed_head_is_not_approved():
    api = FakeAPI()
    original = api.get

    def changed(path):
        if path.endswith("/pulls/25"):
            return {**api.pr, "head": {**api.pr["head"], "sha": "new"}}
        return original(path)

    api.get = changed
    assert reviewer.run(api, "public-edge") == 0
    assert api.checks_posted == []


def test_prior_policy_failure_does_not_poison_future_pass():
    api = FakeAPI()
    api.checks["total_count"] = 2
    api.checks["check_runs"].append({
        "name": reviewer.CHECK_NAME, "app": {"id": reviewer.APP_ID},
        "status": "completed", "conclusion": "failure",
    })
    assert reviewer.run(api, "public-edge") == 1
    assert api.checks_posted[0]["conclusion"] == "success"


def test_wrong_organization_fails_closed():
    api = FakeAPI()
    try:
        reviewer.run(api, "re8ch")
    except ValueError:
        pass
    else:
        raise AssertionError("unexpected organization accepted")
