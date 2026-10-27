export const DOMAINS=['main','networking','identity-access','agent-runtime','observability-metering','delivery-products'];
const time=(v,now)=>typeof v==='string'&&Number.isFinite(Date.parse(v))&&Date.parse(v)<=now&&now-Date.parse(v)<900000;
function counts(input,keys) {
  const out={};
  for(const key of keys) {
    const n=input?.[key];
    if(!Number.isSafeInteger(n)||n<0||n>100000)throw Error('Invalid aggregate');
    out[key]=n;
  }
  return out;
}
export function aggregate(input,identity,now=Date.now()) {
  const empty={schema:2,...identity,observedAt:new Date(now).toISOString(),complete:false,domains:[]};
  try {
    if(input?.schema!==2||input.complete!==true||!time(input.observedAt,now)||!Array.isArray(input.domains)||input.domains.length!==DOMAINS.length) return empty;
    for(const key of ['repository','sha','batch'])if(input[key]!==identity[key])return empty;
    const ids=new Set();
    const domains=input.domains.map(d=>{
      if(!DOMAINS.includes(d.id)||ids.has(d.id))throw Error('Invalid domain');ids.add(d.id);
      if(!['current','unavailable'].includes(d.observation?.status)||!time(d.observation.observedAt,now))throw Error('Invalid observation');
      if(!['succeeded','failed','running','unknown'].includes(d.operations?.status))throw Error('Invalid operation');
      const at=d.operations.observedAt;
      if(at!==null&&(!Number.isFinite(Date.parse(at))||Date.parse(at)>now))throw Error('Invalid operation time');
      if(d.operations.status!=='unknown'&&at===null)throw Error('Missing operation time');
      return {id:d.id,sync:counts(d.sync,['synced','drifted','failed','unknown']),health:counts(d.health,['healthy','unhealthy','unknown']),
        observation:{status:d.observation.status,observedAt:d.observation.observedAt},operations:{status:d.operations.status,observedAt:at}};
    });
    return {...empty,observedAt:input.observedAt,complete:true,domains};
  } catch { return empty; }
}
