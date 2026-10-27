import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';

function run(failUpload=false,ref='refs/heads/main') {
  const source=`
    import cp from 'node:child_process';
    import {syncBuiltinESMExports} from 'node:module';
    let requests=0;
    cp.execFileSync=(command,args,options)=>{
      if(command!=='gh')throw Error('Unexpected subprocess');
      console.log(JSON.stringify({check:JSON.parse(options.input)}));
    };
    syncBuiltinESMExports();
    globalThis.fetch=async(url,options)=>{
      requests++;
      if(requests===2)console.log(JSON.stringify({snapshot:JSON.parse(options.body)}));
      return {ok:!(requests===2&&${failUpload}),json:async()=>({value:'PRIVATE_TOKEN'})};
    };
    await import('./observe.mjs');
  `;
  return spawnSync(process.execPath,['--input-type=module','-e',source],{
    cwd:new URL('.',import.meta.url),encoding:'utf8',env:{...process.env,
      GITHUB_REPOSITORY:'re8ch/service-backend',GITHUB_SHA:'a'.repeat(40),GITHUB_REF:ref,
      GITHUB_RUN_ID:'1',GITHUB_RUN_ATTEMPT:'1',
      ACTIONS_ID_TOKEN_REQUEST_URL:'https://identity.invalid/token',ACTIONS_ID_TOKEN_REQUEST_TOKEN:'PRIVATE_TOKEN'}});
}
test('missing observation publishes an unavailable aggregate before a neutral check',()=>{
  const result=run();assert.equal(result.status,0,result.stderr);
  const rows=result.stdout.split('\n').filter(x=>x.startsWith('{')).map(x=>JSON.parse(x));
  assert.deepEqual(Object.keys(rows[0].snapshot).sort(),['batch','complete','domains','observedAt','repository','schema','sha']);
  assert.equal(rows[0].snapshot.complete,false);
  assert.deepEqual(rows[0].snapshot.domains,[]);
  assert.equal(rows[1].check.conclusion,'neutral');
  assert.equal(rows[1].check.output.text,undefined);
  assert.ok(!result.stdout.includes('PRIVATE_TOKEN'));
});
test('upload failure emits no check and no raw error',()=>{
  const result=run(true);assert.equal(result.status,1);
  assert.ok(!result.stdout.includes('"check"'));
  assert.ok(!result.stderr.includes('PRIVATE_TOKEN'));
});
test('non-main invocation fails before any external request',()=>{
  const result=run(false,'refs/heads/other');assert.equal(result.status,1);
  assert.equal(result.stdout,'');
});
