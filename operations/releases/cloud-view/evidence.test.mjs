import test from 'node:test';
import assert from 'node:assert/strict';
import {project,stateOf,readIndex,readDomain,evidenceItems,TTL,EXECUTOR_APP_ID} from './evidence.mjs';
const sha='a'.repeat(40), observedAt='2026-10-26T00:00:00.000Z', now=Date.parse(observedAt);
const domain={id:'main',checkRunId:42,count:1};
const item=project({kind:'HelmRelease',metadata:{name:'example',namespace:'flux-system',generation:2},spec:{values:{password:'PRIVATE'}},status:{observedGeneration:2,conditions:[{type:'Ready',status:'True',message:'PRIVATE'}],lastAppliedRevision:'separate-domain-sha'}},'main');
const index={schema:1,headSha:sha,batch:'1-1',observedAt,domains:[domain]};
const payload={...index,domain:'main',items:[item]};
const check=(name,data)=>({id:42,app:{id:EXECUTOR_APP_ID},head_sha:sha,name,status:'completed',conclusion:'success',output:{text:JSON.stringify(data)}});
test('projection publishes only allowed status fields and retains domain revision',()=>{
 assert.equal(item.state,'success');assert.equal(item.revision,'separate-domain-sha');
 assert.ok(!JSON.stringify(item).includes('PRIVATE'));assert.equal(item.spec,undefined);
});
test('suspension, missing state, generation lag and controller replicas are distinct',()=>{
 assert.equal(stateOf({...item,suspended:true}),'suspended');
 assert.equal(stateOf({...item,watched:false}),'unknown');
 assert.equal(stateOf({...item,observedGeneration:-1}),'unknown');
 assert.equal(stateOf({...item,observedGeneration:1}),'processing');
 assert.equal(stateOf({...item,ready:false}),'failure');
 assert.equal(stateOf({...item,reconciling:true}),'processing');
 const controller={...item,kind:'Deployment',desired:2,ready:1,updated:2,available:1};
 assert.equal(stateOf(controller),'failure');
 assert.equal(stateOf({...controller,ready:2,available:2}),'success');
 assert.equal(stateOf({...controller,desired:0}),'suspended');
});
test('exact App, commit, batch, complete inventory and check IDs are required',()=>{
 const i=readIndex(check('flux-runtime',index),sha);
 assert.equal(readDomain(check('flux-runtime/main',payload),i,domain).items.length,1);
 for(const change of [{app:{id:1}},{head_sha:'b'.repeat(40)},{status:'in_progress'},{conclusion:'failure'}])assert.throws(()=>readIndex({...check('flux-runtime',index),...change},sha));
 for(const change of [{batch:'old'},{observedAt:'2026-10-25T00:00:00Z'},{items:[]},{domain:'other'}])assert.throws(()=>readDomain(check('flux-runtime/main',{...payload,...change}),i,domain));
 assert.throws(()=>readDomain({...check('flux-runtime/main',payload),id:43},i,domain));
});
test('refresh does not extend TTL; future, expired, invalid timestamps are Unknown even if suspended',()=>{
 assert.equal(evidenceItems(index,[payload],now+TTL-1)[0].state,'success');
 for(const time of [now-1,now+TTL,now+2*TTL])assert.equal(evidenceItems(index,[payload],time)[0].state,'unknown');
 assert.equal(evidenceItems({...index,observedAt:'invalid'},[payload],now)[0].state,'unknown');
 assert.equal(evidenceItems(index,[{items:[{...item,state:'suspended'}]}],now+TTL)[0].state,'unknown');
});

test('retrying Git/Helm failures stay failed while ordinary progress stays processing',()=>{
 for(const reason of ['GitOperationFailed','ValuesError','UpgradeFailed','DependencyNotReady']) {
  const result=project({kind:'HelmRelease',metadata:{name:'retry',namespace:'flux-system',generation:3},status:{observedGeneration:2,conditions:[{type:'Ready',status:'False',reason,observedGeneration:3},{type:'Reconciling',status:'True',observedGeneration:3}]}},'main');
  assert.equal(result.state,'failure',reason);
 }
 assert.equal(stateOf({...item,ready:false,reconciling:true,reason:'Progressing'}),'processing');
 assert.equal(stateOf({...item,ready:true,reconciling:true}),'processing');
});
