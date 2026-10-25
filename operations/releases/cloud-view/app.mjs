import {RESOURCE_TYPES, latestStatuses} from './model.mjs';
import {EXECUTOR_APP_ID,SNAPSHOT_CHECK,readIndex,readDomain,evidenceItems} from './evidence.mjs';
const REPO='re8ch/service-backend', BASE=`https://api.github.com/repos/${REPO}`;
let snapshot;
const byId=id=>document.getElementById(id);
const element=(tag,text,className)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
function link(text,url){const el=element('a',text);if(/^https:\/\/github\.com\//.test(url||'')){el.href=url;el.target='_blank';el.rel='noopener noreferrer';}return el;}
function badge(text,state='unknown'){return element('span',text,`badge ${['success','failure','suspended','processing'].includes(state)?state:'unknown'}`);}
function tableRow(id,cells){const tr=element('tr');for(const value of cells){const td=element('td');td.append(value instanceof Node?value:document.createTextNode(String(value??'Unknown')));tr.append(td);}byId(id).append(tr);}
function empty(id,columns,message='Unknown · 尚无可用的 GitHub 数据'){const tr=element('tr'),td=element('td',message,'empty');td.colSpan=columns;tr.append(td);byId(id).append(tr);}
async function api(path){const r=await fetch(BASE+path,{credentials:'omit',cache:'no-store',headers:{Accept:'application/vnd.github+json'},signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error(`GitHub HTTP ${r.status}`);return r.json();}
function chart(group){
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 120 120');svg.classList.add('ring');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${group.title}: ${group.success} running, ${group.failure} failed, ${group.suspended} suspended, ${group.processing} processing, ${group.unknown} unknown; ${group.total} reported`);
  const circle=(className,amount=100,offset=0)=>{const c=document.createElementNS(ns,'circle');for(const [key,value] of Object.entries({cx:60,cy:60,r:46,pathLength:100,'stroke-dasharray':`${amount} ${100-amount}`,'stroke-dashoffset':-offset,transform:'rotate(-90 60 60)',class:className}))c.setAttribute(key,String(value));svg.append(c);};
  circle('track');if(group.total){circle('success',group.success/group.total*100);circle('failure',group.failure/group.total*100,group.success/group.total*100);circle('suspended',group.suspended/group.total*100,(group.success+group.failure)/group.total*100);circle('processing',group.processing/group.total*100,(group.success+group.failure+group.suspended)/group.total*100);}
  const text=document.createElementNS(ns,'text');text.setAttribute('x','60');text.setAttribute('y','60');text.textContent=group.total&&group.unknown!==group.total?`${Math.round(group.success/group.total*100)}%`:'Unknown';svg.append(text);return svg;
}
function renderOverview(groups){
  const sort=byId('sort').value,[metric,direction]=sort.split('-');
  groups=[...groups].sort((a,b)=>metric==='name'?(direction==='desc'?-1:1)*a.title.localeCompare(b.title):(direction==='asc'?1:-1)*(a[metric]-b[metric]));
  byId('overview').replaceChildren();
  for(const g of groups){if(byId('show').value==='reported'&&!g.total)continue;const card=element('article',undefined,'card'),body=element('div'),title=element('a',g.title,'card-title');title.href='#resource-section';title.addEventListener('click',()=>{byId('resource-filter').value=g.kind;render();});body.append(title);const legend=element('div',undefined,'legend');for(const [count,label] of [[g.success,'running'],[g.failure,'failed'],[g.suspended,'suspended'],[g.processing,'processing'],[g.unknown,'unknown']])legend.append(element('span',g.total?`${count}/${g.total} ${label}`:`— ${label}`));body.append(legend);card.append(body,chart(g));byId('overview').append(card);}
  if(!byId('overview').children.length)byId('overview').append(element('p','Unknown · 当前提交没有资源回报','source-note'));
  const controllers=(snapshot?.runtimeItems||[]).filter(x=>x.kind==='Deployment');
  const controllerFailures=controllers.filter(x=>x.state==='failure').length;
  const failed=groups.reduce((n,g)=>n+g.failure,0),unknown=groups.reduce((n,g)=>n+g.unknown,0),count=groups.reduce((n,g)=>n+g.total,0),banner=byId('health-banner');
  banner.className=`banner ${failed||controllerFailures?'failure':''}`;
  banner.textContent=!snapshot?.runtime?'Unknown · 无可用的 GitHub 证据':failed?`${failed} resource(s) failing · ${unknown} unknown`:`${groups.reduce((n,g)=>n+g.success,0)}/${count} running · ${groups.reduce((n,g)=>n+g.suspended,0)} suspended · ${groups.reduce((n,g)=>n+g.processing,0)} processing · ${unknown} unknown`;
  if(snapshot?.runtime)banner.textContent+=` · ${controllerFailures} controller(s) not ready`;
}
function render(){
  for(const id of ['runtime','controllers','checks','deployments','statuses'])byId(id).replaceChildren();
  const domain=byId('domain-filter').value;
  const items=snapshot?.runtime?evidenceItems(snapshot.runtime.index,snapshot.runtime.domains).filter(x=>domain==='all'||x.domain===domain):[];
  if(snapshot)snapshot.runtimeItems=items;
  const groups=RESOURCE_TYPES.map(([kind,title])=>{const resources=items.filter(x=>x.kind.toLowerCase()===kind);return {kind,title,resources,total:resources.length,...Object.fromEntries(['success','failure','suspended','processing','unknown'].map(state=>[state,resources.filter(x=>x.state===state).length]))};});renderOverview(groups);
  if(!snapshot){byId('controller-status').textContent='Unknown';empty('runtime',7);empty('controllers',6);empty('checks',4);empty('deployments',4);empty('statuses',3);return;}
  const {sha,statuses,checks,deployments}=snapshot,filter=byId('resource-filter').value,query=byId('search').value.toLowerCase();
  for(const g of groups)for(const s of g.resources){if((filter!=='all'&&filter!==g.kind)||!s.name.toLowerCase().includes(query))continue;const status=element('div');status.append(badge(s.state==='success'?'Ready':s.state,s.state),element('span',s.reason||'', 'subtext'));tableRow('runtime',[s.name,g.title,s.domain,s.namespace,status,s.revision||s.digest||'Unknown',s.observedAt]);}
  if(!byId('runtime').children.length)empty('runtime',7,'Unknown · 当前筛选没有有效资源快照');
  const controllers=items.filter(x=>x.kind==='Deployment');
  const controllerStatus=byId('controller-status');controllerStatus.textContent=!controllers.length?'Unknown':controllers.some(x=>x.state==='unknown')?'Unknown':controllers.some(x=>x.state==='failure')?'Some Controllers Are Not Ready':'Observed';
  for(const c of controllers)tableRow('controllers',[c.name,c.domain,c.namespace,c.state==='unknown'?'Unknown':`${c.ready}/${c.desired}`,badge(c.state,c.state),(c.images||[]).join('\n')]);
  if(!controllers.length)empty('controllers',6,'Unknown · 没有可用的控制器快照');
  for(const c of checks)tableRow('checks',[link(c.name,c.html_url),c.status,badge(c.conclusion||'pending'),c.completed_at||c.started_at||'Unknown']);
  if(!checks.length)empty('checks',4,'No Checks · 不据此推断运行时健康');
  for(const d of deployments)tableRow('deployments',[link(d.environment,`https://github.com/${REPO}/deployments`),badge(d.latest?.state||'Unknown'),d.sha.slice(0,12),d.latest?.created_at||d.created_at]);
  if(!deployments.length)empty('deployments',4,'No Deployments · 不据此推断运行时健康');
  for(const s of latestStatuses(statuses))tableRow('statuses',[link(s.context,s.target_url),badge(s.state),s.created_at]);
  if(!statuses.length)empty('statuses',3);
}
async function refresh(){
  byId('refresh').disabled=true;snapshot=undefined;render();byId('message').textContent='正在读取 GitHub…';byId('revision').textContent='';
  try{const commit=await api('/commits/main'),sha=commit.sha;const [statuses,checkData,deployments]=await Promise.all([api(`/commits/${sha}/statuses?per_page=100`),api(`/commits/${sha}/check-runs?per_page=100`),api(`/deployments?sha=${sha}&per_page=3`)]);await Promise.all(deployments.map(async d=>{d.latest=(await api(`/deployments/${d.id}/statuses?per_page=1`))[0];}));const list=await api(`/commits/${sha}/check-runs?app_id=${EXECUTOR_APP_ID}&check_name=${SNAPSHOT_CHECK}&filter=latest&per_page=100`);
    const latest=[...list.check_runs].sort((a,b)=>b.id-a.id)[0];
    const index=readIndex(latest,sha);
    const domains=await Promise.all(index.domains.map(async d=>readDomain(await api(`/check-runs/${d.checkRunId}`),index,d)));
    snapshot={sha,statuses,checks:checkData.check_runs,deployments,runtime:{index,domains}};
    const selected=byId('domain-filter').value;byId('domain-filter').replaceChildren();const all=element('option','All Domains');all.value='all';byId('domain-filter').append(all);for(const d of index.domains){const option=element('option',d.id);option.value=d.id;byId('domain-filter').append(option);}byId('domain-filter').value=index.domains.some(d=>d.id===selected)?selected:'all';byId('revision').textContent=`main · ${sha}`;byId('message').textContent=`读取时间 ${new Date().toLocaleString()}；刷新不会延长证据有效期。最多读取 100 条状态、100 个 Checks、3 个当前提交部署记录。`;
  }catch(error){snapshot=undefined;byId('message').textContent=`Unknown：${error.message}。仓库权限、网络或 API 限流可能导致不可用；没有回退到集群查询。`;}finally{render();byId('refresh').disabled=false;}
}
for(const [kind,title] of RESOURCE_TYPES){const option=element('option',title);option.value=kind;byId('resource-filter').append(option);}
byId('refresh').addEventListener('click',refresh);
for(const id of ['sort','show','resource-filter','domain-filter'])byId(id).addEventListener('change',render);
byId('search').addEventListener('input',render);
setInterval(render,15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)render();});refresh();
