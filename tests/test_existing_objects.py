import json
import subprocess
from pathlib import Path
import pytest
from test_ssa_release_executor import prepare,executor,NOW,Runner,compile_bundle,canonical,digest,operation_for

class ExistingRunner(Runner):
    def __init__(self,fault=None):super().__init__();self.fault=fault;self.written=False
    def __call__(self,argv,**kwargs):
        if argv[:2]==['cosign','verify'] and self.fault=='signature':
            self.calls.append((argv,kwargs.get('input')))
            return subprocess.CompletedProcess(argv,1,'','invalid signature')
        if argv[:2]==['kubectl','get'] and 'jsonpath={.metadata.uid}{" "}{.metadata.resourceVersion}' in argv:
            self.calls.append((argv,kwargs.get('input')))
            return subprocess.CompletedProcess(argv,1 if self.fault=='missing' else 0,'' if self.fault=='missing' else 'original-uid 17','not found' if self.fault=='missing' else '')
        if argv[:2]==['kubectl','apply']:
            self.calls.append((argv,kwargs.get('input')))
            item=json.loads(kwargs['input']);assert item['metadata']['resourceVersion']=='17'
            assert item['metadata']['uid']=='original-uid'
            if '--dry-run=server' not in argv:
                if self.fault in {'deleted-after-get','version-changed'}:
                    return subprocess.CompletedProcess(argv,1,'','resourceVersion precondition failed')
                self.written=True
            return subprocess.CompletedProcess(argv,0,'','')
        return super().__call__(argv,**kwargs)

def fixture(tmp_path):
    value,op,path=prepare(tmp_path)
    value['spec']['authorization']['requireExisting']=True
    rendered='apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: demo, namespace: demo}\nspec:\n  replicas: 1\n  template:\n    spec:\n      containers:\n      - {name: demo, image: "registry.example/demo@sha256:'+('a'*64)+'"}\n'
    archive,manifest=compile_bundle(value,rendered.encode());path.write_bytes(archive)
    op['contractDigest']=digest(canonical(value));op['inventoryDigest']=manifest['inventoryDigest']
    op['bundle'].update(digest=digest(archive),payloadDigest=digest(archive))
    return value,op,path

@pytest.mark.parametrize('fault',['missing','deleted-after-get','version-changed'])
def test_adoption_never_recreates_missing_or_changed_objects(tmp_path,fault):
    value,op,path=fixture(tmp_path);runner=ExistingRunner(fault)
    with pytest.raises(executor.Rejected):executor.Executor(runner=runner,now=lambda:NOW).reconcile(value,op,path,tmp_path/'state')
    assert not runner.written
    assert not (tmp_path/'state/state/demo.json').exists()
    if fault=='missing':assert not any(c[0][:2]==['kubectl','apply'] for c in runner.calls)

def test_resource_version_precondition_is_bound_after_signature(tmp_path):
    value,op,path=fixture(tmp_path);before=path.read_bytes();runner=ExistingRunner()
    result=executor.Executor(runner=runner,now=lambda:NOW).reconcile(value,op,path,tmp_path/'state')
    assert result['outcome']=='healthy' and runner.written
    names=[c[0][0] for c in runner.calls];assert names.index('cosign')<names.index('kubectl')
    assert path.read_bytes()==before
    applies=[json.loads(data) for argv,data in runner.calls if argv[:2]==['kubectl','apply']]
    assert len(applies)==2 and all(o['metadata']['resourceVersion']=='17' for o in applies)

def test_failed_signature_never_reads_existing_resource(tmp_path):
    value,op,path=fixture(tmp_path);runner=ExistingRunner('signature')
    with pytest.raises(executor.Rejected):executor.Executor(runner=runner,now=lambda:NOW).reconcile(value,op,path,tmp_path/'state')
    assert not any(c[0][0]=='kubectl' for c in runner.calls)

def test_normal_release_without_adoption_flag_keeps_existing_behavior(tmp_path):
    value,op,path=prepare(tmp_path);runner=Runner()
    result=executor.Executor(runner=runner,now=lambda:NOW).reconcile(value,op,path,tmp_path/'state',verify_signature=False)
    assert result['outcome']=='healthy'
    assert not any('jsonpath={.metadata.uid}{" "}{.metadata.resourceVersion}' in argv for argv,_ in runner.calls)


