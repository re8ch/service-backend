import copy
import importlib.util
from pathlib import Path
import pytest

p = Path(__file__).resolve().parents[1] / 'operations/infrastructure-contract/recovery/network_window.py'
spec = importlib.util.spec_from_file_location('network_window', p)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def observed():
    return {'items': [{'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':n,'namespace':'flux-system','uid':n+'-uid','resourceVersion':'12'},'spec':s} for n,s in m.baseline().items()]}

def test_open_and_close_modify_only_task_selector_and_require_cas():
    a=observed(); opened=m.plan(a,'open')
    for n,p in opened['patches'].items():
        assert [x['op'] for x in p]==['test','test','test','replace']
        assert p[-1]['path']=='/spec/podSelector' and p[-1]['value']==m.SELECTOR
    for o in a['items']:
        o['spec']['podSelector']=copy.deepcopy(m.SELECTOR)
        o['metadata']['resourceVersion']='13'
    closed=m.plan(a,'close',opened['receipt'])
    assert all(p[-1]['value']=={} and p[1]['value']=='13' for p in closed['patches'].values())

def test_notin_preserves_unlabelled_and_unrelated_pods():
    expression=m.SELECTOR['matchExpressions'][0]
    assert expression['operator']=='NotIn'
    for labels in [{},{'app':'other'},{'control-plane':'controller'}]:
        assert labels.get('app') not in expression['values']
    assert all(x in expression['values'] for x in m.APPS)

@pytest.mark.parametrize('change',['uid','rules','namespace','extra'])
def test_close_refuses_recreated_or_changed_policy(change):
    a=observed(); receipt=m.plan(a,'open')['receipt']
    for o in a['items']:o['spec']['podSelector']=copy.deepcopy(m.SELECTOR)
    o=a['items'][0]
    if change=='uid':o['metadata']['uid']='recreated'
    elif change=='rules':o['spec']['ingress']=[]
    elif change=='selector':o['spec']['podSelector']={}
    elif change=='namespace':o['metadata']['namespace']='other'
    else:a['items'].append(copy.deepcopy(o))
    with pytest.raises(m.Rejected):m.plan(a,'close',receipt)

def test_open_refuses_any_existing_selector_and_close_requires_receipt():
    a=observed();a['items'][0]['spec']['podSelector']={'matchLabels':{'other':'label'}}
    with pytest.raises(m.Rejected):m.plan(a,'open')
    with pytest.raises(m.Rejected):m.plan(observed(),'close')


def test_close_recovers_partial_open_without_reopening_original_policy():
    a=observed(); receipt=m.plan(a,'open')['receipt']
    a['items'][0]['spec']['podSelector']=copy.deepcopy(m.SELECTOR)
    result=m.plan(a,'close',receipt)
    assert set(result['patches']) == {a['items'][0]['metadata']['name']}
