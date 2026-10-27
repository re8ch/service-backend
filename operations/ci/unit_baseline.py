"""Main-only unit baseline runner. PR consumers are deliberately not enabled."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import impact, unit_catalog, unit_evidence as evidence, unit_report


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')


def input_tree(root):
    try:
        return evidence.Inputs(root)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def fingerprint_for_unit(tree, test, catalog, environment):
    try:
        return tree.fingerprint(test, catalog, environment) if tree else (None, 'unreadable-input-tree')
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, 'unreadable-input'


def run(root, plan, output, publisher, *, baseline=None, baseline_reason='no-baseline', full=False, lookup_seconds=0):
    started = time.monotonic()
    root, output = Path(root), Path(output).resolve()
    force = full or plan.get('full', False)
    output.mkdir(parents=True, exist_ok=True)
    success = output / 'evidence.json'
    success.unlink(missing_ok=True)
    catalog = unit_catalog.load_catalog(root)
    eligible = evidence.candidates(catalog)
    selected = sorted(set(plan['tests']) | set(eligible) | set(catalog['always_tests']))
    plan = {**plan, 'tests': selected, 'jobs': {**plan['jobs'], 'unit': True}, 'run': True}
    observation = unit_catalog.describe(plan, root, catalog)
    observation['reuse_enabled'] = not force
    environment = unit_report.toolchain()
    environment['execution'] = {'pytest': '-q --durations=20', 'plugins': ['operations.ci.unit_report'],
                                'config': 'pyproject.toml', 'locale': os.environ.get('LANG'),
                                'timezone': os.environ.get('TZ')}
    unsafe = [key for key in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD') if os.environ.get(key)]
    collection = output / 'collection.json'
    collection.unlink(missing_ok=True)
    env = {**os.environ, 'UNIT_COLLECTION_FILE': str(collection)}
    command = [sys.executable, '-m', 'pytest']
    result = subprocess.run([*command, '--collect-only', '-q', '-p', 'operations.ci.unit_collection', *selected],
                            cwd=root, env=env, capture_output=True, text=True)
    if result.returncode or not collection.exists():
        print(result.stdout + result.stderr)
        for test in selected:
            observation['units'][test].update(status='blocked', reasons=[{'code': 'collection-failed'}])
        write(output / 'units.json', observation)
        return result.returncode or 1
    cases = json.loads(collection.read_text())
    inputs = input_tree(root)
    fingerprints, reused, execution = {}, {}, []
    for test in selected:
        unit = observation['units'][test]
        reason, fingerprint = 'reuse-not-enabled', None
        if test in eligible:
            fingerprint, reason = fingerprint_for_unit(inputs, test, catalog, environment)
            fingerprints[test] = fingerprint
        if unsafe:
            fingerprint, reason = None, 'unclosed-pytest-environment'
            fingerprints[test] = None
        record = (baseline or {}).get('units', {}).get(test)
        hit = False
        if force:
            reason = 'forced-full-regression' if full else 'forced-impact-full'
        elif fingerprint:
            if record:
                hit, reason = evidence.match(record, fingerprint, cases.get(test, []))
            else:
                reason = baseline_reason if baseline is None else 'unit-missing-from-latest-baseline'
        unit['reasons'].append({'code': reason})
        if hit:
            reused[test] = record
            unit.update(status='reused', outcomes={'reused_success': len(record['cases'])}, evidence={
                'fingerprint': fingerprint, 'origin': record['origin'], 'case_count': len(record['cases']),
                'age_seconds': time.time() - record['verified_at'],
                'url': f"https://github.com/{publisher['repository']}/actions/runs/{record['origin']['run_id']}"})
        else:
            execution.append(test)
    execution_plan = {**plan, 'tests': execution}
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    execution_env = {**os.environ, 'UNIT_PLAN': json.dumps(execution_plan)}
    # The final aggregate owns the summary; pytest/JUnit describe real execution only.
    execution_env.pop('GITHUB_STEP_SUMMARY', None)
    actual = output / 'execution'
    for stale in ('units.json', 'junit.xml'):
        (actual / stale).unlink(missing_ok=True)
    if execution:
        result = subprocess.run([*command, '-q', '--durations=20', '-p', 'operations.ci.unit_report',
                                 '--unit-report-dir=' + str(actual), '--junitxml=' + str(actual / 'junit.xml'),
                                 *execution], cwd=root, env=execution_env)
        code = result.returncode
    else:
        code = 0
    report = json.loads((actual / 'units.json').read_text()) if (actual / 'units.json').exists() else {}
    records = {}
    # Re-read inputs after execution: mutations cannot certify an old fingerprint.
    after = input_tree(root)
    for test in selected:
        unit = observation['units'][test]
        if test in reused:
            fresh, _ = fingerprint_for_unit(after, test, catalog, environment)
            if fresh != fingerprints[test]:
                unit.update(status='blocked', reasons=[{'code': 'inputs-mutated-during-validation'}])
                code = 1
            else:
                records[test] = {key: reused[test][key] for key in ('fingerprint', 'cases', 'result', 'seconds', 'verified_at', 'origin')}
                records[test]['inherited_from'] = baseline['publisher']
            continue
        real = report.get('units', {}).get(test)
        if real:
            reasons = unit['reasons']
            unit.update(real)
            unit['reasons'] = reasons
        else:
            unit['status'] = 'blocked'
        if test not in eligible or not fingerprints.get(test):
            continue
        fresh, _ = fingerprint_for_unit(after, test, catalog, environment)
        expected = sorted(cases.get(test, []))
        actual_cases = unit.get('cases', {})
        if (fresh == fingerprints[test] and expected and sorted(actual_cases) == expected
                and all(unit_report.outcome(phases) == 'passed' for phases in actual_cases.values())):
            records[test] = {'fingerprint': fresh, 'cases': expected, 'result': 'passed',
                             'seconds': sum(unit['seconds'].values()), 'verified_at': time.time(),
                             'origin': publisher, 'inherited_from': None}
    # Any unit failure prevents publishing even previously successful inherited records.
    if code == 0 and any(observation['units'][test]['status'] in {'blocked', 'failed'} for test in selected):
        code = 1
    observation.update(exit_code=code, wall_seconds=time.monotonic() - started + lookup_seconds, lookup_seconds=lookup_seconds, toolchain=environment,
                       baseline_reason=baseline_reason, publisher=publisher,
                       counts=dict(Counter(u['status'] for u in observation['units'].values())))
    write(output / 'units.json', observation)
    if code == 0:
        payload = {'version': evidence.VERSION, 'publisher': publisher, 'units': records}
        if len(json.dumps(payload).encode()) > evidence.MAX_BYTES:
            raise ValueError('unit evidence exceeds bounded size')
        write(success, payload)
    if summary_path:
        with open(summary_path, 'a') as stream:
            stream.write(f"### Main unit evidence\n\n{observation['counts']}; wall {observation['wall_seconds']:.2f}s. "
                         f"Baseline: {baseline_reason}. Latest only; retention 14 days.\n\n")
            stream.write(unit_catalog.summary(observation).replace('input-aware; reuse disabled', 'exact success evidence'))
            for test in reused:
                info = observation['units'][test]['evidence']
                stream.write(f"\nReused `{test}`: `{info['fingerprint']}`; [original run]({info['url']}), "
                             f"SHA `{info['origin']['sha']}`, {info['case_count']} cases, age {info['age_seconds']:.0f}s.\n")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    repository = os.environ['GITHUB_REPOSITORY']
    workflow = os.environ.get('GITHUB_WORKFLOW_REF', '')
    event = os.environ.get('GITHUB_EVENT_NAME')
    allowed = any(workflow == f'{repository}/.github/workflows/{name}@refs/heads/main' and event in events
                  for name, events in evidence.SOURCES.items())
    if os.environ.get('GITHUB_REF') != 'refs/heads/main' or not allowed:
        parser.error('unit success publication is restricted to approved main workflows')
    if workflow.endswith('/full-regression.yml@refs/heads/main') and not args.full:
        parser.error('full regression must execute every test')
    publisher = {'repository': repository, 'run_id': int(os.environ['GITHUB_RUN_ID']),
                 'attempt': int(os.environ['GITHUB_RUN_ATTEMPT']), 'sha': os.environ['GITHUB_SHA']}
    plan = impact.select([], full=True) if args.full else json.loads(args.plan.read_text())
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() != publisher['sha']:
        parser.error('checked-out SHA does not match workflow identity')
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all']):
        parser.error('baseline publication requires a clean checkout')
    lookup_started = time.monotonic()
    baseline, reason = (None, 'forced-full-regression') if args.full else evidence.latest_baseline(repository)
    lookup_seconds = time.monotonic() - lookup_started
    code = run(Path.cwd(), plan, args.output, publisher, baseline=baseline, baseline_reason=reason, full=args.full, lookup_seconds=lookup_seconds)
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all']):
        (args.output / 'evidence.json').unlink(missing_ok=True)
        parser.error('checkout changed during validation; no evidence published')
    raise SystemExit(code)


if __name__ == '__main__':
    main()
