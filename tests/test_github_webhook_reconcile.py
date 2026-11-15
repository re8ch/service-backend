import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "github_webhook_reconcile", ROOT / "operations/github-webhooks/reconcile.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
desired = MODULE.desired
plan_hook = MODULE.plan_hook


DECLARATION = {
    "spec": {
        "organization": {
            "login": "re8ch",
            "active": True,
            "events": ["push", "issues", "push"],
            "urlEnvironment": "URL",
            "secretEnvironment": "SECRET",
        }
    }
}


def test_unset_url_blocks_without_guessing_an_endpoint():
    target = desired(DECLARATION, {})
    assert plan_hook(target, []) == {"action": "blocked", "reason": "webhook URL environment is unset"}


def test_matching_hook_is_idempotent():
    target = desired(DECLARATION, {"URL": "https://agent.example/hook"})
    plan = plan_hook(target, [{
        "id": 42,
        "active": True,
        "events": ["issues", "push"],
        "config": {"url": "https://agent.example/hook"},
    }])
    assert plan == {"action": "none", "hookId": 42, "changes": {}}


def test_existing_hook_updates_events_without_replacing_secret_config():
    target = desired(DECLARATION, {"URL": "https://agent.example/hook"})
    plan = plan_hook(target, [{
        "id": 42,
        "active": False,
        "events": ["push"],
        "config": {"url": "https://agent.example/hook", "secret": "********"},
    }])
    assert plan == {
        "action": "update",
        "hookId": 42,
        "changes": {"events": ["issues", "push"], "active": True},
    }