class MovingVersionRunner(Runner):
    def __init__(self, fault=None):
        super().__init__()
        self.fault = fault
        self.reads = 0
        self.applies = 0
        self.writes = 0

    def __call__(self, argv, **kwargs):
        if argv[:2] == ['kubectl', 'get'] and 'jsonpath={.metadata.uid}{" "}{.metadata.resourceVersion}' in argv:
            self.calls.append((argv, kwargs.get('input')))
            self.reads += 1
            if self.fault == 'missing-on-retry' and self.applies:
                return subprocess.CompletedProcess(argv, 1, '', 'NotFound')
            replaced = self.fault == 'replaced-before-write' and self.reads >= 3
            replaced |= self.fault == 'replaced-on-retry' and self.applies > 0
            uid = 'replacement' if replaced else 'original'
            return subprocess.CompletedProcess(argv, 0, f'{uid} {self.reads}', '')
        if argv[:2] == ['kubectl', 'apply']:
            self.calls.append((argv, kwargs.get('input')))
            self.applies += 1
            item = json.loads(kwargs['input'])
            assert item['metadata']['resourceVersion'] == str(self.reads)
            assert item['metadata']['uid'] == 'original'
            if self.fault == 'ownership':
                return subprocess.CompletedProcess(argv, 1, '', 'error: Apply failed with 1 conflict: conflict with "other": .spec.rules')
            if self.fault in {'always-conflict', 'replaced-on-retry', 'missing-on-retry'} or (self.fault == 'once' and self.applies == 1):
                return subprocess.CompletedProcess(argv, 1, '', 'error: Operation cannot be fulfilled on deployments.apps "demo": the object has been modified; please apply your changes to the latest version and try again')
            if '--dry-run=server' not in argv:
                self.writes += 1
            return subprocess.CompletedProcess(argv, 0, '', '')
        return super().__call__(argv, **kwargs)


@pytest.mark.parametrize('fault', [None, 'once'])
def test_status_versions_refresh_per_object_without_replacing_uid(tmp_path, fault):
    value, op, path = fixture(tmp_path)
    runner = MovingVersionRunner(fault)
    result = executor.Executor(runner=runner, now=lambda: NOW).reconcile(value, op, path, tmp_path/'state')
    assert result['outcome'] == 'healthy' and runner.writes == 1
    assert runner.reads == (3 if fault is None else 4)
    assert runner.applies == (2 if fault is None else 3)


@pytest.mark.parametrize('fault,expected_applies', [
    ('always-conflict', 3), ('ownership', 1), ('replaced-before-write', 1),
    ('replaced-on-retry', 1), ('missing-on-retry', 1),
])
def test_version_retries_are_bounded_and_never_adopt_replacements(tmp_path, fault, expected_applies):
    value, op, path = fixture(tmp_path)
    runner = MovingVersionRunner(fault)
    with pytest.raises(executor.Rejected):
        executor.Executor(runner=runner, now=lambda: NOW).reconcile(value, op, path, tmp_path/'state')
    assert runner.applies == expected_applies
    assert runner.writes == 0
    assert not (tmp_path/'state/state/demo.json').exists()


def test_health_failure_cannot_rollback_onto_replacement_uid(tmp_path, monkeypatch):
    value, op, path = fixture(tmp_path)
    runner = MovingVersionRunner()
    instance = executor.Executor(runner=runner, now=lambda: NOW)
    root = tmp_path / "state"
    instance.reconcile(value, op, path, root)
    manifest, _ = executor.load_archive(path)
    next_op = operation_for(value, path.read_bytes(), manifest, 2, previous=op["bundle"]["digest"])
    def replaced_during_health(*args):
        runner.fault = "replaced-before-write"
        return False, "object replaced during health wait"
    monkeypatch.setattr(instance, "wait_healthy", replaced_during_health)
    with pytest.raises(executor.Rejected, match="rollback failed.*missing or replaced"):
        instance.reconcile(value, next_op, path, root)
    assert runner.writes == 2  # Initial publish + attempted update; no rollback write.
    assert runner.applies == 4
    assert executor.Store(root, "demo").state()["blocked"] is True


def test_rollback_rejects_objects_outside_initial_adoption_snapshot(tmp_path):
    value, op, path = fixture(tmp_path)
    manifest, objects = executor.load_archive(path)
    store = executor.Store(tmp_path / "state", "demo")
    store.cache_bundle(path, op["bundle"]["digest"])
    previous = {"appliedDigest": op["bundle"]["digest"]}
    runner = MovingVersionRunner()
    with pytest.raises(executor.Rejected, match="not bound in this adoption attempt"):
        executor.Executor(runner=runner, now=lambda: NOW).rollback(
            previous, manifest, [], value, op, "re8ch-release/demo", store, "health failed")
    assert runner.reads == runner.applies == runner.writes == 0
