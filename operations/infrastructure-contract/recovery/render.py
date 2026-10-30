"""Render fixed recovery objects; encrypted inputs only, no credentials accessed."""
from pathlib import Path
import hashlib
import json
import sys
import yaml
import restore
ROOT = Path(__file__).resolve().parents[3]
DIR = Path(__file__).resolve().parent
NAME = 'infra-contracts-recovery'
IMAGE = 'ghcr.io/getsops/sops@sha256:320f253aced1393537b1e90c77eb48295204d805d4c68933264cd1285192465d'

def render(receipts=None, apply=False):
    receipts = receipts or {n: {'uid': 'REPLACE_WITH_PRECREATED_UID', 'resourceVersion': 'REPLACE_WITH_PRECREATED_RESOURCE_VERSION'} for n in restore.TARGETS}
    data = {'targets.json': json.dumps(receipts, sort_keys=True), 'restore.py': (DIR / 'restore.py').read_text()}
    for obj in yaml.safe_load_all((ROOT / 'clusters/re8ch/secrets/ai-operations-inventory.sops.yaml').read_text()):
        n = obj['metadata']['name']
        if n in restore.TARGETS:
            raw = json.dumps(obj, sort_keys=True, separators=(',', ':'))
            assert hashlib.sha256(raw.encode()).hexdigest() == restore.CIPHER_HASHES[n]
            data[n + '.json'] = raw
    common = {'name': NAME, 'namespace': 'flux-system'}
    return [
      {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': common, 'automountServiceAccountToken': False},
      {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': {'name': NAME, 'namespace': 'ai-operations'}, 'rules': [{'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': list(restore.TARGETS), 'verbs': ['patch']}]},
      {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': {'name': NAME, 'namespace': 'ai-operations'}, 'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': NAME}, 'subjects': [{'kind': 'ServiceAccount', 'name': NAME, 'namespace': 'flux-system'}]},
      {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': common, 'immutable': True, 'data': data},
      {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy', 'metadata': common, 'spec': {'endpointSelector': {'matchLabels': {'app': NAME}}, 'enableDefaultDeny': {'ingress': True, 'egress': True}, 'ingress': [], 'egress': [{'toEntities': ['kube-apiserver'], 'toPorts': [{'ports': [{'port': '443', 'protocol': 'TCP'}, {'port': '6443', 'protocol': 'TCP'}]}]}]}},
      {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {**common, 'name': NAME + ('-apply' if apply else '-dryrun')}, 'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 600, 'ttlSecondsAfterFinished': 3600, 'template': {'metadata': {'labels': {'app': NAME}}, 'spec': {
        'restartPolicy': 'Never', 'serviceAccountName': NAME, 'automountServiceAccountToken': False,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 65532, 'runAsGroup': 65532, 'fsGroup': 65532, 'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{'name': 'restore', 'image': IMAGE, 'command': ['python3', '-B', '/input/restore.py'] + (['--apply'] if apply else []),
          'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
          'resources': {'requests': {'cpu': '50m', 'memory': '128Mi'}, 'limits': {'cpu': '500m', 'memory': '512Mi'}},
          'volumeMounts': [{'name': 'input', 'mountPath': '/input', 'readOnly': True}, {'name': 'state', 'mountPath': '/state', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'identity', 'mountPath': '/var/run/secrets/kubernetes.io/serviceaccount', 'readOnly': True}]}],
        'initContainers': [{'name': 'decrypt', 'image': IMAGE, 'command': ['python3', '-B', '/input/restore.py', '--prepare'], 'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}, 'resources': {'requests': {'cpu': '50m', 'memory': '128Mi'}, 'limits': {'cpu': '500m', 'memory': '512Mi'}}, 'volumeMounts': [{'name': 'input', 'mountPath': '/input', 'readOnly': True}, {'name': 'age', 'mountPath': '/age', 'readOnly': True}, {'name': 'state', 'mountPath': '/state'}, {'name': 'tmp', 'mountPath': '/tmp'}]}],
        'volumes': [{'name': 'state', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}}, {'name': 'input', 'configMap': {'name': NAME, 'defaultMode': 288}}, {'name': 'age', 'secret': {'secretName': 'sops-age', 'defaultMode': 288, 'items': [{'key': 'age.agekey', 'path': 'age.agekey'}]}}, {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}}, {'name': 'identity', 'projected': {'defaultMode': 288, 'sources': [{'serviceAccountToken': {'path': 'token', 'expirationSeconds': 600}}, {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}}]
      }}}},
    ]

if __name__ == '__main__':
    # Receipts are non-secret metadata produced by platform precreation.
    if len(sys.argv) > 3 or (len(sys.argv) == 3 and sys.argv[2] != '--apply'): raise SystemExit(1)
    receipts = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) >= 2 else None
    print(yaml.safe_dump_all(render(receipts, len(sys.argv) == 3), sort_keys=False))
