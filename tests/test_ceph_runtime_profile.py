"""Runtime stage identity stays distinct from signed publisher provenance."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from test_ssa_release_executor import NOW, Runner, executor, prepare
from ceph_execution_profile import runtime_provenance
from unified_bundle import canonical, digest

ROOT = Path(__file__).resolve().parents[1]


def request(tmp_path, name='rook-ceph'):
    _, operation, bundle = prepare(tmp_path)
    contract = yaml.safe_load((ROOT / f'contracts/releases/ceph/{name}.yaml').read_text())
    runtime = runtime_provenance(contract)
    operation.update(releaseId=name, target='re8ch/rook-ceph', contractDigest=digest(canonical(contract)),
                     inventoryDigest=contract['spec']['inventory']['digest'])
    operation['github'].update(runtime)
    operation['bundle'].update(signer=contract['spec']['provenance']['signer'])
    manifest = {'releaseId': name, 'contractDigest': operation['contractDigest'],
                'inventoryDigest': operation['inventoryDigest']}
    return contract, operation, bundle, manifest


@pytest.mark.parametrize('name', ['rook-ceph', 'rook-ceph-cluster', 'ceph-csi-drivers'])
def test_exact_three_contracts_accept_runtime_without_rebinding_bundle(tmp_path, monkeypatch, name):
    contract, operation, bundle, manifest = request(tmp_path, name)
    monkeypatch.setenv('EXECUTOR_AUDIENCE', operation['github']['audience'])
    monkeypatch.setattr(executor, 'load_archive', lambda _: (manifest, []))
    instance = executor.Executor(now=lambda: NOW)
    assert instance.validate(contract, operation, bundle, executor.Store(tmp_path, name))[0] == manifest
    modified = copy.deepcopy(contract)
    modified['spec']['lifecycle']['healthTimeoutSeconds'] += 1
    assert runtime_provenance(modified) is None


@pytest.mark.parametrize('field,value', [('repository','other/repo'), ('workflow','.github/workflows/other.yml'),
    ('ref','refs/heads/other'), ('environment','release-executor-ceph-infra')])
def test_runtime_rejects_identity_mismatch(tmp_path, monkeypatch, field, value):
    contract, operation, bundle, _ = request(tmp_path)
    monkeypatch.setenv('EXECUTOR_AUDIENCE', operation['github']['audience'])
    operation['github'][field] = value
    with pytest.raises(executor.Rejected, match='not authorized'):
        executor.Executor(now=lambda: NOW).validate(contract, operation, bundle, executor.Store(tmp_path, 'rook-ceph'))


def test_actual_runtime_token_cannot_claim_infra(tmp_path, monkeypatch):
    contract, operation, bundle, _ = request(tmp_path)
    monkeypatch.setenv('EXECUTOR_AUDIENCE', operation['github']['audience'])
    operation['github'].update({k:contract['spec']['provenance'][k] for k in runtime_provenance(contract)})
    with pytest.raises(executor.Rejected, match='actual OIDC'):
        executor.Executor(now=lambda: NOW).validate(contract, operation, bundle, executor.Store(tmp_path, 'rook-ceph'))


@pytest.mark.parametrize('stages,finalize,action', [({'runtime'},True,'reconcile'),
    ({'infra'},False,'reconcile'), ({'runtime','rbac'},False,'reconcile'),
    (None,False,'reconcile'), (set(),False,'reconcile'), ({'runtime'},False,'reset')])
def test_runtime_cannot_finalize_or_select_infrastructure(tmp_path, stages, finalize, action):
    contract, operation, bundle, _ = request(tmp_path)
    operation['action'] = action
    runner = Runner()
    with pytest.raises(executor.Rejected, match='bounded partial'):
        executor.Executor(runner=runner, now=lambda: NOW).reconcile(contract, operation, bundle,
            tmp_path/'state', stages=stages, finalize=finalize, verify_signature=False)
    assert not runner.calls


@pytest.mark.parametrize('kind,api,namespace', [('Role','rbac.authorization.k8s.io/v1','rook-ceph'),
    ('CustomResourceDefinition','apiextensions.k8s.io/v1',None), ('ConfigMap','v1','other')])
def test_runtime_rejects_infra_objects_even_if_stage_mislabeled(tmp_path, monkeypatch, kind, api, namespace):
    contract, operation, bundle, manifest = request(tmp_path)
    item = {'apiVersion':api,'kind':kind,'metadata':{'name':'test'}}
    if namespace: item['metadata']['namespace'] = namespace
    identity = executor.object_identity(item)
    manifest['stages'] = [{'name':'runtime','inventory':[identity]}]
    runner = Runner()
    instance = executor.Executor(runner=runner, now=lambda: NOW)
    monkeypatch.setattr(instance, 'validate', lambda *args: (manifest,[item],'sha256:'+'a'*64))
    with pytest.raises(executor.Rejected, match='infrastructure objects'):
        instance.reconcile(contract,operation,bundle,tmp_path/'state',stages={'runtime'},finalize=False,verify_signature=False)
    assert not runner.calls


def test_runtime_partial_applies_without_accepting_state(tmp_path, monkeypatch):
    contract, operation, bundle, manifest = request(tmp_path)
    item = {'apiVersion':'apps/v1','kind':'Deployment',
            'metadata':{'name':'rook-ceph-operator','namespace':'rook-ceph'}}
    identity = executor.object_identity(item)
    manifest['stages'] = [{'name':'runtime','inventory':[identity]}]
    runner = Runner()
    instance = executor.Executor(runner=runner, now=lambda: NOW)
    monkeypatch.setattr(instance, 'validate', lambda *args: (manifest,[item],'sha256:'+'a'*64))
    result = instance.reconcile(contract,operation,bundle,tmp_path/'state',
        stages={'runtime'},finalize=False,verify_signature=False)
    assert result['outcome'] == 'healthy'
    assert len([argv for argv, _ in runner.calls if argv[:2] == ['kubectl','apply']]) == 2
    assert not (tmp_path/'state/state/rook-ceph.json').exists()
