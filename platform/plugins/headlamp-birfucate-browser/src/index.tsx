import { registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { request } from '@kinvolk/headlamp-plugin/lib/ApiProxy';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem, Select, TextField, Typography } from '@mui/material';
import React, { useEffect, useMemo, useState } from 'react';
import { TENANT_TOOL_COUNT, TENANT_TOOL_GROUPS, ToolGroup } from './tenant-tool-catalog';

const BIRFUCATE_PROXY = '/api/v1/namespaces/observability-ai/services/http:birfucate-metering:9791/proxy';
const METRICS = [
  'birfucate_occupancy_current',
  'birfucate_occupancy_units_total',
  'birfucate_bifurcation_occurrences_total',
  'birfucate_bifurcation_intensity',
  'birfucate_bifurcation_fanout',
  'birfucate_bifurcation_score_total',
  'birfucate_metered_cost_total',
  'birfucate_tenant_cost_total',
  'birfucate_tenant_resource_count',
] as const;

type Labels = Record<string, string>;
type Sample = { metric: Labels; value: [number, string] };
type KubeObject = { apiVersion?: string; kind?: string; metadata?: {name?: string; namespace?: string; labels?: Labels}; spec?: Record<string, any>; status?: Record<string, any> };
type TenantView = {
  name: string; displayName: string; lifecycle: string; visibility: string;
  hostingMode: string; byocAllowed: boolean; runtimeNamespace: string;
  businessNamespaces: string[]; observedNamespaces: string[];
  identities: KubeObject[]; grants: KubeObject[]; registrations: KubeObject[];
};
type ResourceFlow = {id:string; label:string; resourceClass:string; count:number; toolCount:number; disposition:'retained'|'local'|'external'; destinations:string[]; detail:string};
type Row = {
  tenant: string; resource: string; domain: string; meter: string;
  occupancy: number; occupiedTime: number; occurrences: number;
  intensity: number; fanout: number; score: number; cost: number;
  currency: string; priced: boolean; resourceType: string; purpose: string;
};

