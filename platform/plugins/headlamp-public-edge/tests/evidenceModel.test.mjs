import { test } from 'node:test';
import assert from 'node:assert/strict';
import { backendState, candidateState, qualityState, federationDetails } from '../src/evidenceModel.ts';
const now = Date.parse('2026-11-23T01:00:00Z');
const node = { status: { conditions: [{ type: 'Ready', status: 'True' }] } };
function edge() {
  return { metadata: { generation: 3 }, spec: { enabled: true, nodeName: 'remote', discovery: {
    source: 'AmongCluster', clusterRef: 're8ch', serviceRef: 'spiffe://re8ch.internal/ns/test/service/api', endpointId: 'primary',
    advertisementRef: { uid: 'ad' }, importedServiceRef: { uid: 'import' },
    assessmentRefs: { 'domain-ex': { uid:'npa', sourceNode:'domain-ex' } },
  } }, status: { observedAt: new Date(now).toISOString(), observedGeneration: 3,
    services: { tools: { ready: true } }, backendConditions: [{ type: 'ServiceBackendReady', status: 'True' }],
    networkEvidence: { eligible: true, validUntil:new Date(now + 120000).toISOString(), assessmentRef: { uid: 'npa', scope: { sourceNode: 'domain-ex' } },
      federation: { eligible: true, publisherRef: 're8ch', serviceIdentity: 'spiffe://re8ch.internal/ns/test/service/api',
        endpointId: 'primary', expiresAt: now / 1000 + 90, advertisementRef: { uid: 'ad' }, importedServiceRef: { uid: 'import' } } } } };
}
test('typed remote status uses published identity and evidence, not a local Node lookup', () => {
  const value = edge();
  assert.equal(candidateState(value, undefined, false, now), 'Ready');
  assert.equal(federationDetails(value).source, 'domain-ex');
});
test('legacy federation and absent scoped evidence remain Unknown', () => {
  const value = edge(); delete value.spec.discovery.advertisementRef;
  assert.match(candidateState(value, node, true, now), /^Unknown/);
  const scoped = edge(); delete scoped.status.networkEvidence.assessmentRef;
  assert.match(candidateState(scoped, node, true, now), /^Unknown/);
});
test('expiry, UID replacement and stale generation cannot reuse old Ready', () => {
  const value = edge();
  assert.equal(candidateState(value, undefined, true, now + 91000), 'Stale evidence');
  value.status.observedAt = new Date(now + 91000).toISOString();
  assert.equal(candidateState(value, undefined, true, now + 91000), 'Federation expired');
  value.status.observedAt = new Date(now).toISOString();
  value.spec.discovery.advertisementRef.uid = 'replacement';
  assert.match(candidateState(value, undefined, true, now), /identity mismatch/);
  value.status.observedGeneration = 2;
  assert.equal(candidateState(value, undefined, true, now), 'Old generation');
});
test('real remote rejection and local Node failures remain visible', () => {
  const value = edge(); value.status.networkEvidence.eligible = false; value.status.networkEvidence.reason = 'ServiceTransferEvidenceRejected';
  assert.equal(candidateState(value, undefined, true, now), 'ServiceTransferEvidenceRejected');
  delete value.spec.discovery;
  assert.equal(candidateState(value, undefined, true, now), 'Node missing');
  assert.match(candidateState(value, undefined, false, now), /^Unknown/);
  assert.equal(candidateState(value, {status:{}}, true, now), 'Node NotReady');
});
test('GlobalPing 0/0 and unfinished observations are not measured failures', () => {
  assert.match(qualityState({ probes:0, finished:0, ready:false }), /^Unknown/);
  assert.match(qualityState({ probes:3, finished:0, ready:false }), /^Unknown/);
  assert.equal(qualityState({ probes:3, finished:2, ready:false }), 'Pending probes');
  assert.equal(qualityState({ probes:3, finished:3, ready:false }), 'Problem');
});
test('remote EndpointSlice without local Pod stays Unknown; real local failures stay failed', () => {
  const refs = [{name:'api',namespace:'test'}];
  const slice = {metadata:{namespace:'test',labels:{'kubernetes.io/service-name':'api'}},endpoints:[{addresses:['192.0.2.1'],conditions:{ready:true}}]};
  assert.match(backendState(refs,[slice],new Map()), /^Unknown: remote/);
  slice.endpoints[0].targetRef = {kind:'Pod'};
  assert.equal(backendState(refs,[slice],new Map([['192.0.2.1',{status:{}}]])), 'Backend Pod NotReady');
  assert.equal(backendState(refs,[slice],new Map([['192.0.2.1',node]])), 'Ready');
  assert.match(backendState(refs,[],new Map(),false), /^Unknown/);
});

test('fresh top-level status cannot hide expired or replaced AF evidence', () => {
 const value = edge(); value.status.networkEvidence.validUntil = new Date(now - 1).toISOString();
 assert.equal(candidateState(value, undefined, true, now), 'Network evidence expired');
 value.status.networkEvidence.validUntil = new Date(now + 10000).toISOString();
 value.spec.discovery.assessmentRefs['domain-ex'].uid = 'replacement';
 assert.match(candidateState(value, undefined, true, now), /assessment reference mismatch/);
});
