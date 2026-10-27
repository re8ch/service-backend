import importlib.util
import json
import io
import zipfile
import pytest
from pathlib import Path
import urllib.error
import yaml

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec=importlib.util.spec_from_file_location(name, ROOT/'operations/releases/cloud-view'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

collect=load('collect')
access=load('access')


def test_complete_application_can_have_unhealthy_service():
    result={'outcome':'failed','application':{'status':'complete'},'finishedAt':'2026-10-28T01:01:00Z','releaseId':'example','operationId':'operation'}
    assert collect.sync_state({},result,{'status':'failed','observedAt':'2026-10-28T02:00:00Z','_results':[result]})=='synced'
    assert collect.health_state({'kind':'DaemonSet','metadata':{'generation':2},'status':{'observedGeneration':2,'desiredNumberScheduled':12,'numberReady':9,'updatedNumberScheduled':12}})=='unhealthy'


def test_later_api_failure_never_inherits_previous_success():
    state={'updatedAt':'2026-10-28T01:00:00Z'}
    result={'outcome':'healthy','approvedDigest':'sha256:old','observedDigest':'sha256:old','finishedAt':state['updatedAt']}
    assert collect.sync_state(state,result,{'status':'failed','observedAt':'2026-10-28T02:00:00Z'})=='unknown'
    assert collect.sync_state(state,result,{'status':'unknown','observedAt':None})=='unknown'
    assert collect.sync_state(state,result,{'status':'succeeded','observedAt':'2026-10-28T02:00:00Z'})=='synced'


def test_partial_application_and_rollback_are_distinct():
    op={'status':'failed','observedAt':'2026-10-28T01:00:00Z'}
    result={'outcome':'failed','application':{'status':'partial-or-unknown'},'finishedAt':'2026-10-28T01:01:00Z','releaseId':'example','operationId':'operation'}
    op['_results']=[result]
    assert collect.sync_state({},result,op)=='failed'
    rollback=dict(result,outcome='rolled-back')
    op['_results']=[rollback]
    assert collect.sync_state({},rollback,op)=='drifted'


def test_collection_failure_separates_observation_and_live_health_and_deduplicates():
    now='2026-10-28T01:00:00Z'
    ops={d:{'status':'succeeded','observedAt':now} for d in collect.DOMAINS}
    w={'kind':'Deployment','namespace':'example','name':'example'}
    entries=[{'domain':'main','namespace':'example','name':name,'workloads':[w]} for name in ['a','b']]
    class Cluster:
        def get(self,kind,namespace,name):
            if kind=='ConfigMap':raise RuntimeError('SECRET API response must never be printed')
            return {'kind':kind,'metadata':{'generation':1},'spec':{'replicas':1},'status':{'observedGeneration':1,'readyReplicas':1,'updatedReplicas':1,'availableReplicas':1}}
    domains=collect.summarize(entries,Cluster(),ops,now)
    assert domains[0]['sync']['unknown']==2
    assert domains[0]['health']['healthy']==1
    assert domains[0]['observation']['status']=='unavailable'
    assert 'SECRET' not in json.dumps(domains)


def test_generated_access_matches_current_authorities_and_cannot_read_secrets():
    entries=access.inventory()
    assert entries==json.loads((ROOT/'operations/releases/cloud-view/observation-inventory.json').read_text())
    assert not any(e['name']=='ssa-state-main-commerce-billing-payments' for e in entries)
    assert not any(e['name']=='ssa-state-identity-access-domain-mail-system' for e in entries)
    assert any(e['name']=='ssa-state-agent-runtime-domain-mail-system' for e in entries)
    docs=list(yaml.safe_load_all((ROOT/'clusters/re8ch/components/ops/github-actions-node-observer-rbac.yaml').read_text()))
    assert docs[2:]==access.rbac(entries)
    for obj in docs[2:]:
        for rule in obj.get('rules',[]):
            assert rule['verbs']==['get'] and rule['resourceNames']
            assert set(rule['resources']) <= {'configmaps','deployments','statefulsets','daemonsets'}
    assert 'toolkit.fluxcd.io' not in json.dumps(docs)


def test_blocked_state_never_claims_synchronized_from_old_complete_result():
    assert collect.sync_state({'blocked':True},{'application':{'status':'complete'}},{'status':'succeeded','observedAt':None})=='failed'


def test_old_run_rerun_is_selected_by_update_not_creation(monkeypatch):
    monkeypatch.setenv('AGENT_OBSERVER_TOKEN','PRIVATE')
    runs=[{'id':1,'created_at':'2026-10-20T00:00:00Z','updated_at':'2026-10-28T02:00:00Z','status':'completed','conclusion':'failure'},
          {'id':2,'created_at':'2026-10-28T00:00:00Z','updated_at':'2026-10-28T01:00:00Z','status':'completed','conclusion':'success'}]
    monkeypatch.setattr(collect,'request_json',lambda *args: {'total_count':2,'workflow_runs':runs})
    assert collect.operation('agent-runtime')['status']=='failed'
    monkeypatch.setattr(collect,'request_json',lambda *args: {'total_count':1001,'workflow_runs':runs})
    assert collect.operation('agent-runtime')=={'status':'unknown','observedAt':None}


def archive(**files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name, value in files.items():
            z.writestr(name,value)
    return out.getvalue()


def full_result():
    return {'apiVersion':'delivery.re8ch.com/release-result/v1alpha1',
            'releaseId':'example','operationId':'operation','outcome':'failed',
            'application':{'status':'complete'},'reason':'PRIVATE'}


def test_failed_run_requires_exact_result_not_same_time_or_old_operation():
    result=full_result()
    op={'status':'failed','observedAt':None,'_results':[result]}
    assert collect.sync_state({},result,op)=='synced'
    assert collect.sync_state({},dict(result,operationId='old'),op)=='unknown'
    assert collect.sync_state({},dict(result,releaseId='other'),op)=='unknown'
    assert collect.sync_state({},dict(result,application={'status':'partial-or-unknown'}),op)=='unknown'
    assert collect.sync_state({},result,dict(op,status='running'))=='unknown'


def test_private_run_result_is_not_exported_in_aggregate():
    result=full_result()
    ops={d:{'status':'failed','observedAt':None,'_results':[result]} for d in collect.DOMAINS}
    class Cluster:
        def get(self,*args):
            return {'data':{'last-result.json':json.dumps(result)}}
    summary=collect.summarize([{'domain':'main','namespace':'example','name':'state','workloads':[]}],Cluster(),ops,'now')
    assert summary[0]['sync']['synced']==1
    assert 'PRIVATE' not in json.dumps(summary) and '_results' not in json.dumps(summary)


def test_archive_limits_and_full_result_only():
    result=full_result()
    assert collect.artifact_result(archive(**{'result.json':json.dumps(result)}))==result
    assert collect.artifact_result(archive(**{'result.json':json.dumps(dict(result,apiVersion='delivery.re8ch.com/stage-result/v1alpha1'))})) is None
    assert collect.artifact_result(archive(**{'nested/result.json':json.dumps(result)})) is None
    assert collect.artifact_result(archive(**{'result.json':json.dumps(result),'static-release-result.json':json.dumps(result)})) is None
    with pytest.raises(ValueError):
        collect.artifact_result(b'x'*(collect.MAX_ARCHIVE+1))
    with pytest.raises(ValueError):
        collect.artifact_result(archive(**{str(i):'' for i in range(17)}))
    with pytest.raises(ValueError):
        collect.artifact_result(archive(**{'result.json':'x'*(collect.MAX_ARCHIVE+1)}))


def test_only_current_run_full_result_artifacts_and_no_ambiguous_rerun(monkeypatch):
    names=['static-release-result-42','ceph-infra-42','ceph-runtime-42','ceph-accept-42','static-release-result-41']
    artifacts=[{'id':i,'name':name,'expired':False,'size_in_bytes':100,'workflow_run':{'id':42,'head_sha':'abc'}} for i,name in enumerate(names)]
    monkeypatch.setattr(collect,'request_json',lambda *a:{'total_count':len(artifacts),'artifacts':artifacts})
    downloaded=[]
    def download(repo,artifact_id,token):
        downloaded.append(artifact_id)
        return archive(**{'result.json':json.dumps(full_result())})
    monkeypatch.setattr(collect,'download_artifact',download)
    assert len(collect.run_results('re8ch/service-backend',{'id':42,'run_attempt':1,'head_sha':'abc'},'TOKEN'))==2
    assert downloaded==[0,3]
    artifacts[0]['workflow_run']['head_sha']='wrong'
    artifacts[3]['workflow_run']['id']=41
    downloaded.clear()
    assert collect.run_results('re8ch/service-backend',{'id':42,'run_attempt':1,'head_sha':'abc'},'TOKEN')==[]
    assert downloaded==[]
    assert collect.run_results('re8ch/service-backend',{'id':42,'run_attempt':2},'TOKEN')==[]


def test_artifact_redirect_never_forwards_observer_token(monkeypatch):
    requests=[]
    class Opener:
        def open(self,req,timeout):
            requests.append(req)
            if len(requests)==1:
                raise urllib.error.HTTPError(req.full_url,302,'redirect',{'Location':'https://storage.example/signed?sig=PRIVATE'},None)
            return io.BytesIO(b'zip')
    monkeypatch.setattr(collect.urllib.request,'build_opener',lambda *a:Opener())
    assert collect.download_artifact('re8ch/service-backend',1,'TOKEN')==b'zip'
    assert requests[0].get_header('Authorization')=='Bearer TOKEN'
    assert requests[1].get_header('Authorization') is None
    assert collect.NoRedirect().redirect_request(None,None,302,'',{},'http://unsafe') is None


def test_artifact_rejects_insecure_redirect(monkeypatch):
    class Opener:
        def open(self,req,timeout):
            raise urllib.error.HTTPError(req.full_url,302,'redirect',{'Location':'http://storage.example/signed'},None)
    monkeypatch.setattr(collect.urllib.request,'build_opener',lambda *a:Opener())
    with pytest.raises(ValueError):
        collect.download_artifact('re8ch/service-backend',1,'TOKEN')
