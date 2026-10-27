#!/usr/bin/env python3
"""Bounded cloud observation. No Flux reads, raw resource output, or cluster writes."""
import concurrent.futures
import datetime as dt
import json
import io
import re
import zipfile
import os
from pathlib import Path
import ssl
import time
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

# Shared by all worker threads. Initialized once per collection, never renewed by
# an individual request or queued task. Leave enough of the 8-minute job for upload.
DEADLINE = None
COLLECTION_SECONDS = 240

def remaining_timeout():
    remaining = 15 if DEADLINE is None else DEADLINE - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('collection deadline reached')
    return min(15, remaining)

def read_bounded(response, limit):
    chunks, size = [], 0
    while size <= limit:
        timeout = remaining_timeout()
        # HTTPResponse's socket initially inherits the connection timeout; shrink
        # it before each single buffered read as the shared deadline approaches.
        sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if sock is not None:
            sock.settimeout(timeout)
        read = getattr(response, 'read1', response.read)
        chunk = read(min(65536, limit + 1 - size))
        remaining_timeout()
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)
        size += len(chunk)
    raise ValueError('response exceeds bound')

def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')

def epoch(value):
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0

def sync_state(accepted, result, operation):
    # A later failed/in-flight cloud attempt cannot inherit an earlier success.
    correlated = any(result == candidate and result.get('releaseId') and result.get('operationId')
                     for candidate in operation.get('_results', []))
    if operation['status'] in ('unknown', 'running') or (operation['status'] == 'failed' and not correlated):
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
    with urllib.request.urlopen(req, context=context, timeout=remaining_timeout()) as response:
        return json.loads(read_bounded(response, 4 * 1024 * 1024))

# Only full executor results qualify. In particular Ceph runtime/infra are stages.
MAX_ARCHIVE = 1024 * 1024
MAX_RESULT = 256 * 1024
MAX_ARTIFACTS = 64

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def download_artifact(repo, artifact_id, token):
    url = f'https://api.github.com/repos/{repo}/actions/artifacts/{int(artifact_id)}/zip'
    opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer '+token,
                                              'User-Agent': 're8ch-cloud-observer'})
    try:
        response = opener.open(req, timeout=remaining_timeout())
    except urllib.error.HTTPError as error:
        if error.code != 302:
            raise
        location = error.headers.get('Location', '')
        parts = urllib.parse.urlsplit(location)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise ValueError('invalid artifact location') from None
        # Deliberately construct a fresh request without Authorization. Do not
        # follow further redirects (including HTTPS -> HTTP).
        response = opener.open(urllib.request.Request(location), timeout=remaining_timeout())
    with response:
        payload = read_bounded(response, MAX_ARCHIVE)
    if len(payload) > MAX_ARCHIVE:
        raise ValueError('artifact exceeds bound')
    return payload

def artifact_result(payload):
    remaining_timeout()
    if len(payload) > MAX_ARCHIVE:
        raise ValueError('artifact exceeds bound')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) > 16 or sum(m.file_size for m in members) > MAX_ARCHIVE:
            raise ValueError('archive exceeds bound')
        selected = [m for m in members if m.filename in ('result.json', 'static-release-result.json')]
        if len(selected) != 1 or selected[0].file_size > MAX_RESULT:
            return None
        with archive.open(selected[0]) as stream:
            raw = stream.read(MAX_RESULT + 1)
        if len(raw) > MAX_RESULT:
            raise ValueError('result exceeds bound')
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('apiVersion') != 'delivery.re8ch.com/release-result/v1alpha1':
            return None
        if not all(isinstance(result.get(k), str) and result[k] for k in ('releaseId', 'operationId')):
            return None
        return result

def run_results(repo, run, token):
    # Existing artifact names do not encode run_attempt. Never attribute an
    # earlier attempt's retained artifact to a failed rerun using timestamp guesses.
    if run.get('run_attempt') != 1:
        return []
    run_id = int(run['id'])
    data = request_json(f'https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100', token)
    artifacts = data['artifacts']
    if data['total_count'] != len(artifacts) or len(artifacts) > MAX_ARTIFACTS:
        return []
    pattern = re.compile(r'(?:main-domain-release-result|static-release-result|ceph-accept|(?:main-domain|networking|delivery-products)-reconcile-[a-z0-9-]+)-' + str(run_id))
    results = []
    for artifact in artifacts:
        provenance = artifact.get('workflow_run', {})
        if (provenance.get('id') != run_id or not run.get('head_sha')
                or provenance.get('head_sha') != run['head_sha']
                or artifact.get('expired') or not pattern.fullmatch(artifact['name'])):
            continue
        if not 0 < artifact.get('size_in_bytes', 0) <= MAX_ARCHIVE:
            continue
        try:
            result = artifact_result(download_artifact(repo, artifact['id'], token))
            if result is not None:
                results.append(result)
        except Exception:
            # Missing/expired/malformed evidence leaves only that release Unknown.
            continue
    return results

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
        return {'status': state, 'observedAt': latest['updated_at'],
                '_results': run_results(repo, latest, token) if state == 'failed' else []}
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
        'observation': {'status': 'current', 'observedAt': now}, 'operations': {k: operations[d][k] for k in ('status', 'observedAt')}} for d in DOMAINS}
    workloads = {}
    for entry in entries:
        for w in entry['workloads']:
            workloads[(entry['domain'], w['kind'], w['namespace'], w['name'])] = w
    def release(entry):
        try:
            remaining_timeout()
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
                remaining_timeout()
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
    global DEADLINE
    DEADLINE = time.monotonic() + COLLECTION_SECONDS
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
