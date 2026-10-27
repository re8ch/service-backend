import importlib.util
import json
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
    result={'outcome':'failed','application':{'status':'complete'},'finishedAt':'2026-10-28T01:01:00Z'}
    assert collect.sync_state({},result,{'status':'failed','observedAt':'2026-10-28T01:00:00Z'})=='synced'
    assert collect.health_state({'kind':'DaemonSet','metadata':{'generation':2},'status':{'observedGeneration':2,'desiredNumberScheduled':12,'numberReady':9,'updatedNumberScheduled':12}})=='unhealthy'


def test_later_api_failure_never_inherits_previous_success():
    state={'updatedAt':'2026-10-28T01:00:00Z'}
    result={'outcome':'healthy','approvedDigest':'sha256:old','observedDigest':'sha256:old','finishedAt':state['updatedAt']}
    assert collect.sync_state(state,result,{'status':'failed','observedAt':'2026-10-28T02:00:00Z'})=='unknown'
    assert collect.sync_state(state,result,{'status':'unknown','observedAt':None})=='unknown'
    assert collect.sync_state(state,result,{'status':'succeeded','observedAt':'2026-10-28T02:00:00Z'})=='synced'


def test_partial_application_and_rollback_are_distinct():
    op={'status':'failed','observedAt':'2026-10-28T01:00:00Z'}
    result={'outcome':'failed','application':{'status':'partial-or-unknown'},'finishedAt':'2026-10-28T01:01:00Z'}
    assert collect.sync_state({},result,op)=='failed'
    assert collect.sync_state({},dict(result,outcome='rolled-back'),op)=='drifted'


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
