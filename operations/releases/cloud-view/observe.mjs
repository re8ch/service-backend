// A bounded GitHub Actions observation, not a reconciler: no cluster writes.
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {project,SNAPSHOT_CHECK} from './evidence.mjs';
const repo=process.env.GITHUB_REPOSITORY,sha=process.env.GITHUB_SHA,batch=`${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT}`;
if(repo!=='re8ch/service-backend'||!/^[a-f0-9]{40}$/.test(sha||'')) throw new Error('Unexpected publication target');
// Reuse the installed GitHub client and its approved proxy handling. Credentials
// remain in GH_TOKEN; payloads go over stdin, never command arguments or logs.
async function github(path,body,method=body?'POST':'GET') {
  const args=['api',`repos/${repo}${path}`,'--method',method];
  if(body)args.push('--input','-');
  return JSON.parse(execFileSync('gh',args,{input:body?JSON.stringify(body):undefined,
    encoding:'utf8',maxBuffer:8*1024*1024,timeout:45000,stdio:['pipe','pipe','pipe']}));
}
const pick=(value,keys)=>Object.fromEntries(keys.filter(k=>value?.[k]!==undefined).map(k=>[k,value[k]]));
const evidenceCheck=c=>({...pick(c,['id','name','head_sha','status','conclusion']),app:{id:c.app.id},output:{text:c.output.text}});
const snapshotURL='https://flux-status.re8ch.com/api/snapshot';
async function publishSnapshot(body) {
  const url=new URL(process.env.ACTIONS_ID_TOKEN_REQUEST_URL);url.searchParams.set('audience',snapshotURL);
  const identity=await fetch(url,{headers:{Authorization:`Bearer ${process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN}`},signal:AbortSignal.timeout(15000)});
  if(!identity.ok)throw new Error('Cloud publication identity unavailable');
  const {value}=await identity.json();
  const response=await fetch(snapshotURL,{method:'POST',headers:{Authorization:`Bearer ${value}`,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw new Error(`Cloud publication HTTP ${response.status}`);
}
const update=(id,body)=>github(`/check-runs/${id}`,body,'PATCH');
const authority=JSON.parse(readFileSync(process.argv[2],'utf8'));
const domains=[{id:'main',namespace:'flux-system',repository:repo},...Object.entries(authority.spec.domains).map(([id,d])=>({id,namespace:d.controllerNamespace,repository:d.repository.replace(/^ssh:\/\/git@ssh.github.com:443\//,'').replace(/\.git$/,'')}))];
// Capture incomplete first: readers must not silently use a previous successful batch.
const index=await github('/check-runs',{name:SNAPSHOT_CHECK,head_sha:sha,status:'in_progress',external_id:batch,output:{title:'Flux runtime observation',summary:'Collecting a complete read-only inventory.'}});
const observedAt=new Date().toISOString();
const byNamespace=new Map(domains.map(d=>[d.namespace,d]));
const temp=mkdtempSync(join(process.env.RUNNER_TEMP,'flux-observer-'));
let stage='OIDC exchange';
try {
  const url=new URL(process.env.ACTIONS_ID_TOKEN_REQUEST_URL);url.searchParams.set('audience','https://kube-api.re8ch.com/observer');
  const oidc=await fetch(url,{headers:{Authorization:`Bearer ${process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN}`},signal:AbortSignal.timeout(15000)});
  if(!oidc.ok)throw new Error('OIDC exchange failed');
  const identity=await oidc.json();
  if(typeof identity.value!=='string'||!identity.value||!process.env.KUBE_API_CA)throw new Error('Missing observation credentials');
  const config={apiVersion:'v1',kind:'Config',clusters:[{name:'re8ch',cluster:{server:'https://kube-api.re8ch.com:6443','certificate-authority-data':Buffer.from(process.env.KUBE_API_CA).toString('base64')}}],users:[{name:'observer',user:{token:identity.value}}],contexts:[{name:'observer',context:{cluster:'re8ch',user:'observer'}}],'current-context':'observer'};
  process.env.KUBECONFIG=join(temp,'config');writeFileSync(process.env.KUBECONFIG,JSON.stringify(config),{mode:0o600});
  const resources=['kustomizations.kustomize.toolkit.fluxcd.io','helmreleases.helm.toolkit.fluxcd.io','gitrepositories.source.toolkit.fluxcd.io','ocirepositories.source.toolkit.fluxcd.io','helmrepositories.source.toolkit.fluxcd.io','helmcharts.source.toolkit.fluxcd.io','alerts.notification.toolkit.fluxcd.io','providers.notification.toolkit.fluxcd.io','receivers.notification.toolkit.fluxcd.io'];
  const buckets=new Map(domains.map(d=>[d.id,[]]));
  const get=argv=>JSON.parse(execFileSync('kubectl',[...argv,'--request-timeout=30s','-o','json'],{encoding:'utf8',maxBuffer:32*1024*1024,stdio:['ignore','pipe','pipe']}));
  // Verify the real short-lived identity still fails closed before publishing.
  stage='permission boundary';
  for(const args of [['get','secrets','--all-namespaces'],['patch','deployments.apps','-n','flux-system'],['create','serviceaccounts/token','-n','flux-system']]) {
    try {
      const answer=execFileSync('kubectl',['auth','can-i',...args,'--request-timeout=15s'],{encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
      if(answer!=='no')throw new Error('Observer permission boundary widened');
    } catch(error) {
      if(error.status!==1||String(error.stdout).trim()!=='no')throw new Error('Observer permission boundary could not be verified',{cause:error});
    }
  }
  for(const resource of resources){
    stage=`read ${resource}`;
    for(const object of get(['get',resource,'--all-namespaces']).items){
      const owner=object.metadata.labels?.['kustomize.toolkit.fluxcd.io/namespace'];
      const domain=byNamespace.get(object.metadata.namespace)||byNamespace.get(owner)||domains[0];
      buckets.get(domain.id).push(project(object,domain.id,byNamespace.has(object.metadata.namespace)));
    }
  }
  for(const d of domains){
    stage=`read controllers in ${d.namespace}`;
    for(const object of get(['get','deployments.apps','-n',d.namespace]).items){
      if(object.metadata.name.endsWith('-controller')) buckets.get(d.id).push(project(object,d.id));
    }
  }
  const published=[],domainChecks=[];
  for(const domain of domains){
    stage=`publish ${domain.id}`;
    const items=buckets.get(domain.id).sort((a,b)=>`${a.kind}/${a.namespace}/${a.name}`.localeCompare(`${b.kind}/${b.namespace}/${b.name}`));
    const data={schema:1,headSha:sha,batch,observedAt,domain:domain.id,items};
    const text=JSON.stringify(data);if(text.length>60000)throw new Error(`Domain evidence exceeds Checks output bound: ${domain.id}`);
    const failed=items.filter(x=>x.state==='failure').length;
    const check=await github('/check-runs',{name:`${SNAPSHOT_CHECK}/${domain.id}`,head_sha:sha,status:'completed',conclusion:failed?'failure':'neutral',external_id:batch,completed_at:new Date().toISOString(),output:{title:`${domain.id}: ${items.length} observed resources`,summary:`${failed} resources report failure. See the timestamped snapshot; this conclusion is not a perpetual health guarantee.`,text}});
    published.push({...domain,checkRunId:check.id,count:items.length});
    domainChecks.push(check);
  }
  stage='complete inventory';
  const indexCheck=await update(index.id,{status:'completed',conclusion:'success',completed_at:new Date().toISOString(),output:{title:'Flux runtime inventory captured',summary:'Collection completed. Resource health is recorded separately; freshness expires after 15 minutes.',text:JSON.stringify({schema:1,headSha:sha,batch,observedAt,domains:published})}});
  stage='publish public cloud snapshot';
  const [statuses,checks,deployments]=await Promise.all([github(`/commits/${sha}/statuses?per_page=100`),github(`/commits/${sha}/check-runs?per_page=100`),github(`/deployments?sha=${sha}&per_page=3`)]);
  for(const d of deployments)d.latest=(await github(`/deployments/${d.id}/statuses?per_page=1`))[0];
  await publishSnapshot({schema:1,repository:repo,sha,indexCheck:evidenceCheck(indexCheck),domainChecks:domainChecks.map(evidenceCheck),
    statuses:statuses.map(s=>pick(s,['context','target_url','state','created_at'])),
    checks:checks.check_runs.map(c=>pick(c,['name','html_url','status','conclusion','completed_at','started_at'])),
    deployments:deployments.map(d=>({...pick(d,['environment','sha','created_at']),latest:pick(d.latest,['state','created_at'])}))});
  console.log(JSON.stringify({batch,domains:published.map(d=>({id:d.id,count:d.count})),observedAt}));
} catch(error){
  // Only allowlisted categories leave the process, never raw stderr or tokens.
  const detail=String(error.cause?.stderr||error.stderr||error.cause?.message||error.message);
  const category=/certificate|x509|TLS/i.test(detail)?'TLS verification':/Unauthorized|credentials|logged in/i.test(detail)?'authentication':/forbidden/i.test(detail)?'authorization':/timeout|timed out|deadline/i.test(detail)?'timeout':/output bound/i.test(detail)?'output size':/GitHub [0-9]+/.test(detail)?'GitHub API':'unclassified';
  await update(index.id,{status:'completed',conclusion:'failure',completed_at:new Date().toISOString(),output:{title:'Flux runtime observation unavailable',summary:`Incomplete observation at ${stage}: ${category}. The cloud view must show Unknown.`}}).catch(()=>{});
  await publishSnapshot({schema:1,repository:repo,sha,unavailable:true,batch,observedAt:new Date().toISOString()}).catch(()=>{});
  // Do not emit command stderr, object bodies or credentials into public logs.
  console.error(`Runtime observation failed at ${stage}: ${category}; no complete inventory was published.`);process.exitCode=1;
} finally {rmSync(temp,{recursive:true,force:true});}

