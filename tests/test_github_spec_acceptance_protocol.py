"""Local Git protocol experiments; these do NOT prove GitHub ruleset compatibility."""

from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading

import pytest


def git(repo, *args, check=True, stdin=None):
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, text=True,
        capture_output=True, check=check, timeout=15,
    )


@pytest.fixture
def graph(tmp_path):
    repo = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.name", "Protocol test")
    git(repo, "config", "user.email", "protocol@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    tree = git(repo, "mktree", stdin="").stdout.strip()

    def commit(message, *parents):
        args = ["commit-tree", tree, "-m", message]
        for parent in parents:
            args.extend(["-p", parent])
        return git(repo, *args).stdout.strip()

    base = commit("base")
    git(repo, "update-ref", "refs/heads/main", base)
    return repo, base, commit


def test_exact_old_oid_allows_only_one_concurrent_candidate(graph):
    repo, base, commit = graph
    candidates = [commit("candidate A", base), commit("candidate B", base)]
    barrier = threading.Barrier(2)

    def accept(candidate):
        barrier.wait(timeout=10)
        return git(repo, "update-ref", "refs/heads/main", candidate, base, check=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(accept, candidates))
    assert sum(result.returncode == 0 for result in outcomes) == 1
    winner = candidates[next(i for i, result in enumerate(outcomes) if result.returncode == 0)]
    assert git(repo, "rev-parse", "refs/heads/main").stdout.strip() == winner
    assert git(repo, "rev-list", "--parents", "-n", "1", winner).stdout.split() == [winner, base]


def test_stale_expected_oid_rejected_even_for_fast_forward_target(graph):
    repo, base, commit = graph
    first = commit("first", base)
    second = commit("second", first)
    git(repo, "update-ref", "refs/heads/main", first, base)
    # Fast-forward ancestry is insufficient to prove the caller observed current HEAD.
    assert git(repo, "merge-base", "--is-ancestor", first, second).returncode == 0
    result = git(repo, "update-ref", "refs/heads/main", second, base, check=False)
    assert result.returncode != 0
    assert git(repo, "rev-parse", "refs/heads/main").stdout.strip() == first


def test_compare_and_swap_does_not_enforce_candidate_parent(graph):
    repo, base, commit = graph
    unrelated = commit("unrelated root")
    # CAS alone accepts this: parent validation and no-force rules are separate gates.
    git(repo, "update-ref", "refs/heads/main", unrelated, base)
    assert git(repo, "rev-list", "--parents", "-n", "1", unrelated).stdout.split() == [unrelated]


def test_compare_and_swap_cannot_atomically_read_other_validity_ref(graph):
    repo, base, commit = graph
    candidate = commit("candidate", base)
    valid = commit("valid")
    revoked = commit("revoked", valid)
    git(repo, "update-ref", "refs/heads/validity", valid)
    assert git(repo, "rev-parse", "refs/heads/validity").stdout.strip() == valid
    git(repo, "update-ref", "refs/heads/validity", revoked, valid)
    # A separate validity check races with acceptance, even though main CAS succeeds.
    git(repo, "update-ref", "refs/heads/main", candidate, base)
    assert git(repo, "rev-parse", "refs/heads/validity").stdout.strip() == revoked


def test_git_transaction_can_verify_validity_and_update_head_together(graph):
    repo, base, commit = graph
    candidate = commit("candidate", base)
    valid = commit("valid")
    revoked = commit("revoked", valid)
    git(repo, "update-ref", "refs/heads/validity", revoked)
    transaction = (
        "start\n"
        f"verify refs/heads/validity {valid}\n"
        f"update refs/heads/main {candidate} {base}\n"
        "prepare\ncommit\n"
    )
    result = git(repo, "update-ref", "--stdin", stdin=transaction, check=False)
    assert result.returncode != 0
    assert git(repo, "rev-parse", "refs/heads/main").stdout.strip() == base


def test_receive_pack_exact_lease_rejects_second_proposal(graph, tmp_path):
    remote, base, commit = graph
    first, second = commit("proposal A", base), commit("proposal B", base)
    client = tmp_path / "client"
    subprocess.run(["git", "clone", str(remote), str(client)], check=True, capture_output=True)
    # Fetch immutable proposals without moving the target ref.
    git(client, "fetch", "origin", first, second)
    lease = f"--force-with-lease=refs/heads/main:{base}"
    accepted = git(client, "push", "origin", lease, f"{first}:refs/heads/main", check=False)
    rejected = git(client, "push", "origin", lease, f"{second}:refs/heads/main", check=False)
    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode != 0
    assert "stale info" in rejected.stderr
    assert git(remote, "rev-parse", "refs/heads/main").stdout.strip() == first


def test_receive_pack_lease_is_not_a_non_fast_forward_safety_policy(graph, tmp_path):
    remote, base, commit = graph
    unrelated = commit("unrelated proposal")
    client = tmp_path / "client"
    subprocess.run(["git", "clone", str(remote), str(client)], check=True, capture_output=True)
    git(client, "fetch", "origin", unrelated)
    lease = f"--force-with-lease=refs/heads/main:{base}"
    # The transport option carries the old OID but also permits forced updates.
    # Server policy must forbid them independently; the executor must check parentage.
    git(remote, "config", "receive.denyNonFastForwards", "true")
    rejected = git(client, "push", "origin", lease, f"{unrelated}:refs/heads/main", check=False)
    assert rejected.returncode != 0
    assert git(remote, "rev-parse", "refs/heads/main").stdout.strip() == base
