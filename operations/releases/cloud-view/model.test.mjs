import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runtimeState, latestStatuses, MAX_AGE_MS, resourceGroups} from './model.mjs';
const now = Date.parse('2026-10-26T12:00:00Z');
const status = {sha:'abc', context:'flux/kustomization/example', state:'success', created_at:new Date(now-1000).toISOString()};
test('fresh runtime evidence is tied to exact revision', () => {
  assert.equal(runtimeState(status,'abc',now).state,'success');
  assert.equal(runtimeState(status,'def',now).state,'unknown');
});
test('expiry is based on observation, never page load or fetch time', () => {
  assert.equal(runtimeState(status,'abc',now+MAX_AGE_MS).state,'unknown');
  for (const created_at of ['invalid',new Date(now+1).toISOString()])
    assert.equal(runtimeState({...status,created_at},'abc',now).state,'unknown');
});
test('failed and incomplete observations cannot become healthy', () => {
  assert.equal(runtimeState({...status,state:'failure'},'abc',now).state,'failure');
  assert.equal(runtimeState({...status,state:'pending'},'abc',now).state,'unknown');
  assert.equal(runtimeState(null,'abc',now).state,'unknown');
});
test('new failure supersedes old success regardless of API ordering', () => {
  const failure={...status,state:'failure',created_at:new Date(now).toISOString()};
  assert.deepEqual(latestStatuses([failure,status]),[failure]);
});

test('overview counts only reported objects and expires their health', () => {
  const source={...status, context:'flux/gitrepository/main'};
  const groups=resourceGroups([status,source], 'abc', now);
  assert.equal(groups.find(g=>g.kind==='kustomization').success,1);
  assert.equal(groups.find(g=>g.kind==='gitrepository').success,1);
  assert.equal(groups.find(g=>g.kind==='helmrelease').total,0);
  const expired=resourceGroups([status], 'abc', now+MAX_AGE_MS)[0];
  assert.equal(expired.success,0);
  assert.equal(expired.unknown,1);
});
test('overview never invents resources from checks or unavailable data', () => {
  assert.ok(resourceGroups([], 'abc', now).every(g=>g.total===0 && g.success===0));
  assert.ok(resourceGroups([{...status,context:'ci/build'}], 'abc', now).every(g=>g.total===0));
});
