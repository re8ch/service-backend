import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import pytest
import yaml
from test_ssa_release_executor import prepare, rendered, executor, NOW, canonical, digest, compile_bundle

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('selected', ['sha256:'+'a'*64, 'https://other.example/release@sha256:'+'a'*64, 'sha256:abc', '$(touch injected)'])
def test_reuse_shell_uses_fixed_reference_and_never_builds_or_signs(tmp_path,selected):
    jobs=yaml.safe_load((ROOT/'.github/workflows/main-domain-release.yml').read_text())['jobs']
    job=jobs['compile-sign']; assert "inputs.mode != 'reconcile'" in job['if']
    step=next(s for s in job['steps'] if s.get('id')=='publish')
    source=next(ROOT.glob('contracts/releases/main-domain/*cluster-bootstrap.yaml')); contract=yaml.safe_load(source.read_text());spec=contract['spec']
    (tmp_path/'operations/releases').mkdir(parents=True);shutil.copyfile(ROOT/'operations/releases/verify_reuse_source.py',tmp_path/'operations/releases/verify_reuse_source.py')
    shutil.copytree(ROOT/spec['source']['path'],tmp_path/spec['source']['path'])
    (tmp_path/'contracts/releases/main-domain').mkdir(parents=True);(tmp_path/'contracts/releases/main-domain/current.yaml').write_text(source.read_text())
    script='if ! type readarray >/dev/null 2>&1; then readarray() { fields=(); while IFS= read -r line; do fields+=("$line"); done; }; fi\n'+step['run']
    for key,value in {'audience':spec['provenance']['audience'],'environment':spec['provenance']['environment'],'domain':spec['authorityDomain'],'signer':spec['provenance']['signer']}.items():
        script=script.replace('${{ needs.validate.outputs.'+key+' }}',value)
    bins=tmp_path/'bin';bins.mkdir();(bins/'python').symlink_to(sys.executable)
    for name in ['kubectl','flux','cosign','git']:
        p=bins/name;p.write_text('#!/bin/sh\nprintf "%s\\n" "'+name+' $*" >> "$CALLS"\n'+('exit 0\n' if name in {'cosign','git'} else 'exit 73\n'));p.chmod(0o755)
    calls=tmp_path/'calls'; output=tmp_path/'output'
    result=subprocess.run(['bash','-c',script],cwd=tmp_path,capture_output=True,text=True,env={**os.environ,'PATH':str(bins)+os.pathsep+os.environ['PATH'],'CONTRACT':'contracts/releases/main-domain/current.yaml','REUSE_DIGEST':selected,'REUSE_SOURCE_COMMIT':'b'*40,'RUNNER_TEMP':str(tmp_path),'GITHUB_OUTPUT':str(output),'CALLS':str(calls)})
    if selected=='sha256:'+'a'*64:
        assert result.returncode==0,result.stderr
        reference=f"ghcr.io/{spec['provenance']['repository']}-release/{contract['metadata']['name']}"
        assert 'cosign verify ' in calls.read_text()
        assert '--certificate-github-workflow-sha '+'b'*40 in calls.read_text()
        assert reference+'@'+selected in calls.read_text()
        assert ' sign ' not in calls.read_text() and 'kubectl' not in calls.read_text() and 'flux ' not in calls.read_text()
        assert f'digest={selected}' in output.read_text()
    else:
        assert result.returncode!=0 and not calls.exists() and not output.exists()
    assert not (tmp_path/'injected').exists()

@pytest.mark.parametrize('mismatch',['release','contract'])
def test_reused_payload_stays_bound_to_current_release_and_contract(tmp_path,mismatch):
    value,operation,path=prepare(tmp_path)
    if mismatch=='release':
        foreign=copy.deepcopy(value);foreign['metadata']['name']='another-release'
        archive,_=compile_bundle(foreign,rendered());path.write_bytes(archive)
        operation['bundle']['payloadDigest']=digest(archive)
    else:
        value['spec']['source']['revision']='c'*40
        operation['contractDigest']=digest(canonical(value))
    with pytest.raises(executor.Rejected,match='another release|another contract'):
        executor.Executor(now=lambda:NOW).validate(value,operation,path,executor.Store(tmp_path/'state','demo'))

@pytest.fixture
def source_repo(tmp_path):
    import verify_reuse_source
    root=tmp_path/'repo';root.mkdir()
    def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
    git('init','-q');git('config','user.email','test@example.invalid');git('config','user.name','Fixture');git('config','commit.gpgsign','false')
    (root/'contracts').mkdir();(root/'releases/demo').mkdir(parents=True)
    (root/'contracts/demo.yaml').write_text(yaml.safe_dump({'spec':{'source':{'path':'releases/demo'}}}))
    (root/'releases/demo/kustomization.yaml').write_text('resources: [resources.yaml]\n')
    (root/'releases/demo/resources.yaml').write_text('kind: ConfigMap\n')
    git('add','.');git('commit','-qm','fixture');commit=git('rev-parse','HEAD')
    return root,commit,git,verify_reuse_source.verify

def test_reuse_accepts_unrelated_main_changes(source_repo):
    root,commit,git,verify=source_repo
    (root/'README.md').write_text('unrelated');git('add','.');git('commit','-qm','unrelated')
    verify(commit,'contracts/demo.yaml',root)

@pytest.mark.parametrize('fault',['content','contract','unknown','ref','external','symlink'])
def test_reuse_rejects_changed_source_missing_commit_and_external_dependencies(source_repo,fault):
    root,commit,git,verify=source_repo
    if fault=='content':(root/'releases/demo/resources.yaml').write_text('kind: Service\n')
    if fault=='contract':(root/'contracts/demo.yaml').write_text(yaml.safe_dump({'spec':{'source':{'path':'releases/demo'},'changed':True}}))
    if fault=='unknown':commit='f'*40
    if fault=='ref':commit='HEAD'
    if fault=='external':(root/'releases/demo/kustomization.yaml').write_text('resources: [resources.yaml]\ncomponents: [../../external]\n')
    if fault=='symlink':(root/'releases/demo/link').symlink_to('/tmp')
    git('add','.');subprocess.run(['git','commit','-qm','change'],cwd=root,capture_output=True)
    with pytest.raises(ValueError):verify(commit,'contracts/demo.yaml',root)
