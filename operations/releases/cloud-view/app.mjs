import {resourceGroups, RESOURCE_TYPES, latestStatuses} from './model.mjs';
const REPO='re8ch/service-backend', BASE=`https://api.github.com/repos/${REPO}`;
let snapshot;
const byId=id=>document.getElementById(id);
const element=(tag,text,className)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
function link(text,url){const el=element('a',text);if(/^https:\/\/github\.com\//.test(url||'')){el.href=url;el.target='_blank';el.rel='noopener noreferrer';}return el;}
function badge(text,state='unknown'){return element('span',text,`badge ${['success','failure'].includes(state)?state:'unknown'}`);}
function tableRow(id,cells){const tr=element('tr');for(const value of cells){const td=element('td');td.append(value instanceof Node?value:document.createTextNode(String(value??'Unknown')));tr.append(td);}byId(id).append(tr);}
function empty(id,columns,message='Unknown · 尚无可用的 GitHub 数据'){const tr=element('tr'),td=element('td',message,'empty');td.colSpan=columns;tr.append(td);byId(id).append(tr);}
async function api(path){const r=await fetch(BASE+path,{credentials:'omit',cache:'no-store',headers:{Accept:'application/vnd.github+json'},signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error(`GitHub HTTP ${r.status}`);return r.json();}
function chart(group){
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 120 120');svg.classList.add('ring');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${group.title}: ${group.success} running, ${group.failure} failed, ${group.unknown} unknown; ${group.total} reported`);
  const circle=(className,amount=100,offset=0)=>{const c=document.createElementNS(ns,'circle');for(const [key,value] of Object.entries({cx:60,cy:60,r:46,pathLength:100,'stroke-dasharray':`${amount} ${100-amount}`,'stroke-dashoffset':-offset,transform:'rotate(-90 60 60)',class:className}))c.setAttribute(key,String(value));svg.append(c);};
  circle('track');if(group.total){circle('success',group.success/group.total*100);circle('failure',group.failure/group.total*100,group.success/group.total*100);}
  const text=document.createElementNS(ns,'text');text.setAttribute('x','60');text.setAttribute('y','60');text.textContent=group.total&&group.unknown!==group.total?`${Math.round(group.success/group.total*100)}%`:'Unknown';svg.append(text);return svg;
}
function renderOverview(groups){
  const sort=byId('sort').value,[metric,direction]=sort.split('-');
  groups=[...groups].sort((a,b)=>metric==='name'?(direction==='desc'?-1:1)*a.title.localeCompare(b.title):(direction==='asc'?1:-1)*(a[metric]-b[metric]));
  byId('overview').replaceChildren();
  for(const g of groups){if(byId('show').value==='reported'&&!g.total)continue;const card=element('article',undefined,'card'),body=element('div'),title=element('a',g.title,'card-title');title.href='#resource-section';title.addEventListener('click',()=>{byId('resource-filter').value=g.kind;render();});body.append(title);const legend=element('div',undefined,'legend');for(const [count,label] of [[g.success,'running'],[g.failure,'failed'],[g.unknown,'unknown']])legend.append(element('span',g.total?`${count}/${g.total} ${label}`:`— ${label}`));legend.append(element('span','— suspended','unreported'),element('span','— processing','unreported'));body.append(legend);card.append(body,chart(g));byId('overview').append(card);}
  if(!byId('overview').children.length)byId('overview').append(element('p','Unknown · 当前提交没有资源回报','source-note'));
  const failed=groups.reduce((n,g)=>n+g.failure,0),unknown=groups.reduce((n,g)=>n+g.unknown,0),count=groups.reduce((n,g)=>n+g.total,0),banner=byId('health-banner');
  banner.className=`banner ${failed?'failure':''}`;
  banner.textContent=!snapshot?'Unknown · 无可用的 GitHub 证据':failed?`${failed} reported resource(s) failing · ${unknown} unknown`:`${count-unknown}/${count} reported resource(s) ready · ${unknown} unknown · 未回报资源状态未知`;
}
function render(){
  for(const id of ['runtime','checks','deployments','statuses'])byId(id).replaceChildren();
  const groups=resourceGroups(snapshot?.statuses||[],snapshot?.sha||'');renderOverview(groups);
  if(!snapshot){empty('runtime',6);empty('checks',4);empty('deployments',4);empty('statuses',3);return;}
  const {sha,statuses,checks,deployments}=snapshot,filter=byId('resource-filter').value,query=byId('search').value.toLowerCase();
  for(const g of groups)for(const s of g.resources){if((filter!=='all'&&filter!==g.kind)||!s.name.toLowerCase().includes(query))continue;const status=element('div');status.append(badge(s.evidence.state==='success'?'Ready':s.evidence.state==='failure'?'Failed':'Unknown',s.evidence.state),element('span',s.description||s.evidence.reason,'subtext'));tableRow('runtime',[link(s.name,s.target_url),g.title,'Unknown',status,sha.slice(0,12),s.created_at]);}
  if(!byId('runtime').children.length)empty('runtime',6,'Unknown · 当前筛选没有已回报的资源');
  for(const c of checks)tableRow('checks',[link(c.name,c.html_url),c.status,badge(c.conclusion||'pending'),c.completed_at||c.started_at||'Unknown']);
  if(!checks.length)empty('checks',4,'No Checks · 不据此推断运行时健康');
  for(const d of deployments)tableRow('deployments',[link(d.environment,`https://github.com/${REPO}/deployments`),badge(d.latest?.state||'Unknown'),d.sha.slice(0,12),d.latest?.created_at||d.created_at]);
  if(!deployments.length)empty('deployments',4,'No Deployments · 不据此推断运行时健康');
  for(const s of latestStatuses(statuses))tableRow('statuses',[link(s.context,s.target_url),badge(s.state),s.created_at]);
  if(!statuses.length)empty('statuses',3);
}
async function refresh(){
  byId('refresh').disabled=true;snapshot=undefined;render();byId('message').textContent='正在读取 GitHub…';byId('revision').textContent='';
  try{const commit=await api('/commits/main'),sha=commit.sha;const [statuses,checkData,deployments]=await Promise.all([api(`/commits/${sha}/statuses?per_page=100`),api(`/commits/${sha}/check-runs?per_page=100`),api(`/deployments?sha=${sha}&per_page=3`)]);await Promise.all(deployments.map(async d=>{d.latest=(await api(`/deployments/${d.id}/statuses?per_page=1`))[0];}));snapshot={sha,statuses,checks:checkData.check_runs,deployments};byId('revision').textContent=`main · ${sha}`;byId('message').textContent=`读取时间 ${new Date().toLocaleString()}；刷新不会延长证据有效期。最多读取 100 条状态、100 个 Checks、3 个当前提交部署记录。`;
  }catch(error){snapshot=undefined;byId('message').textContent=`Unknown：${error.message}。仓库权限、网络或 API 限流可能导致不可用；没有回退到集群查询。`;}finally{render();byId('refresh').disabled=false;}
}
for(const [kind,title] of RESOURCE_TYPES){const option=element('option',title);option.value=kind;byId('resource-filter').append(option);}
byId('refresh').addEventListener('click',refresh);
for(const id of ['sort','show','resource-filter'])byId(id).addEventListener('change',render);
byId('search').addEventListener('input',render);
setInterval(render,15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)render();});refresh();
