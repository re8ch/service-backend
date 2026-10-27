#!/usr/bin/env python3
"""Bounded cloud observation. No Flux reads, raw resource output, or cluster writes."""
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import ssl
import urllib.error
import urllib.parse
import urllib.request

DOMAINS = ('main', 'networking', 'identity-access', 'agent-runtime', 'observability-metering', 'delivery-products')
WORKFLOWS = {
    'main': ('re8ch/service-backend', ['main-domain-release.yml', 'main-domain-reconcile.yml', 'ceph-static-release.yml'], 'RE8CH_OBSERVER_TOKEN'),
    'networking': ('pillars-infra/network-gitops', ['static-release.yml', 'networking-reconcile.yml'], 'NETWORK_OBSERVER_TOKEN'),
    'identity-access': ('re8ch/identity-gitops', ['static-release.yml'], 'RE8CH_OBSERVER_TOKEN'),
    'agent-runtime': ('8iops/agent-gitops', ['static-release.yml'], 'AGENT_OBSERVER_TOKEN'),
    'observability-metering': ('siren-observer/observability-gitops', ['static-release.yml'], 'OBSERVABILITY_OBSERVER_TOKEN'),
    'delivery-products': ('re8ch/delivery-products-gitops', ['static-release.yml', 'delivery-products-reconcile.yml'], 'RE8CH_OBSERVER_TOKEN'),
}
RESOURCES = {'Deployment': 'deployments', 'StatefulSet': 'statefulsets', 'DaemonSet': 'daemonsets'}

def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')

def epoch(value):
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0

def sync_state(accepted, result, operation):
    # A later failed/in-flight cloud attempt cannot inherit an earlier success.
    latest = max(epoch(result.get('finishedAt')), epoch(accepted.get('updatedAt')))
    if operation['status'] == 'unknown' or (operation['status'] in ('failed', 'running') and epoch(operation['observedAt']) > latest):
        return 'unknown'
    if accepted.get('blocked'):
        return 'failed'
    if result.get('application', {}).get('status') == 'complete':
        return 'synced'
    if result.get('outcome') == 'failed':
        return 'failed'
    if result.get('outcome') == 'rolled-back':
        return 'drifted'
    if result.get('outcome') == 'healthy' and result.get('observedDigest') == result.get('approvedDigest') and result.get('observedDigest'):
        return 'synced'
    return 'unknown'

def health_state(obj):
    spec, status = obj.get('spec', {}), obj.get('status', {})
    if status.get('observedGeneration', -1) < obj.get('metadata', {}).get('generation', 0):
        return 'unknown'
    if obj['kind'] == 'DaemonSet':
        desired = status.get('desiredNumberScheduled', 0)
        return 'healthy' if desired > 0 and status.get('numberReady', 0) == desired and status.get('updatedNumberScheduled', 0) == desired and status.get('numberUnavailable', 0) == 0 else 'unhealthy'
    desired = spec.get('replicas', 1)
    if desired == 0:
        return 'unknown'
    if status.get('readyReplicas', 0) != desired or status.get('updatedReplicas', 0) != desired:
        return 'unhealthy'
    if obj['kind'] == 'Deployment' and status.get('availableReplicas', 0) != desired:
        return 'unhealthy'
    return 'healthy'

def request_json(url, token, context=None):
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer '+token, 'Accept': 'application/json', 'User-Agent': 're8ch-cloud-observer'})
    with urllib.request.urlopen(req, context=context, timeout=15) as response:
        return json.load(response)

