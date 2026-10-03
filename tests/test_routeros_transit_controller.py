import copy
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).parents[1]
DIRECTORY = ROOT / 'clusters/re8ch/components/network/host-network-controller'
SPEC = importlib.util.spec_from_file_location('transit', DIRECTORY / 'routeros_transit.py')
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


class Router:
    def __init__(self, spec):
        self.tables = {
            '/interface/wireguard/peers': [{'.id':str(i),'interface':x['interface'],'public-key':x['peerPublicKey'],
                'allowed-address':','.join([x['peerAddress'],x['identity'],x['podCIDR'],x['probeIdentity']])} for i,x in enumerate(spec['links'])],
            '/ip/firewall/filter': [{'.id':'anchor','comment':'defconf: fasttrack'}],
            '/ip/firewall/address-list': []}
        self.calls = []
    def request(self, path, method='GET', body=None):
        self.calls.append((path,method,body))
        if path.endswith('/move'):
            return {}
        if method == 'GET':
            return copy.deepcopy(self.tables[path])
        if method == 'PUT':
            row = {'.id':'new'+str(len(self.calls)),**body}
            self.tables[path].append(row)
            return row
        table, key = path.rsplit('/',1)
        row = next(x for x in self.tables[table] if x['.id']==key)
        if method == 'PATCH':row.update(body)
        elif method == 'DELETE':self.tables[table].remove(row)
        else:raise AssertionError(method)


def spec():
    return json.loads((DIRECTORY/'routeros-transit.json').read_text())


def test_complete_transit_sources_and_idempotence():
    s=spec();r=Router(s)
    first=a.reconcile(r,s)
    assert first['forwardRules']==4
    for peer in r.tables['/interface/wireguard/peers'][:2]:
        assert {'192.0.2.109/32','192.0.2.254/24','192.0.2.59/32'} <= set(peer['allowed-address'].split(','))
    assert a.reconcile(r,s)['changes']==0
    assert all('/route' not in path and '/nat' not in path for path,_,_ in r.calls)


def test_removing_source_preserves_control_baseline():
    s=spec();r=Router(s);a.reconcile(r,s)
    s['sources'].remove('192.0.2.109/32');a.reconcile(r,s)
    for i,peer in enumerate(r.tables['/interface/wireguard/peers'][:2]):
        allowed=set(peer['allowed-address'].split(','))
        assert '192.0.2.109/32' not in allowed
        assert s['links'][i]['identity'] in allowed
        assert s['links'][i]['podCIDR'] in allowed


def test_unknown_peer_fails_before_mutation():
    s=spec();r=Router(s)
    r.tables['/interface/wireguard/peers'][1]['public-key']='unreviewed'
    with pytest.raises(ValueError,match='unknown'):a.reconcile(r,s)
    assert all(method=='GET' for _,method,_ in r.calls)


def test_default_route_not_valid_source():
    s=spec();s['sources'].append('0.0.0.0/0');r=Router(s)
    with pytest.raises(ValueError,match='outside'):a.reconcile(r,s)
    assert r.calls==[]
