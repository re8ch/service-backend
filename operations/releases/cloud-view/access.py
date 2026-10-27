"""Derive bounded observation reads from existing executor declarations."""
import json
from collections import defaultdict
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]
DOMAINS = ('main', 'networking', 'identity-access', 'agent-runtime', 'observability-metering', 'delivery-products')
APPS = {'deployments': 'Deployment', 'statefulsets': 'StatefulSet', 'daemonsets': 'DaemonSet'}

def inventory(root=ROOT):
    ops = root / 'clusters/re8ch/components/ops'
    paths = sorted(ops.glob('*executor-rbac.yaml')) + [root / 'clusters/re8ch/components/release-compat/ceph-executor-rbac.yaml']
    retired = yaml.safe_load((root / 'operations/releases/static-domain-authority.yaml').read_text())['spec'].get('retiredReleases', {})
    entries = {}
    for path in paths:
        docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
        roles = [d for d in docs if d['kind'] == 'Role']
        for cm in docs:
            meta = cm.get('metadata', {})
            if cm['kind'] != 'ConfigMap' or meta.get('labels', {}).get('delivery.re8ch.com/state') != 'accepted':
                continue
            ns, name = meta['namespace'], meta['name']
            if name.removeprefix('ssa-state-') in retired:
                continue
            writers = [r for r in roles if r['metadata'].get('namespace') == ns and any(
                'configmaps' in rule.get('resources', []) and name in rule.get('resourceNames', []) and
                set(rule['verbs']) & {'patch', 'update'} for rule in r.get('rules', []))]
            # Retired authorities retain a GET-only Role and historical state.
            if not writers:
                continue
            domain = meta.get('labels', {}).get('delivery.re8ch.com/authority-domain', 'main')
            if domain not in DOMAINS:
                domain = 'main'
            workloads = set()
            for role in writers:
                for rule in role.get('rules', []):
                    if rule.get('apiGroups') == ['apps'] and set(rule['verbs']) & {'patch', 'update'}:
                        for resource in rule['resources']:
                            if resource in APPS:
                                for obj in rule.get('resourceNames', []):
                                    workloads.add((APPS[resource], ns, obj))
            entries[(ns, name)] = {'domain': domain, 'namespace': ns, 'name': name,
                'workloads': [dict(zip(('kind', 'namespace', 'name'), w)) for w in sorted(workloads)]}
    return sorted(entries.values(), key=lambda e: (e['domain'], e['namespace'], e['name']))

def rbac(entries):
    permissions = defaultdict(lambda: defaultdict(set))
    for e in entries:
        permissions[e['namespace']][('', 'configmaps')].add(e['name'])
        for w in e['workloads']:
            resource = next(r for r,k in APPS.items() if k == w['kind'])
            permissions[w['namespace']][('apps', resource)].add(w['name'])
    docs = []
    for ns, rules in sorted(permissions.items()):
        meta = {'name': 'github-actions-release-observer', 'namespace': ns}
        docs += [{'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': meta,
            'rules': [{'apiGroups': [g], 'resources': [r], 'resourceNames': sorted(names), 'verbs': ['get']}
                      for (g,r), names in sorted(rules.items())]},
            {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': meta,
             'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': meta['name']},
             'subjects': [{'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Group', 'name': 're8ch:credential-contract:node-observer'}]}]
    return docs

if __name__ == '__main__':
    entries = inventory()
    (ROOT / 'operations/releases/cloud-view/observation-inventory.json').write_text(json.dumps(entries, indent=2)+'\n')
    target = ROOT / 'clusters/re8ch/components/ops/github-actions-node-observer-rbac.yaml'
    previous = list(yaml.safe_load_all(target.read_text()))
    base = [d for d in previous if d and d['kind'] in ('ClusterRole', 'ClusterRoleBinding')]
    base[0]['rules'] = [r for r in base[0]['rules'] if r.get('apiGroups') == [''] or 'nonResourceURLs' in r]
    target.write_text('# Generated release reads: operations/releases/cloud-view/access.py\n'+yaml.safe_dump_all(base+rbac(entries),sort_keys=False))