def operation(domain):
    repo, workflows, env = WORKFLOWS[domain]
    token = os.environ.get(env)
    unknown = {'status': 'unknown', 'observedAt': None}
    if not token:
        return unknown
    try:
        runs = []
        for workflow in workflows:
            base = f'https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?branch=main&per_page=100'
            data = request_json(base, token)
            total = data['total_count']
            # Reruns keep created_at. Read the complete bounded history rather than
            # selecting the newest-created run and silently missing an old rerun.
            if total > 1000:
                return unknown
            history = data['workflow_runs']
            for page in range(2, (total+99)//100+1):
                history += request_json(base+f'&page={page}', token)['workflow_runs']
            if len({r['id'] for r in history}) != total:
                return unknown
            runs += history
        if not runs:
            return unknown
        latest = max(runs, key=lambda r: epoch(r['updated_at']))
        state = 'running' if latest['status'] != 'completed' else ('succeeded' if latest['conclusion'] == 'success' else 'failed')
        return {'status': state, 'observedAt': latest['updated_at']}
    except Exception:
        return unknown

class Cluster:
    def __init__(self):
        self.context = ssl.create_default_context(cadata=os.environ['KUBE_API_CA'])
        parts = urllib.parse.urlsplit(os.environ['ACTIONS_ID_TOKEN_REQUEST_URL'])
        query = dict(urllib.parse.parse_qsl(parts.query))
        query['audience'] = 'https://kube-api.re8ch.com/observer'
        url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
        self.token = request_json(url, os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN'])['value']
        # Fixed public service endpoint; no local kubeconfig or credential fallback.
        self.server = 'https://kube-api.re8ch.com:6443'
        for path in ('/api/v1/namespaces/default/secrets', '/api/v1/configmaps'):
            try:
                request_json(self.server+path, self.token, self.context)
            except urllib.error.HTTPError as error:
                if error.code == 403:
                    continue
                raise RuntimeError('observer identity unavailable') from None
            raise RuntimeError('observer exceeded read boundary')

    def get(self, kind, namespace, name):
        if kind == 'ConfigMap':
            path = f'/api/v1/namespaces/{namespace}/configmaps/{name}'
        else:
            path = f'/apis/apps/v1/namespaces/{namespace}/{RESOURCES[kind]}/{name}'
        return request_json(self.server+path, self.token, self.context)

def summarize(entries, cluster, operations, now):
    domains = {d: {'id': d, 'sync': dict.fromkeys(('synced','drifted','failed','unknown'), 0),
        'health': dict.fromkeys(('healthy','unhealthy','unknown'), 0),
        'observation': {'status': 'current', 'observedAt': now}, 'operations': operations[d]} for d in DOMAINS}
    workloads = {}
    for entry in entries:
        for w in entry['workloads']:
            workloads[(entry['domain'], w['kind'], w['namespace'], w['name'])] = w
    def release(entry):
        try:
            data = cluster.get('ConfigMap', entry['namespace'], entry['name']).get('data', {})
            accepted = json.loads(data.get('accepted-state.json', '{}'))
            result = json.loads(data.get('last-result.json', '{}'))
            return entry['domain'], sync_state(accepted, result, operations[entry['domain']]), True
        except Exception:
            return entry['domain'], 'unknown', False
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for domain, state, available in pool.map(release, entries):
            domains[domain]['sync'][state] += 1
            if not available:
                domains[domain]['observation']['status'] = 'unavailable'
        def workload(item):
            key, w = item
            try:
                return key[0], health_state(cluster.get(w['kind'], w['namespace'], w['name'])), True
            except urllib.error.HTTPError as error:
                return key[0], 'unhealthy' if error.code == 404 else 'unknown', error.code == 404
            except Exception:
                return key[0], 'unknown', False
        for domain, state, available in pool.map(workload, workloads.items()):
            domains[domain]['health'][state] += 1
            if not available:
                domains[domain]['observation']['status'] = 'unavailable'
    return list(domains.values())

def main():
    now = timestamp()
    snapshot = {'schema': 2, 'repository': os.environ['GITHUB_REPOSITORY'], 'sha': os.environ['GITHUB_SHA'],
        'batch': f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}", 'observedAt': now, 'complete': False, 'domains': []}
    try:
        if snapshot['repository'] != 're8ch/service-backend' or os.environ['GITHUB_REF'] != 'refs/heads/main':
            raise ValueError('unexpected observer identity')
        entries = json.loads(Path(__file__).with_name('observation-inventory.json').read_text())
        cluster = Cluster()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            operations = dict(zip(DOMAINS, pool.map(operation, DOMAINS)))
        snapshot['domains'] = summarize(entries, cluster, operations, now)
        snapshot['complete'] = True
    except Exception:
        # Exceptions may contain API responses, argv, or credentials. Never print them.
        pass
    Path(os.environ['RUNNER_TEMP'], 'cloud-runtime-snapshot.json').write_text(json.dumps(snapshot))
    print('Cloud observation produced a bounded aggregate; unavailable reads remain Unknown.')

if __name__ == '__main__':
    main()
