# GitOps drift audit

The audit separates namespace configuration authority before reporting drift:

- `gitops`: full Git/Flux policy and reconciliation checks;
- `tenant-controller`: tenant boundary invariants only; tenant workloads are not
  compared with Git;
- `runtime-controller`: controller ownership and health review;
- `unclassified`: ownership must be assigned before drift can be interpreted.

`audit.py` is read-only. It reads Namespace metadata, the named tenant boundary
resources, and Flux Kustomization status. It never reads Secrets or mutates the
cluster. `run-audit.sh` atomically writes the latest report to
`~/Library/Application Support/RE8CH/gitops-drift/latest.json`.

On the Macmini, install the checked-in LaunchAgents after the corresponding
commit is present in the canonical anchor:

```sh
operations/gitops-drift/macos/install-launchagents.sh
```

The installer copies executable entry points to Application Support because
macOS launchd does not execute scripts directly from the external A1 volume.

The anchor sync runs every five minutes and refuses a dirty, divergent, or
wrong-branch checkout. The drift audit runs hourly; tenant boundary checks are
included while tenant-owned workload content is excluded.
