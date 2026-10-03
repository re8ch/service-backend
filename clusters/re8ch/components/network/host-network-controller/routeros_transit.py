#!/usr/bin/env python3
"""Reconcile RouterOS source acceptance for declared transit links; no routes/NAT."""
import base64
import ipaddress
import json
from pathlib import Path
import time
import urllib.request


def validate(spec):
    if spec['managementIp'] != '192.0.2.131' or spec['publicEndpoint'] != '192.0.2.79':
        raise ValueError('RouterOS authority differs')
    if spec['addressList'] != 're8ch-fabric-transit-sources' or spec['commentPrefix'] != 'RE8CH FABRIC TRANSIT':
        raise ValueError('ownership prefix differs')
    links = {x['node']: x for x in spec['links']}
    if set(links) != {'restricted-node-1eb0570f74','restricted-node-296f36f2fb','restricted-node-abb3f27a69'} or set(spec['ingressNodes']) != {'restricted-node-1eb0570f74','restricted-node-296f36f2fb'}:
        raise ValueError('unexpected transit topology')
    for prefix in spec['sources']:
        n = ipaddress.ip_network(prefix)
        if not (n.subnet_of(ipaddress.ip_network('192.0.2.106/24')) and n.prefixlen == 32 or
                n.subnet_of(ipaddress.ip_network('192.0.2.224/16')) and n.prefixlen == 24):
            raise ValueError('source outside cluster authority')
    if not spec['sources']:
        raise ValueError('empty transit source declaration')
    return links


class Router:
    def __init__(self, address):
        user = Path('/credential/username').read_bytes().strip()
        password = Path('/credential/password').read_bytes()
        self.auth = base64.b64encode(user+b':'+password).decode()
        self.base = 'http://' + address + '/rest'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, method='GET', body=None):
        r = urllib.request.Request(self.base+path, method=method,
            headers={'Authorization':'Basic '+self.auth, 'Content-Type':'application/json'},
            data=json.dumps(body).encode() if body is not None else None)
        with self.opener.open(r, timeout=10) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}


def reconcile(router, spec):
    links = validate(spec)
    peers = router.request('/interface/wireguard/peers')
    filters = router.request('/ip/firewall/filter')
    lists = router.request('/ip/firewall/address-list')
    anchor = next((x for x in filters if x.get('comment') == 'defconf: fasttrack'), None)
    if not anchor:
        raise ValueError('firewall anchor missing')
    selected = {}
    for name in spec['ingressNodes']:
        link = links[name]
        matches = [x for x in peers if x.get('interface') == link['interface']]
        if len(matches) != 1 or matches[0].get('public-key') != link['peerPublicKey']:
            raise ValueError('unknown/multiple WireGuard peers on declared interface')
        peer = matches[0]
        baseline = {link['peerAddress'],link['identity'],link['podCIDR'],link['probeIdentity']}
        if not baseline <= set(peer['allowed-address'].split(',')):
            raise ValueError('control-plane baseline missing')
        selected[name] = peer
    owned_list = [x for x in lists if x.get('list') == spec['addressList']]
    if any(x.get('comment') != spec['commentPrefix'] for x in owned_list):
        raise ValueError('address list ownership conflict')
    old_sources = {x['address'] for x in owned_list}
    wanted = set(spec['sources'])
    changes = 0
    # Keep each link's control baseline; mutate only formerly/newly declared
    # transit sources. Separate WG interfaces avoid cryptokey peer collisions.
    for name, peer in selected.items():
        allowed = set(peer['allowed-address'].split(','))
        target = (allowed - old_sources) | wanted
        link = links[name]
        target |= {link['peerAddress'],link['identity'],link['podCIDR'],link['probeIdentity']}
        if target != allowed:
            router.request('/interface/wireguard/peers/'+peer['.id'], 'PATCH', {'allowed-address':','.join(sorted(target))})
            changes += 1
    for prefix in sorted(wanted-old_sources):
        router.request('/ip/firewall/address-list', 'PUT', {'list':spec['addressList'], 'address':prefix,'comment':spec['commentPrefix']})
        changes += 1
    expected = set()
    for source in spec['ingressNodes']:
        for target, link in links.items():
            if target == source:
                continue
            comment = spec['commentPrefix']+' '+source+' to '+target
            expected.add(comment)
            body = {'comment':comment, 'chain':'forward','action':'accept','disabled':'false',
                    'in-interface':links[source]['interface'],'out-interface':link['interface'],
                    'src-address-list':spec['addressList'],'dst-address':link['podCIDR']}
            prior = [x for x in filters if x.get('comment') == comment]
            if len(prior) > 1:
                raise ValueError('duplicate owned firewall rule')
            if not prior:
                row = router.request('/ip/firewall/filter', 'PUT', body)
                changes += 1
            else:
                row = prior[0]
                if any(row.get(k) != v for k,v in body.items()):
                    router.request('/ip/firewall/filter/'+row['.id'], 'PATCH', body)
                    changes += 1
            # RouterOS evaluates the exact transit allow before its generic rules.
            router.request('/ip/firewall/filter/move','POST',{'numbers':row['.id'],'destination':anchor['.id']})
    for row in owned_list:
        if row['address'] not in wanted:
            router.request('/ip/firewall/address-list/'+row['.id'],'DELETE')
            changes += 1
    for row in filters:
        if row.get('comment','').startswith(spec['commentPrefix']+' ') and row['comment'] not in expected:
            router.request('/ip/firewall/filter/'+row['.id'],'DELETE')
            changes += 1
    return {'changes':changes,'ingressNodes':spec['ingressNodes'],'sources':len(wanted),'forwardRules':len(expected)}


def main():
    while True:
        try:
            spec = json.loads(Path('/config/routeros-transit.json').read_text())
            result = reconcile(Router(spec['managementIp']),spec)
            Path('/tmp/routeros-transit-ready').touch()
            print(json.dumps({'state':'reconciled',**result}),flush=True)
        except Exception as exc:
            Path('/tmp/routeros-transit-ready').unlink(missing_ok=True)
            # Do not print HTTP bodies or credentials.
            print(json.dumps({'state':'failed','errorType':type(exc).__name__}),flush=True)
        time.sleep(30)


if __name__ == '__main__':
    main()