function n(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function human(value: number): string {
  return new Intl.NumberFormat(undefined, {maximumFractionDigits: 2, notation: Math.abs(value) >= 100000 ? 'compact' : 'standard'}).format(value);
}

async function instantQuery(query: string): Promise<Sample[]> {
  const path = `${BIRFUCATE_PROXY}/api/v1/browser/query?metric=${encodeURIComponent(query)}`;
  // request() binds the URL to Headlamp's currently selected cluster. Calling
  // clusterRequest() without that cluster silently targets the Headlamp SPA and
  // returns index.html, which then fails JSON parsing at "<!DOCTYPE".
  const response: any = await request(path, {method: 'GET'});
  if (response?.status !== 'success') throw new Error(response?.error || 'Birfucate query failed');
  return response?.data?.result || [];
}

async function apiList(path: string): Promise<KubeObject[]> {
  const response: any = await request(path, {method: 'GET'});
  return response?.items || [];
}

function key(labels: Labels): string {
  return [labels.tenant || '', labels.resource_ref || '', labels.domain || '', labels.meter || ''].join('\u0000');
}

function ResourceRing({tenant, flows}: {tenant:string; flows:ResourceFlow[]}) {
  const colors = {retained:'#2e7d32',local:'#5c6bc0',external:'#ef6c00'};
  const labels = {retained:'储蓄 / 回流',local:'本租户消耗',external:'外部租户分流'};
  const cx=450, cy=270, radius=185;
  return <Box sx={{overflowX:'auto'}}>
    <Box sx={{display:'flex',gap:1,flexWrap:'wrap',mb:1}}>{Object.entries(labels).map(([key,label])=><Chip key={key} size="small" label={label} sx={{borderColor:colors[key as keyof typeof colors],borderWidth:2,borderStyle:'solid'}}/>)}</Box>
    <svg role="img" aria-label="Tenant circular resource bifurcation flow" viewBox="0 0 900 540" style={{minWidth:760,width:'100%',height:540}}>
      <defs>{Object.entries(colors).map(([key,color])=><marker key={key} id={`arrow-${key}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill={color}/></marker>)}</defs>
      <circle cx={cx} cy={cy} r="82" fill="#263238" opacity=".94"/><circle cx={cx} cy={cy} r="104" fill="none" stroke="#90a4ae" strokeWidth="2" strokeDasharray="5 6"/>
      <text x={cx} y={cy-8} textAnchor="middle" fill="white" fontSize="22" fontWeight="700">{tenant || 'all tenants'}</text><text x={cx} y={cy+20} textAnchor="middle" fill="white" fontSize="13">Tenant resource pool</text>
      {flows.map((flow,index)=>{const angle=-Math.PI/2+index*(Math.PI*2/Math.max(flows.length,1));const x=cx+Math.cos(angle)*radius,y=cy+Math.sin(angle)*radius;const ox=cx+Math.cos(angle)*(radius+86),oy=cy+Math.sin(angle)*(radius+86);const color=colors[flow.disposition];const anchor=x<cx-20?'end':x>cx+20?'start':'middle';const tx=x+(anchor==='end'?-34:anchor==='start'?34:0);const ty=y+(y<cy?-30:42);return <g key={flow.id}>
        <path d={`M ${cx+Math.cos(angle)*105} ${cy+Math.sin(angle)*105} Q ${cx+Math.cos(angle+.25)*145} ${cy+Math.sin(angle+.25)*145} ${x} ${y}`} fill="none" stroke={color} strokeWidth={3+Math.min(flow.count,9)} opacity=".72" markerEnd={`url(#arrow-${flow.disposition})`}/>
        {flow.disposition==='retained'&&<path d={`M ${x} ${y} Q ${cx+Math.cos(angle-.55)*155} ${cy+Math.sin(angle-.55)*155} ${cx+Math.cos(angle-.2)*105} ${cy+Math.sin(angle-.2)*105}`} fill="none" stroke={color} strokeWidth="3" strokeDasharray="7 5" markerEnd="url(#arrow-retained)"/>}
        {flow.disposition==='external'&&<path d={`M ${x} ${y} L ${ox} ${oy}`} fill="none" stroke={color} strokeWidth="3" strokeDasharray="8 5" markerEnd="url(#arrow-external)"/>}
        <circle cx={x} cy={y} r="29" fill={color}/><text x={x} y={y-2} textAnchor="middle" fill="white" fontSize="13" fontWeight="700">{flow.count} res</text><text x={x} y={y+13} textAnchor="middle" fill="white" fontSize="9">{flow.toolCount} tools</text>
        <text x={tx} y={ty} textAnchor={anchor} fill="currentColor" fontSize="13" fontWeight="700">{flow.label}</text><text x={tx} y={ty+17} textAnchor={anchor} fill="currentColor" fontSize="11">{labels[flow.disposition]}</text>
        {flow.disposition==='external'&&<text x={ox} y={oy+(oy<cy?-9:16)} textAnchor={ox<cx?'end':'start'} fill={color} fontSize="11">{flow.destinations.join(' / ')||'external'}</text>}
      </g>})}
      {!flows.length&&<text x={cx} y={cy+145} textAnchor="middle" fill="currentColor">暂无已授权或已发现的资源流</text>}
    </svg>
  </Box>;
}

function Dashboard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [tenant, setTenant] = useState('re8ch');
  const [domain, setDomain] = useState('');
  const [resource, setResource] = useState('');
  const [tenantCosts, setTenantCosts] = useState<Sample[]>([]);
  const [tenantResources, setTenantResources] = useState<Sample[]>([]);
  const [tenantViews, setTenantViews] = useState<TenantView[]>([]);
  const [workloads, setWorkloads] = useState<KubeObject[]>([]);
  const [serviceInstances, setServiceInstances] = useState<KubeObject[]>([]);
  const [connections, setConnections] = useState<KubeObject[]>([]);
  const [consumptionBindings, setConsumptionBindings] = useState<KubeObject[]>([]);

  async function refresh() {
    setLoading(true); setError('');
    try {
      const [results, tenantObjects, identityObjects, grantObjects, registrations, namespaces, deployments, statefulSets, daemonSets, jobs, cronJobs, nuclioFunctions, instances, clusterConnections, bindings] = await Promise.all([
        Promise.all(METRICS.map(metric => instantQuery(metric))),
        apiList('/apis/tenancy.re8ch.com/v1alpha1/tenants'),
        apiList('/apis/tenancy.re8ch.com/v1alpha1/tenantidentitybindings'),
        apiList('/apis/tenancy.re8ch.com/v1alpha1/tenantgrants'),
        apiList('/apis/finops.re8ch.com/v1alpha1/clusterregistrations'),
        apiList('/api/v1/namespaces'),
        apiList('/apis/apps/v1/deployments'),
        apiList('/apis/apps/v1/statefulsets'),
        apiList('/apis/apps/v1/daemonsets'),
        apiList('/apis/batch/v1/jobs'),
        apiList('/apis/batch/v1/cronjobs'),
        apiList('/apis/nuclio.io/v1beta1/nucliofunctions'),
        apiList('/apis/tenancy.re8ch.com/v1alpha1/tenantserviceinstances'),
        apiList('/apis/finops.re8ch.com/v1alpha1/clusterconnections'),
        apiList('/apis/finops.re8ch.com/v1alpha1/consumptionbindings'),
      ]);
      const typedWorkloads = [
        ...deployments.map(x=>({...x,kind:'Deployment'})), ...statefulSets.map(x=>({...x,kind:'StatefulSet'})),
        ...daemonSets.map(x=>({...x,kind:'DaemonSet'})), ...jobs.map(x=>({...x,kind:'Job'})),
        ...cronJobs.map(x=>({...x,kind:'CronJob'})), ...nuclioFunctions.map(x=>({...x,kind:'NuclioFunction'})),
      ];
      setWorkloads(typedWorkloads); setServiceInstances(instances); setConnections(clusterConnections); setConsumptionBindings(bindings);
      setTenantViews(tenantObjects.map(item => {
        const name = item.metadata?.name || '';
        const spec = item.spec || {};
        return {
          name,
          displayName: spec.displayName || name,
          lifecycle: spec.lifecycle || 'Unknown',
          visibility: spec.visibility || 'Private',
          hostingMode: spec.hostingMode || 'platform-managed',
          byocAllowed: spec.byocAllowed === true,
          runtimeNamespace: spec.runtimeNamespaceRef || '-',
          businessNamespaces: spec.businessNamespaces || [],
          observedNamespaces: namespaces.filter(namespace => namespace.metadata?.labels?.['saas.re8ch.com/tenant'] === name).map(namespace => namespace.metadata?.name || '').filter(Boolean),
          identities: identityObjects.filter(binding => binding.spec?.tenantRef === name),
          grants: grantObjects.filter(grant => grant.spec?.tenantRef === name),
          registrations: registrations.filter(registration => registration.spec?.tenantRef === name),
        };
      }));
      setTenantCosts(results[7]); setTenantResources(results[8]);
      const byKey = new Map<string, Row>();
      results.slice(0, 7).forEach((samples, index) => samples.forEach(sample => {
        const labels = sample.metric || {};
        if (!labels.tenant || !labels.resource_ref) return;
        const id = key(labels);
        const row = byKey.get(id) || {tenant: labels.tenant, resource: labels.resource_ref, domain: labels.domain || '-', meter: labels.meter || '-', occupancy: 0, occupiedTime: 0, occurrences: 0, intensity: 0, fanout: 0, score: 0, cost:0, currency:labels.currency || 'CNY', priced:labels.priced === 'true', resourceType:labels.resource_type || labels.domain || '-', purpose:labels.purpose || labels.meter || '-'};
        const value = n(sample.value?.[1]);
        if (index === 0) row.occupancy = value;
        if (index === 1) row.occupiedTime = value;
        if (index === 2) row.occurrences = value;
        if (index === 3) row.intensity = value;
        if (index === 4) row.fanout = value;
        if (index === 5) row.score = value;
        if (index === 6) { row.cost = value; row.currency=labels.currency || row.currency; row.priced=labels.priced === 'true'; }
        byKey.set(id, row);
      }));
      setRows([...byKey.values()].sort((a,b) => b.score - a.score || b.occupiedTime - a.occupiedTime));
    } catch (caught) { setError(String(caught)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);
  const tenants = useMemo(() => [...new Set([...rows.map(x => x.tenant), ...tenantViews.map(x => x.name)])].sort(), [rows, tenantViews]);
  const domains = useMemo(() => [...new Set(rows.map(x => x.domain))].sort(), [rows]);
  const visible = rows.filter(x => (!tenant || x.tenant === tenant) && (!domain || x.domain === domain) && (!resource || x.resource.toLowerCase().includes(resource.toLowerCase())));
  const resources = new Set(visible.map(x => `${x.tenant}\u0000${x.resource}`)).size;
  const occupancy = visible.reduce((sum,x) => sum + x.occupiedTime, 0);
  const score = visible.reduce((sum,x) => sum + x.score, 0);
  const cost = visible.reduce((sum,x) => sum + x.cost, 0);
  const selectedTenantCost = tenantCosts.filter(x=>!tenant || x.metric.tenant===tenant).reduce((sum,x)=>sum+n(x.value?.[1]),0);
  const selectedTenantResources = tenantResources.filter(x=>!tenant || x.metric.tenant===tenant).reduce((sum,x)=>sum+n(x.value?.[1]),0);
  const currency = visible.find(x=>x.currency)?.currency || tenantCosts[0]?.metric.currency || 'CNY';
  const visibleTenantViews = tenantViews.filter(x => !tenant || x.name === tenant);
  const namespaceOwners = new Map(tenantViews.flatMap(view=>view.businessNamespaces.map(namespace=>[namespace,view.name] as [string,string])));
  const visibleWorkloads = workloads.filter(item => {
    const owner=namespaceOwners.get(item.metadata?.namespace || '');
    return owner && (!tenant || owner===tenant);
  });
  const visibleInstances = serviceInstances.filter(item=>!tenant || item.spec?.tenantRef===tenant);
  const visibleBindings = consumptionBindings.filter(item=>!tenant || item.spec?.tenantRef===tenant);
  const visibleConnections = connections.filter(item=>!tenant || item.spec?.tenantRef===tenant);
  const externalDestinations = [...new Set([
    ...visibleBindings.flatMap(item=>item.spec?.access?.cilium?.destinationClusters || []),
    ...visibleConnections.map(item=>item.spec?.clusterUID).filter(Boolean),
  ])];
  const classLabels:Record<string,string> = {
    'compute.workload.v1':'Workloads','storage.volume.v1':'Volumes','database.postgresql.shared.v1':'PostgreSQL',
    'network.tenant.v1':'Tenant network','network.among-clusters.v1':'AmongClusters','observability.grafana.v1':'Grafana',
    'registry.harbor.v1':'Harbor registry','storage.bucket.v1':'Object storage','ai.model-gateway.v1':'Model gateway','sandbox.runtime.v1':'Sandbox',
  };
  const retainedClasses=new Set(['storage.volume.v1','storage.bucket.v1','database.postgresql.shared.v1','registry.harbor.v1']);
  const externalClasses=new Set(['network.among-clusters.v1']);
  const selectedGrants=visibleTenantViews.flatMap(view=>view.grants);
  const resourceFlows:ResourceFlow[] = selectedGrants.map(grant=>{const resourceClass=String(grant.spec?.consumableRef||'unknown');const disposition=externalClasses.has(resourceClass)?'external':retainedClasses.has(resourceClass)?'retained':'local';const instances=visibleInstances.filter(item=>item.spec?.serviceClass===resourceClass).length;const toolCount=TENANT_TOOL_GROUPS.filter(group=>group.resourceClass===resourceClass||group.resourceClass==='all service classes').reduce((sum,group)=>sum+group.tools.length,0);const count=resourceClass==='compute.workload.v1'?visibleWorkloads.length:instances;return {id:resourceClass,label:classLabels[resourceClass]||resourceClass,resourceClass,count,toolCount,disposition,destinations:disposition==='external'?externalDestinations:[],detail:`grant Active · ${instances} service instances`};});
  if (visibleConnections.length && !resourceFlows.some(flow=>flow.id==='byoc')) resourceFlows.push({id:'byoc',label:'BYOC connections',resourceClass:'byoc.cluster',count:visibleConnections.length,toolCount:4,disposition:'external',destinations:externalDestinations,detail:'connected'});

  return <Box sx={{p: 2}}>
    <Box sx={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:2,mb:1}}>
      <Box><Typography variant="h4">Birfucate Browser</Typography><Typography color="text.secondary">租户资源占用与消费行为分叉的只读观察面</Typography></Box>
      <Button variant="outlined" onClick={() => void refresh()} disabled={loading}>{loading ? '读取中…' : '刷新'}</Button>
    </Box>
    <Alert severity="info" sx={{mb:2}}>只读 showback：费用来自 Chart 中经版本管理的费率卡，仅供计量归因，不会创建账单或入账。</Alert>
    {error && <Alert severity="error" sx={{mb:2}}>无法读取 Birfucate 指标：{error}</Alert>}
    <Box sx={{display:'flex',gap:2,flexWrap:'wrap',mb:2}}>
      <FormControl size="small" sx={{minWidth:180}}><InputLabel>Tenant</InputLabel><Select value={tenant} label="Tenant" onChange={e=>setTenant(String(e.target.value))}><MenuItem value="">全部</MenuItem>{tenants.map(x=><MenuItem key={x} value={x}>{x}</MenuItem>)}</Select></FormControl>
      <FormControl size="small" sx={{minWidth:160}}><InputLabel>Domain</InputLabel><Select value={domain} label="Domain" onChange={e=>setDomain(String(e.target.value))}><MenuItem value="">全部</MenuItem>{domains.map(x=><MenuItem key={x} value={x}>{x}</MenuItem>)}</Select></FormControl>
      <TextField size="small" label="Resource contains" value={resource} onChange={e=>setResource(e.target.value)}/>
      <Chip label={`${resources} visible resources`}/><Chip label={`${visibleWorkloads.length} owned workloads`}/><Chip label={`${TENANT_TOOL_COUNT} Tenant tools`}/><Chip label={`${human(selectedTenantResources)} tenant resources`}/><Chip label={`${human(occupancy)} occupancy units`}/><Chip color="primary" label={`${human(score)} bifurcation score`}/><Chip color="secondary" label={`${currency} ${human(cost || selectedTenantCost)} metered cost`}/>
    </Box>
    <SectionBox title={`Tenant contracts (${visibleTenantViews.length})`}>
      <Table data={visibleTenantViews} columns={[
        {header:'Tenant',accessorFn:(x:TenantView)=><Box><Typography variant="body2">{x.displayName}</Typography><Typography variant="caption" color="text.secondary">{x.name} · 业务租户</Typography></Box>},
        {header:'Lifecycle',accessorFn:(x:TenantView)=><Box><StatusLabel status={x.lifecycle === 'Active' ? 'success' : 'warning'}>{x.lifecycle}</StatusLabel><Typography variant="caption" display="block" color="text.secondary">{x.visibility}</Typography></Box>},
        {header:'Hosting / BYOC',accessorFn:(x:TenantView)=><Box><Chip size="small" label={x.hostingMode === 'platform-managed' ? '平台托管' : x.hostingMode}/><Typography variant="caption" display="block" color="text.secondary">BYOC {x.byocAllowed ? `允许 · ${x.registrations.length} registrations` : '禁用'}</Typography></Box>},
        {header:'Namespaces',accessorFn:(x:TenantView)=><Box><Typography variant="body2">runtime: {x.runtimeNamespace}</Typography><Typography variant="caption" color="text.secondary">声明 {x.businessNamespaces.join(', ') || '-'}<br/>已标记 {x.observedNamespaces.join(', ') || '-'}</Typography></Box>},
        {header:'OIDC identities',accessorFn:(x:TenantView)=><Box>{x.identities.map(binding=><Chip key={binding.metadata?.name} size="small" variant="outlined" label={`${binding.spec?.group} · ${binding.spec?.role}`} sx={{mr:.5,mb:.5}}/>)}<Typography variant="caption" display="block" color="text.secondary">{[...new Set(x.identities.map(binding=>binding.spec?.issuer))].filter(Boolean).join(', ') || '-'}</Typography></Box>},
        {header:'Granted capabilities',accessorFn:(x:TenantView)=><Box>{x.grants.map(grant=><Chip key={grant.metadata?.name} size="small" label={grant.spec?.consumableRef} sx={{mr:.5,mb:.5}}/>)}</Box>},
      ] as any}/>
    </SectionBox>
    <SectionBox title={`${tenant || 'All tenants'} resource ring`}><ResourceRing tenant={tenant} flows={resourceFlows}/></SectionBox>
    <SectionBox title={`Resource classes (${resourceFlows.length})`}>
      <Table data={resourceFlows} columns={[
        {header:'Resource class',accessorFn:(x:ResourceFlow)=><Box><Typography variant="body2">{x.label}</Typography><Typography variant="caption" color="text.secondary">{x.resourceClass}</Typography></Box>},
        {header:'Flow',accessorFn:(x:ResourceFlow)=><StatusLabel status={x.disposition==='retained'?'success':x.disposition==='external'?'warning':'info'}>{x.disposition==='retained'?'储蓄 / 回流':x.disposition==='external'?'外部租户分流':'本租户消耗'}</StatusLabel>},
        {header:'Observed resources',accessorFn:(x:ResourceFlow)=><Box>{x.count}<Typography variant="caption" display="block" color="text.secondary">{x.detail}</Typography></Box>},
        {header:'Tenant tools',accessorFn:(x:ResourceFlow)=>x.toolCount},
        {header:'External destination',accessorFn:(x:ResourceFlow)=>x.destinations.join(', ')||'-'},
      ] as any}/>
    </SectionBox>
    <SectionBox title={`Re8ch Tenant tool coverage (${TENANT_TOOL_COUNT})`}>
      <Table data={TENANT_TOOL_GROUPS} columns={[
        {header:'Resource category',accessorFn:(x:ToolGroup)=><Box><Typography variant="body2">{x.category}</Typography><Typography variant="caption" color="text.secondary">{x.resourceClass}</Typography></Box>},
        {header:'Flow class',accessorFn:(x:ToolGroup)=><StatusLabel status={x.disposition==='retained'?'success':x.disposition==='external'?'warning':'info'}>{x.disposition==='retained'?'储蓄 / 回流':x.disposition==='external'?'外部租户分流':'本租户消耗'}</StatusLabel>},
        {header:'Tools',accessorFn:(x:ToolGroup)=><Box><Chip size="small" label={`${x.tools.length} tools`} sx={{mr:.5}}/>{x.tools.map(tool=><Chip key={tool} size="small" variant="outlined" label={tool} sx={{mr:.5,mb:.5}}/>)}</Box>},
      ] as any}/>
    </SectionBox>
    <SectionBox title={`Owned namespace workloads (${visibleWorkloads.length})`}>
      <Table data={visibleWorkloads} columns={[
        {header:'Namespace / Workload',accessorFn:(x:KubeObject)=><Box><Typography variant="body2">{x.metadata?.namespace} / {x.metadata?.name}</Typography><Typography variant="caption" color="text.secondary">{x.kind}</Typography></Box>},
        {header:'Tenant',accessorFn:(x:KubeObject)=>namespaceOwners.get(x.metadata?.namespace||'')||'-'},
        {header:'Harbor-backed image',accessorFn:(x:KubeObject)=>{const podSpec=x.kind==='CronJob'?x.spec?.jobTemplate?.spec?.template?.spec:x.kind==='Job'?x.spec?.template?.spec:x.spec?.template?.spec;const images=(podSpec?.containers||[]).map((container:any)=>container.image||'');const harbor=images.filter((image:string)=>image.includes('registry.re8ch.com'));return harbor.length?<StatusLabel status="success">{harbor.length} image(s)</StatusLabel>:<StatusLabel status="warning">未发现</StatusLabel>;}},
        {header:'Resource class',accessorFn:()=> 'compute.workload.v1'},
      ] as any}/>
    </SectionBox>
    <SectionBox title={`Re8ch Tenant tool coverage (${TENANT_TOOL_COUNT}/56)`}>
      <Table data={TENANT_TOOL_GROUPS} columns={[
        {header:'Category',accessorFn:(x:ToolGroup)=><Box><Typography variant="body2">{x.category}</Typography><Typography variant="caption" color="text.secondary">{x.resourceClass}</Typography></Box>},
        {header:'Flow',accessorFn:(x:ToolGroup)=><StatusLabel status={x.disposition==='retained'?'success':x.disposition==='external'?'warning':'info'}>{x.disposition==='retained'?'储蓄 / 回流':x.disposition==='external'?'外部租户分流':'本租户消耗'}</StatusLabel>},
        {header:'Tools',accessorFn:(x:ToolGroup)=><Box>{x.tools.map(tool=><Chip key={tool} size="small" variant="outlined" label={tool} sx={{mr:.5,mb:.5}}/>)}</Box>},
        {header:'Count',accessorFn:(x:ToolGroup)=>x.tools.length},
      ] as any}/>
    </SectionBox>
    <SectionBox title={`Resource branches (${visible.length})`}>
      <Table data={visible} columns={[
        {header:'Tenant / Resource',accessorFn:(x:Row)=><Box><Typography variant="body2">{x.tenant}</Typography><Typography variant="caption" color="text.secondary">{x.resource}</Typography></Box>},
        {header:'Flow',accessorFn:(x:Row)=><Box><Box sx={{display:'flex',gap:.5}}><Chip size="small" label={x.resourceType}/><Chip size="small" color="primary" variant="outlined" label={x.purpose}/></Box><Typography variant="caption" color="text.secondary">{x.domain} / {x.meter}</Typography></Box>},
        {header:'Occupancy',accessorFn:(x:Row)=><Box>{human(x.occupancy)}<Typography variant="caption" display="block" color="text.secondary">Σ {human(x.occupiedTime)}</Typography></Box>},
        {header:'Occurrences',accessorFn:(x:Row)=>human(x.occurrences)},
        {header:'Intensity',accessorFn:(x:Row)=>human(x.intensity)},
        {header:'Fanout',accessorFn:(x:Row)=>human(x.fanout)},
        {header:'Bifurcation',accessorFn:(x:Row)=><StatusLabel status={x.score > 0 ? 'success' : 'warning'}>{human(x.score)}</StatusLabel>},
        {header:'Metered cost',accessorFn:(x:Row)=>x.priced ? `${x.currency} ${human(x.cost)}` : <StatusLabel status="warning">未定价</StatusLabel>},
      ] as any}/>
    </SectionBox>
    <Typography variant="caption" color="text.secondary">数据源：Birfucate Chart 提供的受限浏览 API（最近 10 分钟）。Tenant 筛选仅用于浏览；授权由 Headlamp 与 Kubernetes services/proxy RBAC 执行。</Typography>
  </Box>;
}

registerSidebarEntry({name:'birfucate-browser',url:'/birfucate',icon:'mdi:source-branch',parent:'',label:'Birfucate'});
registerRoute({path:'/birfucate',sidebar:'birfucate-browser',name:'Birfucate Browser',component:()=> <Dashboard/>});
