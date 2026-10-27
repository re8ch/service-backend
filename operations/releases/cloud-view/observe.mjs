// Publish only bounded cloud aggregates, never raw Kubernetes or Actions payloads.
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {aggregate} from './snapshot.mjs';
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
  let input;
  try { input=JSON.parse(readFileSync(join(env.RUNNER_TEMP,'cloud-runtime-snapshot.json'),'utf8')); } catch {}
  const snapshot=aggregate(input,{repository,sha,batch:`${env.GITHUB_RUN_ID}-${env.GITHUB_RUN_ATTEMPT}`});
  const response=await fetch(endpoint,{method:'POST',headers:{Authorization:`Bearer ${value}`,'Content-Type':'application/json'},body:JSON.stringify(snapshot),signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw new Error('Cloud aggregate publication failed');
  execFileSync('gh',['api',`repos/${repository}/check-runs`,'--method','POST','--input','-'],{
    input:JSON.stringify({name:'flux-runtime',head_sha:sha,status:'completed',conclusion:'neutral',
      output:{title:'Cloud synchronization and runtime health observed separately',
        summary:'Public aggregate only. Unknown observations and failed synchronization are not successful health checks; detailed evidence requires authenticated platform inspection.'}}),
    encoding:'utf8',timeout:45000,stdio:['pipe','pipe','pipe']});
  console.log('Cloud aggregate published; detailed evidence requires authenticated platform inspection.');
} catch {
  // HTTP responses and subprocess errors may contain private data.
  console.error('Cloud aggregate publication failed; previous observations must expire normally.');
  process.exitCode=1;
}

