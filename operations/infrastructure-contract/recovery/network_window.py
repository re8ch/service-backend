"""Offline CAS patch planner for two temporary recovery policy exclusions."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[3]
NAMES = {'allow-egress', 'allow-scraping'}
APPS = ['infra-contracts-recovery', 'infra-contracts-recovery-preflight']
SELECTOR = {'matchExpressions': [{'key': 'app', 'operator': 'NotIn', 'values': APPS}]}
class Rejected(Exception): pass

def baseline():
    docs = yaml.safe_load_all((ROOT / 'clusters/re8ch/flux-system/gotk-components.yaml').read_text())
    objects = {x['metadata']['name']: x['spec'] for x in docs if x and x.get('kind') == 'NetworkPolicy' and x['metadata'].get('namespace') == 'flux-system' and x['metadata']['name'] in NAMES}
    if set(objects) != NAMES or any(s['podSelector'] != {} for s in objects.values()): raise Rejected()
    return objects

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def plan(observed, action, receipt=None):
    if action not in {'open', 'close'}: raise Rejected()
    source = baseline()
    rows = observed.get('items', [])
    if len(rows) != 2: raise Rejected()
    actual = {o['metadata']['name']: o for o in rows}
    if set(actual) != NAMES: raise Rejected()
    source_hash = fingerprint(source)
    if action == 'close' and (not receipt or set(receipt) != {'sourceHash', 'uids'} or receipt['sourceHash'] != source_hash or set(receipt['uids']) != NAMES): raise Rejected()
    patches, uids = {}, {}
    for name in sorted(NAMES):
        obj = actual[name]; m = obj['metadata']
        if obj.get('kind') != 'NetworkPolicy' or obj.get('apiVersion') != 'networking.k8s.io/v1' or m.get('namespace') != 'flux-system': raise Rejected()
        uid, version = m.get('uid'), m.get('resourceVersion')
        if not isinstance(uid, str) or not uid or not isinstance(version, str) or not version: raise Rejected()
        expected = copy.deepcopy(source[name])
        if action == 'close':
            expected['podSelector'] = SELECTOR
            if receipt['uids'][name] != uid: raise Rejected()
            # Explicit cleanup may follow a partial open; already-original is safe.
            if obj['spec'] == source[name]:
                uids[name] = uid
                continue
        if obj['spec'] != expected: raise Rejected()
        uids[name] = uid
        patches[name] = [
          {'op': 'test', 'path': '/metadata/uid', 'value': uid},
          {'op': 'test', 'path': '/metadata/resourceVersion', 'value': version},
          {'op': 'test', 'path': '/spec', 'value': expected},
          {'op': 'replace', 'path': '/spec/podSelector', 'value': SELECTOR if action == 'open' else source[name]['podSelector']},
        ]
    return {'namespace': 'flux-system', 'action': action, 'patches': patches,
            'receipt': {'sourceHash': source_hash, 'uids': uids}}

if __name__ == '__main__':
    try:
        if len(sys.argv) not in (3, 4): raise Rejected()
        action = sys.argv[1]
        observed = json.loads(Path(sys.argv[2]).read_text())
        receipt = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) == 4 else None
        print(json.dumps(plan(observed, action, receipt), sort_keys=True))
    except Exception:
        print('{"status":"rejected","reason":"policy_snapshot_or_receipt_mismatch"}')
        raise SystemExit(1) from None
