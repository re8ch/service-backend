#!/usr/bin/env python3
"""Permit reviewed cloud delivery jobs and Linux build workers."""

from pathlib import Path

import yaml


ROOTS = (
    Path(".github/workflows"),
    Path("operations/ci/workflows"),
    Path("operations/migrations/github-first-control-plane/landing-zone/workflows"),
)

CLOUD_JOBS = {
    ".github/workflows/node-contract-observer.yml": {"flux"},
    ".github/workflows/ci.yml": {
        "impact", "contract", "k6-contracts", "schema", "unit", "render",
        "secret-scan", "dependency-review", "supply-chain", "policy",
        "tenant-select", "tenant-contract", "control-plane-validate",
    },
    ".github/workflows/github-security-checks.yml": {"dependency-review"},
    ".github/workflows/tenant-go-release.yml": {"tenant-go-validate", "release", "finalize"},
    ".github/workflows/release-compat-controller.yml": {"release"},
    ".github/workflows/ceph-static-release.yml": {"infra", "runtime", "accept"},
    ".github/workflows/main-domain-release.yml": {"validate", "compile-sign", "execute"},
    ".github/workflows/main-domain-reconcile.yml": {"select", "reconcile"},
    ".github/workflows/commerce-go-release.yml": {"release"},
    ".github/workflows/auth-gateway-image-release.yml": {"release"},
    ".github/workflows/auth-gateway-release.yml": {"release"},
    ".github/workflows/conformance-result-release.yml": {"release"},
    ".github/workflows/conformance-runner-release.yml": {"release"},
    ".github/workflows/mirror-supabase-auth.yml": {"mirror"},
    ".github/workflows/stagehand-service-release.yml": {"release"},
    ".github/workflows/toolhive-cloud-ui-release.yml": {"release"},
    ".github/workflows/user-hub-release.yml": {"publish"},
    ".github/workflows/headlamp-runtime-release.yml": {"mirror"},
    ".github/workflows/tenant-provider-contracts.yml": {"tenant-ci"},
    ".github/workflows/cloud-platform-release.yml": {"release"},
    ".github/workflows/tenant-release.yml": {"release"},
    ".github/workflows/studio-toolkit-release.yml": {"publish"},
    ".github/workflows/workspace-memory-audit.yml": {"audit"},
}


def reviewed_cloud_job(path: Path, job_name: str, job: dict) -> bool:
    return (job_name in CLOUD_JOBS.get(path.as_posix(), set())
            and job.get("runs-on") == "ubuntu-24.04")


def labels(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    raise AssertionError(f"unsupported runs-on declaration: {value!r}")


def isolated_private_docs(path: Path, job_name: str, document: dict, job: dict) -> bool:
    # Private Project bodies and the App key must not share the PR build pool.
    # Keep this exception restricted to the reviewed manual, protected-main job.
    triggers = document.get('on', document.get(True))
    return (
        path == Path('.github/workflows/private-operations-docs.yml')
        and job_name == 'verify'
        and job.get('runs-on') == 'ubuntu-latest'
        and isinstance(triggers, dict) and set(triggers) == {'workflow_dispatch'}
        and document.get('permissions') == {'contents': 'read'}
        and job.get('permissions', {'contents': 'read'}) == {'contents': 'read'}
        and job.get('if') == "github.ref == 'refs/heads/main'"
        and job.get('environment') == 'gitops-merge'
    )


def main() -> None:
    failures: list[str] = []
    for root in ROOTS:
        for path in sorted((*root.glob("*.yml"), *root.glob("*.yaml"))):
            document = yaml.safe_load(path.read_text()) or {}
            for job_name, job in (document.get("jobs") or {}).items():
                if not isinstance(job, dict) or "runs-on" not in job:
                    continue
                try:
                    runner_labels = labels(job["runs-on"])
                except AssertionError as error:
                    failures.append(f"{path}:{job_name}: {error}")
                    continue
                if ("self-hosted" not in runner_labels
                        and not isolated_private_docs(path, job_name, document, job)
                        and not reviewed_cloud_job(path, job_name, job)):
                    failures.append(
                        f"{path}:{job_name}: must target a self-hosted runner, got "
                        f"{sorted(runner_labels)!r}"
                    )
    if failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()
