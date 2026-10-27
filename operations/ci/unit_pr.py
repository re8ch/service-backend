"""Consume main success evidence for impact-selected PR units; never publish it."""
import argparse
import json
import os
from pathlib import Path
import time

from . import unit_baseline, unit_catalog, unit_evidence


def consume(root, plan, output, identity, lookup=unit_evidence.latest_baseline):
    catalog = unit_catalog.load_catalog(root)
    if not plan['jobs']['unit']:
        plan = {**plan, 'tests': []}
    selected = set(plan['tests'])
    if selected and not set(catalog['always_tests']) <= selected:
        raise ValueError('impact plan omitted always-real selector tests')
    force = plan.get('full', False)
    cacheable = {test for test, unit in unit_evidence.candidates(catalog).items()
                 if not unit.get('reuse_blocked')}
    started = time.monotonic()
    if force:
        baseline, reason = None, 'forced-impact-full'
    elif not selected:
        baseline, reason = None, 'no-selected-tests'
    elif not selected & cacheable:
        baseline, reason = None, 'no-selected-reusable-units'
    else:
        baseline, reason = lookup(identity['repository'])
    elapsed = time.monotonic() - started
    return unit_baseline.run(root, plan, output, identity, baseline=baseline,
                             baseline_reason=reason, lookup_seconds=elapsed, publish=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_EVENT_NAME') not in {'pull_request', 'workflow_dispatch'}:
        parser.error('PR consumer requires pull_request or manual CI; use the main publisher for pushes')
    identity = {'repository': os.environ['GITHUB_REPOSITORY'], 'sha': os.environ['GITHUB_SHA'],
                'run_id': int(os.environ['GITHUB_RUN_ID']), 'attempt': int(os.environ['GITHUB_RUN_ATTEMPT'])}
    # GITHUB_REPOSITORY is the base repository, including for fork PRs. No head-repo token.
    raise SystemExit(consume(Path.cwd(), json.loads(os.environ['UNIT_PLAN']), args.output, identity))


if __name__ == '__main__':
    main()
