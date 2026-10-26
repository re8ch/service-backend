// Public inventory publication is withdrawn pending an aggregate-only contract.
import {execFileSync} from 'node:child_process';
try {
  const env=process.env,repository=env.GITHUB_REPOSITORY,sha=env.GITHUB_SHA;
  if(repository!=='re8ch/service-backend'||!/^[a-f0-9]{40}$/.test(sha||'')||env.GITHUB_REF!=='refs/heads/main')
    throw new Error('Unexpected publication identity');
  const endpoint='https://flux-status.re8ch.com/api/snapshot';
  const identityURL=new URL(env.ACTIONS_ID_TOKEN_REQUEST_URL);
  identityURL.searchParams.set('audience',endpoint);
  const identity=await fetch(identityURL,{headers:{Authorization:`Bearer ${env.ACTIONS_ID_TOKEN_REQUEST_TOKEN}`},signal:AbortSignal.timeout(15000)});
  if(!identity.ok)throw new Error('Publication identity unavailable');
  const {value}=await identity.json();
  if(typeof value!=='string'||!value)throw new Error('Publication identity unavailable');
  // Overwrite the public object before reporting success. No Kubernetes data,
  // GitHub Checks, deployment metadata or raw error bodies enter this payload.
  const snapshot={schema:1,repository,sha,unavailable:true,
    batch:`${env.GITHUB_RUN_ID}-${env.GITHUB_RUN_ATTEMPT}`,observedAt:new Date().toISOString()};
  const response=await fetch(endpoint,{method:'POST',headers:{Authorization:`Bearer ${value}`,'Content-Type':'application/json'},body:JSON.stringify(snapshot),signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw new Error('Public snapshot withdrawal failed');
  execFileSync('gh',['api',`repos/${repository}/check-runs`,'--method','POST','--input','-'],{
    input:JSON.stringify({name:'flux-runtime',head_sha:sha,status:'completed',conclusion:'neutral',
      output:{title:'Detailed runtime evidence requires authentication',
        summary:'Public inventory publication is withdrawn. This check makes no cluster health assertion. Use authenticated platform inspection.'}}),
    encoding:'utf8',timeout:45000,stdio:['pipe','pipe','pipe']});
  console.log('Public inventory withdrawn; use authenticated platform inspection.');
} catch {
  // HTTP responses and subprocess errors may contain private data.
  console.error('Public inventory withdrawal failed; verify the public snapshot before claiming containment.');
  process.exitCode=1;
}

