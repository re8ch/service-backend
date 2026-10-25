// Small read-only projection shared by the bounded observer and static viewer.
export const EXECUTOR_APP_ID = 5426288;
export const SNAPSHOT_CHECK = 'flux-runtime';
export const TTL = 15 * 60 * 1000;
export const KINDS = ['Kustomization','HelmRelease','GitRepository','OCIRepository','HelmRepository','HelmChart','Alert','Provider','Receiver'];
export function fresh(timestamp, now=Date.now()) {
  const time=Date.parse(timestamp); return Number.isFinite(time)&&time<=now&&now-time<TTL;
}
export function stateOf(item) {
  if(item.suspended) return 'suspended';
  if(item.watched===false) return 'unknown';
  if(item.observedGeneration<0) return 'unknown';
  if(item.kind==='Deployment') {
    if(item.desired===0) return 'suspended';
    if(item.observedGeneration<item.generation) return 'processing';
    return item.ready===item.desired&&item.updated===item.desired&&item.available===item.desired?'success':'failure';
  }
  if(item.observedGeneration<item.generation) return 'processing';
  if(item.stalled) return 'failure';
  // Flux retains Reconciling=True while retrying an explicit failure.
  if(item.ready===false&&item.reason!=='Progressing') return 'failure';
  if(item.reconciling) return 'processing';
  if(item.ready===true) return 'success';
  if(item.ready===false) return 'failure';
  return 'unknown';
}
export function project(object, domain, watched=true) {
  const metadata=object.metadata||{}, spec=object.spec||{}, status=object.status||{};
  const conditions=status.conditions||[], ready=conditions.find(c=>c.type==='Ready');
  const result={kind:object.kind,name:metadata.name,namespace:metadata.namespace,domain,
    generation:metadata.generation??0,observedGeneration:ready?.observedGeneration??status.observedGeneration??-1,
    suspended:spec.suspend===true,watched,ready:ready?.status==='True'?true:ready?.status==='False'?false:null,
    reason:ready?.reason||'',reconciling:conditions.some(c=>c.type==='Reconciling'&&c.status==='True'),
    stalled:conditions.some(c=>c.type==='Stalled'&&c.status==='True')};
  // Artifact/chart revisions belong to the resource; never substitute the main Git SHA.
  result.revision=status.lastAppliedRevision||status.artifact?.revision||status.lastAttemptedRevision||'';
  result.digest=status.artifact?.digest||status.history?.[0]?.digest||'';
  if(object.kind==='Deployment') Object.assign(result,{desired:spec.replicas??1,ready:status.readyReplicas??0,
    updated:status.updatedReplicas??0,available:status.availableReplicas??0,
    images:(spec.template?.spec?.containers||[]).map(c=>c.image)});
  result.state=stateOf(result);return result;
}
function envelope(check,sha,name) {
  if(check?.app?.id!==EXECUTOR_APP_ID||check.head_sha!==sha||check.name!==name||check.status!=='completed') throw new Error('Untrusted or incomplete runtime check');
  const data=JSON.parse(check.output?.text||'null');
  if(data?.schema!==1||data.headSha!==sha||typeof data.batch!=='string') throw new Error('Runtime evidence schema/revision mismatch');
  return data;
}
export function readIndex(check,sha) {
  const index=envelope(check,sha,SNAPSHOT_CHECK);
  if(check.conclusion!=='success'||!Array.isArray(index.domains)||!index.domains.length||index.domains.length>20) throw new Error('Runtime inventory incomplete');
  const ids=new Set();
  for(const d of index.domains) {
    if(!/^[a-z0-9-]+$/.test(d.id)||!Number.isSafeInteger(d.checkRunId)||ids.has(d.id)) throw new Error('Invalid runtime inventory');
    ids.add(d.id);
  }
  return index;
}
export function readDomain(check,index,domain) {
  const data=envelope(check,index.headSha,`${SNAPSHOT_CHECK}/${domain.id}`);
  if(check.id!==domain.checkRunId||data.batch!==index.batch||data.domain!==domain.id||data.observedAt!==index.observedAt||!Array.isArray(data.items)||data.items.length!==domain.count) throw new Error('Incomplete or mixed runtime batch');
  const ids=new Set();
  for(const item of data.items) {
    const id=`${item.kind}/${item.namespace}/${item.name}`;
    if(![...KINDS,'Deployment'].includes(item.kind)||item.domain!==domain.id||ids.has(id)||!['success','failure','suspended','processing','unknown'].includes(item.state)) throw new Error('Invalid runtime resource');
    ids.add(id);
  }
  return data;
}
export function evidenceItems(index,domains,now=Date.now()) {
  return domains.flatMap(d=>d.items.map(item=>({...item,state:fresh(index.observedAt,now)?item.state:'unknown',observedAt:index.observedAt})));
}
