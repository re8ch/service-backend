import test from 'node:test';
import assert from 'node:assert/strict';
import {aggregate,DOMAINS} from './snapshot.mjs';
const now=Date.now(),at=new Date(now-1000).toISOString();
const identity={repository:'re8ch/service-backend',sha:'a'.repeat(40),batch:'1-1'};
function input(){return {schema:2,...identity,observedAt:at,complete:true,domains:DOMAINS.map(id=>({id,sync:{synced:1,drifted:0,failed:0,unknown:0},health:{healthy:0,unhealthy:1,unknown:0},observation:{status:'current',observedAt:at},operations:{status:'succeeded',observedAt:at}}))};}
test('only bounded aggregate fields cross the public boundary',()=>{
  const x=input();x.privateTopology='SECRET';x.domains[0].resources=[{password:'SECRET'}];
  const out=aggregate(x,identity,now);assert.equal(out.complete,true);
  assert.equal(out.domains[0].sync.synced,1);assert.equal(out.domains[0].health.unhealthy,1);
  assert.ok(!JSON.stringify(out).includes('SECRET'));
});
test('missing, stale, mixed identity and invalid counts publish unavailable',()=>{
  const bad=[undefined,{...input(),observedAt:new Date(now-900001).toISOString()},{...input(),sha:'b'.repeat(40)}];
  const x=input();x.domains[0].sync.failed=-1;bad.push(x);
  const duplicate=input();duplicate.domains[1].id='main';bad.push(duplicate);
  for(const x of bad){const out=aggregate(x,identity,now);assert.equal(out.complete,false);assert.deepEqual(out.domains,[]);}
});
