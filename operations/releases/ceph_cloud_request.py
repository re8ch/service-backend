#!/usr/bin/env python3
"""Create a cloud-owned immutable request snapshot for the existing Ceph executor."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
import yaml
from unified_bundle import canonical, digest, validate_schema

ROOT = Path(__file__).resolve().parents[2]
RELEASES = {'rook-ceph', 'ceph-csi-drivers', 'rook-ceph-cluster'}
ACTIONS = {'reconcile', 'force-reconcile', 'reset', 'suspend', 'resume', 'rollback'}


def prepare(inputs: dict, *, run_id: str, actor: str, legacy: dict | None = None) -> tuple[dict, dict]:
    if legacy is not None:
        if any(inputs.get(key) for key in ('release', 'approved_digest', 'approval_ref', 'force_objects', 'reason')):
            raise ValueError('legacy operation_id cannot be combined with direct request inputs')
        if inputs.get('action') not in (None, '', 'reconcile'):
            raise ValueError('legacy action is defined by its immutable spec')
        resource = json.loads(json.dumps(legacy))
        if resource.get('kind') != 'ReleaseOperation' or resource['metadata'].get('namespace') != 'flux-system':
            raise ValueError('invalid legacy operation identity')
        uuid.UUID(resource['metadata']['uid'])
        created = dt.datetime.fromisoformat(resource['metadata']['creationTimestamp'].replace('Z', '+00:00'))
        timeout = resource['spec']['timeoutSeconds']
        if not isinstance(timeout, int) or not 1 <= timeout <= 3600 or created + dt.timedelta(seconds=timeout) <= dt.datetime.now(dt.timezone.utc):
            raise ValueError('legacy operation expired before cloud dispatch')
        name = resource['spec']['releaseRef']
    else:
        name = inputs.get('release')
    if name not in RELEASES:
        raise ValueError('unknown Ceph release')
    releases = list(yaml.safe_load_all((ROOT / 'clusters/re8ch/components/release-compat/ceph-static-releases.yaml').read_text()))
    release = next(item for item in releases if item and item['metadata']['name'] == name)
    contract = yaml.safe_load((ROOT / f'contracts/releases/ceph/{name}.yaml').read_text())
    if release['spec']['contractDigest'] != digest(canonical(contract)):
        raise ValueError('Git release contract digest mismatch')
    if legacy is None:
        action = inputs.get('action') or 'reconcile'
        approved = inputs.get('approved_digest') or release['spec']['bundleDigest']
        if not run_id.isdecimal():
            raise ValueError('invalid GitHub run identity')
        resource = {'apiVersion':'delivery.re8ch.com/v1alpha1','kind':'ReleaseOperation',
                    'metadata':{'name':f'github-{run_id}', 'namespace':'flux-system',
                                'uid':str(uuid.uuid5(uuid.NAMESPACE_URL, f'https://github.com/re8ch/service-backend/actions/runs/{run_id}'))},
                    'spec':{'releaseRef':name,'action':action,'approvedDigest':approved,
                            'contractDigest':release['spec']['contractDigest'],'requestedBy':actor,
                            'reason':inputs.get('reason') or 'GitHub protected workflow request',
                            'idempotencyKey':f'github-run-{run_id}','timeoutSeconds':3600}}
        if inputs.get('approval_ref'):
            resource['spec']['approvalRef'] = inputs['approval_ref']
        if inputs.get('force_objects'):
            resource['spec']['forceConflictObjects'] = json.loads(inputs['force_objects'])
    spec = resource['spec']
    if spec['action'] not in ACTIONS or not re.fullmatch(r'sha256:[0-9a-f]{64}', spec['approvedDigest']):
        raise ValueError('invalid action or digest')
    if spec['contractDigest'] != release['spec']['contractDigest']:
        raise ValueError('request contract differs from reviewed Git')
    if spec['action'] == 'rollback':
        if not spec.get('approvalRef') or (legacy is None and not inputs.get('approved_digest')):
            raise ValueError('rollback requires explicit digest and approval reference')
    elif spec['approvedDigest'] != release['spec']['bundleDigest']:
        raise ValueError('request does not match reviewed Git digest')
    force = spec.get('forceConflictObjects', [])
    if not isinstance(force, list) or not all(isinstance(item, str) for item in force):
        raise ValueError('force objects must be a JSON string array')
    if force and (spec['action'] != 'force-reconcile' or not set(force).issubset(contract['spec']['authorization'].get('forceConflictObjects', []))):
        raise ValueError('force objects are outside the reviewed contract')
    if len(spec.get('reason', '')) > 512 or len(spec.get('approvalRef', '')) > 253:
        raise ValueError('request text exceeds schema bounds')
    return resource, release


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    inputs=json.loads(args.inputs.read_text());legacy=None
    if inputs.get('operation_id'):
        if not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',inputs['operation_id']):
            raise ValueError('invalid legacy operation name')
        legacy=json.loads(subprocess.check_output(['kubectl','get','releaseoperation.delivery.re8ch.com',inputs['operation_id'],'-n','flux-system','-o','json']))
    resource,release=prepare(inputs,run_id=os.environ['GITHUB_RUN_ID'],actor=os.environ['GITHUB_ACTOR'],legacy=legacy)
    # Dependency readiness comes from durable accepted state, not a local projector.
    for name in release['spec'].get('dependencies',[]):
        cm=json.loads(subprocess.check_output(['kubectl','get','configmap','ssa-state-'+name,'-n','rook-ceph','-o','json']))
        state=json.loads(cm.get('data',{}).get('accepted-state.json','{}'))
        validate_schema(state, 'release-accepted-state.schema.json')
        if state.get('releaseId') != name or state.get('blocked') or state.get('suspended') or not state.get('healthyDigest') or state.get('healthyDigest') != state.get('appliedDigest'):
            raise ValueError('Ceph dependency lacks healthy accepted state')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    for name,value in [('operation-resource.json',resource),('static-release.json',release)]:
        (args.output_dir/name).write_bytes(canonical(value))
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:
        out.write('release='+resource['spec']['releaseRef']+'\naction='+resource['spec']['action']+'\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
