import {runtimeState, latestStatuses} from './model.mjs';
const REPO = 're8ch/service-backend';
const BASE = `https://api.github.com/repos/${REPO}`;
let snapshot;
const byId = id => document.getElementById(id);
async function api(path) {
  const r = await fetch(BASE + path, {credentials:'omit', cache:'no-store', headers:{Accept:'application/vnd.github+json'}, signal:AbortSignal.timeout(15000)});
  if (!r.ok) throw new Error(`GitHub HTTP ${r.status}`);
  return r.json();
}
function row(parent, title, detail, state='unknown', url) {
  const el=document.createElement('div'); el.className='row';
  const body=document.createElement('div');
  const name=document.createElement(url ? 'a' : 'strong'); name.textContent=title;
  if (url && /^https:\/\/github\.com\//.test(url)) {name.href=url;name.target='_blank';name.rel='noopener noreferrer';}
  const sub=document.createElement('div'); sub.className='detail';sub.textContent=detail;
  body.append(name,sub);
  const badge=document.createElement('span');badge.className=`badge ${['success','failure'].includes(state)?state:'unknown'}`;
  badge.textContent=state==='success'?'已回报成功':state==='failure'?'已回报失败':'未知';
  el.append(body,badge);parent.append(el);
}
function render() {
  for (const id of ['runtime','checks','deployments','statuses']) byId(id).replaceChildren();
  if (!snapshot) {
    for (const id of ['runtime','checks','deployments','statuses']) row(byId(id),'未知','尚无可用的 GitHub 数据');
    return;
  }
  const {sha,statuses,checks,deployments}=snapshot;
  const runtime=latestStatuses(statuses).filter(s=>s.context.startsWith('flux/kustomization/'));
  if (!runtime.length) row(byId('runtime'),'未知','当前提交没有 Flux Kustomization 回报');
  for (const s of runtime) {
    const result=runtimeState({...s,sha},sha);
    row(byId('runtime'),s.context,`${s.created_at} · ${result.reason} · ${s.description||''}`,result.state,s.target_url);
  }
  if (!checks.length) row(byId('checks'),'没有 Checks','不据此推断健康');
  for (const c of checks) row(byId('checks'),c.name,`${c.status} / ${c.conclusion||'pending'} · ${c.completed_at||c.started_at||''}`,'unknown',c.html_url);
  if (!deployments.length) row(byId('deployments'),'没有 Deployment 记录','不据此推断健康');
  for (const d of deployments) row(byId('deployments'),d.environment,`${d.sha.slice(0,12)} · ${d.latest?.state||'no status'} · ${d.latest?.created_at||d.created_at}`,'unknown',`https://github.com/${REPO}/deployments`);
  for (const s of latestStatuses(statuses).filter(s=>!s.context.startsWith('flux/kustomization/')))
    row(byId('statuses'),s.context,`${s.state} · ${s.created_at}`,'unknown',s.target_url);
}
async function refresh() {
  byId('refresh').disabled=true;
  // Invalidate the previous health conclusion before attempting a new read.
  snapshot=undefined;render();byId('message').textContent='正在读取 GitHub…';byId('revision').textContent='';
  try {
    const commit=await api('/commits/main');
    const sha=commit.sha;
    const [statuses,checkData,deployments]=await Promise.all([
      api(`/commits/${sha}/statuses?per_page=100`),api(`/commits/${sha}/check-runs?per_page=100`),api(`/deployments?sha=${sha}&per_page=3`)
    ]);
    await Promise.all(deployments.map(async d=>{d.latest=(await api(`/deployments/${d.id}/statuses?per_page=1`))[0];}));
    snapshot={sha,statuses,checks:checkData.check_runs,deployments};
    byId('revision').textContent=`main · ${sha}`;
    byId('message').textContent=`读取时间 ${new Date().toLocaleString()}；该时间不会延长证据有效期。最多展示 100 条状态、100 个 Checks、3 个当前提交部署记录。`;
  } catch(error) {
    snapshot=undefined;
    byId('message').textContent=`未知：${error.message}。仓库权限、网络或 API 限流可能导致不可用；没有回退到集群查询。`;
  } finally {render();byId('refresh').disabled=false;}
}
byId('refresh').addEventListener('click',refresh);
setInterval(render,15000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)render();});
refresh();
