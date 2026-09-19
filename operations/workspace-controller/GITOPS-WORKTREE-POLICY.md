# GitOps worktree and main-anchor policy

This policy applies before using `git`, `gh`, `flux`, `kubectl`, `helm`, or the Cloud Platform tool for a desired-state change.

## Invariants

1. GitHub `main` is the sole GitOps authority. GitLab is a read-only historical archive.
2. `/Volumes/A1/service-backend` is the canonical local main anchor. It must remain clean, track `github/main`, and have no user-writable files or directories outside `.git`.
3. Repository writes happen only in a task-specific worktree created from current `github/main`.
4. Each branch carries one reviewable concern with signed commits, successful GitHub Actions, and a pull request.
5. Dirty or divergent worktrees are evidence. Preserve them before cleanup; never discard them to make the checkout look clean.

## Before a GitOps change

From a clean task worktree or another non-anchor checkout, fetch the authority and create a new worktree:

```bash
git fetch github main
git worktree add -b <type>/<task-name> <task-worktree-path> github/main
```

Confirm the new checkout is not `/Volumes/A1/service-backend`, then make and validate the change there. Do not initialize every catalog gitlink; initialize only the module source required by the task.

## Main-anchor maintenance

The anchor may be temporarily unlocked only by the dedicated local sync procedure. That procedure must refuse a dirty checkout, fetch `github/main`, perform `merge --ff-only github/main`, verify that HEAD and upstream match, and relock the filesystem on every exit path.

The local Git hooks must reject commit, merge, rebase, and push when the repository top level is `/Volumes/A1/service-backend`. Filesystem permissions are an additional guard, not a substitute for the hook or this policy.

## Cleanup

After merge or intentional PR closure, verify the task worktree is clean, remove it, prune worktree metadata, and delete its local branch. For unmerged work, first preserve a patch, stash, Git bundle, or quarantine checkout and record the recovery location.
