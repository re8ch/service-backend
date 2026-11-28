import argparse
import copy
import unittest
from datetime import datetime, timedelta, timezone

from centurion import ContractError, new_checkpoint, seal, transition, validate


NOW = datetime(2026, 11, 28, tzinfo=timezone.utc)


def init_args(**overrides):
    values = dict(run_id="run-1", issue="3458", objective="bounded work", hours=24, max_sessions=4, max_failures=2)
    values.update(overrides)
    return argparse.Namespace(**values)


def move_args(state, owner="runner-a", **overrides):
    values = dict(state=state, owner=owner, lease_minutes=45, next_action="next increment", evidence=None, failure=None,
                  idempotency_key=f"{owner}-{state}")
    values.update(overrides)
    return argparse.Namespace(**values)


class CenturionContractTest(unittest.TestCase):
    def test_handoff_resumes_after_lease_expiry(self):
        checkpoint = new_checkpoint(init_args(), NOW)
        checkpoint = transition(checkpoint, move_args("planning"), NOW)
        resumed = transition(checkpoint, move_args("working", owner="runner-b"), NOW + timedelta(minutes=46))
        self.assertEqual(resumed["lease"]["owner"], "runner-b")

    def test_concurrent_owner_is_rejected(self):
        checkpoint = transition(new_checkpoint(init_args(), NOW), move_args("planning"), NOW)
        with self.assertRaisesRegex(ContractError, "active lease"):
            transition(checkpoint, move_args("working", owner="runner-b"), NOW + timedelta(minutes=1))

    def test_corrupted_checkpoint_is_rejected(self):
        checkpoint = new_checkpoint(init_args(), NOW)
        checkpoint["objective"] = "tampered"
        with self.assertRaisesRegex(ContractError, "integrity mismatch"):
            validate(checkpoint, NOW)

    def test_identical_transition_replay_is_idempotent(self):
        checkpoint = new_checkpoint(init_args(), NOW)
        moved = transition(checkpoint, move_args("planning"), NOW)
        replayed = transition(moved, move_args("planning"), NOW + timedelta(seconds=5))
        self.assertEqual(replayed, moved)
        self.assertEqual(replayed["budget"]["sessions_used"], 1)

    def test_idempotency_key_cannot_be_repurposed(self):
        checkpoint = transition(new_checkpoint(init_args(), NOW), move_args("planning"), NOW)
        with self.assertRaisesRegex(ContractError, "different transition"):
            transition(checkpoint, move_args("working", idempotency_key="runner-a-planning"), NOW + timedelta(minutes=46))

    def test_credential_shaped_checkpoint_is_rejected(self):
        checkpoint = new_checkpoint(init_args(), NOW)
        checkpoint["evidence"] = ["authorization bearer material"]
        checkpoint = seal(checkpoint)
        with self.assertRaisesRegex(ContractError, "credential-shaped"):
            validate(checkpoint, NOW)

    def test_wall_clock_budget_is_bounded_and_enforced(self):
        checkpoint = new_checkpoint(init_args(hours=99), NOW)
        self.assertEqual(checkpoint["deadline_at"], "2026-11-29T00:00:00Z")
        with self.assertRaisesRegex(ContractError, "wall-clock budget exhausted"):
            validate(checkpoint, NOW + timedelta(hours=24))

    def test_session_budget_stops_replay(self):
        checkpoint = new_checkpoint(init_args(max_sessions=1), NOW)
        checkpoint = transition(checkpoint, move_args("planning"), NOW)
        with self.assertRaisesRegex(ContractError, "session budget exhausted"):
            transition(checkpoint, move_args("working"), NOW + timedelta(minutes=46))

    def test_failure_budget_stops_retries(self):
        checkpoint = new_checkpoint(init_args(max_failures=1), NOW)
        checkpoint = transition(checkpoint, move_args("planning", failure="bounded failure"), NOW)
        with self.assertRaisesRegex(ContractError, "failure budget exhausted"):
            transition(checkpoint, move_args("working"), NOW + timedelta(minutes=46))

    def test_terminal_state_cannot_restart(self):
        checkpoint = new_checkpoint(init_args(), NOW)
        checkpoint = transition(checkpoint, move_args("planning"), NOW)
        checkpoint = transition(checkpoint, move_args("working"), NOW + timedelta(minutes=46))
        checkpoint = transition(checkpoint, move_args("evaluating"), NOW + timedelta(minutes=92))
        checkpoint = transition(checkpoint, move_args("completed"), NOW + timedelta(minutes=138))
        replayed = transition(checkpoint, move_args("planning"), NOW + timedelta(minutes=139))
        self.assertEqual(replayed["state"], "completed")
        with self.assertRaisesRegex(ContractError, "not allowed"):
            transition(checkpoint, move_args("planning", idempotency_key="restart"), NOW + timedelta(minutes=139))


if __name__ == "__main__":
    unittest.main()
