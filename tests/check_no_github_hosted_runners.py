#!/usr/bin/env python3
"""Fail when a repository workflow can dispatch to a GitHub-hosted runner."""

from pathlib import Path

import yaml


ROOTS = (
    Path(".github/workflows"),
    Path("operations/ci/workflows"),
    Path("operations/migrations/github-first-control-plane/landing-zone/workflows"),
)


def labels(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    raise AssertionError(f"unsupported runs-on declaration: {value!r}")


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
                if "self-hosted" not in runner_labels:
                    failures.append(
                        f"{path}:{job_name}: must target a self-hosted runner, got "
                        f"{sorted(runner_labels)!r}"
                    )
    if failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()
