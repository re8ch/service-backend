import { registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { request } from '@kinvolk/headlamp-plugin/lib/ApiProxy';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem, Select, TextField, Typography } from '@mui/material';
import React, { useEffect, useMemo, useState } from 'react';

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

function key(labels: Labels): string {
  return [labels.tenant || '', labels.resource_ref || '', labels.domain || '', labels.meter || ''].join('\u0000');
}

function FlowDiagram({rows}: {rows: Row[]}) {
  const branches = useMemo(() => {
    const grouped = new Map<string, {tenant:string; resourceType:string; purpose:string; value:number; cost:number}>();
    rows.forEach(row => {
      const id = `${row.tenant}\u0000${row.resourceType}\u0000${row.purpose}`;
      const branch = grouped.get(id) || {tenant:row.tenant,resourceType:row.resourceType,purpose:row.purpose,value:0,cost:0};
      branch.cost += row.cost; branch.value += row.cost || row.occupiedTime || row.score;
      grouped.set(id, branch);
    });
    return [...grouped.values()].sort((a,b) => b.value-a.value).slice(0, 12);
  }, [rows]);
  const max = Math.max(...branches.map(x=>x.value), 1);
  const height = Math.max(180, branches.length * 38 + 32);
  return <Box sx={{overflowX:'auto'}}><svg role="img" aria-label="Tenant resource purpose flow" viewBox={`0 0 900 ${height}`} style={{minWidth:760,width:'100%',height}}>
    <text x="20" y="20" fontSize="12" fill="currentColor">TENANT</text><text x="340" y="20" fontSize="12" fill="currentColor">RESOURCE TYPE</text><text x="700" y="20" fontSize="12" fill="currentColor">PURPOSE</text>
    {branches.map((branch,index) => { const y=48+index*38; const width=2+16*branch.value/max; return <g key={`${branch.resourceType}-${branch.purpose}`}>
      <path d={`M 95 ${y} C 210 ${y}, 230 ${y}, 335 ${y}`} stroke="#5c6bc0" strokeWidth={width} opacity=".6" fill="none"/>
      <path d={`M 475 ${y} C 585 ${y}, 600 ${y}, 695 ${y}`} stroke="#26a69a" strokeWidth={width} opacity=".6" fill="none"/>
      <text x="20" y={y+4} fontSize="12" fill="currentColor">{branch.tenant}</text><text x="345" y={y+4} fontSize="12" fill="currentColor">{branch.resourceType}</text><text x="705" y={y+4} fontSize="12" fill="currentColor">{branch.purpose} · {human(branch.cost)}</text>
    </g>;})}
    {!branches.length && <text x="20" y="62" fill="currentColor">暂无可显示的租户支流</text>}
  </svg></Box>;
}

function Dashboard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [tenant, setTenant] = useState('');
  const [domain, setDomain] = useState('');
  const [resource, setResource] = useState('');
  const [tenantCosts, setTenantCosts] = useState<Sample[]>([]);
  const [tenantResources, setTenantResources] = useState<Sample[]>([]);

  async function refresh() {
    setLoading(true); setError('');
    try {
      const results = await Promise.all(METRICS.map(metric => instantQuery(metric)));
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
  const tenants = useMemo(() => [...new Set(rows.map(x => x.tenant))].sort(), [rows]);
  const domains = useMemo(() => [...new Set(rows.map(x => x.domain))].sort(), [rows]);
  const visible = rows.filter(x => (!tenant || x.tenant === tenant) && (!domain || x.domain === domain) && (!resource || x.resource.toLowerCase().includes(resource.toLowerCase())));
  const resources = new Set(visible.map(x => `${x.tenant}\u0000${x.resource}`)).size;
  const occupancy = visible.reduce((sum,x) => sum + x.occupiedTime, 0);
  const score = visible.reduce((sum,x) => sum + x.score, 0);
  const cost = visible.reduce((sum,x) => sum + x.cost, 0);
  const selectedTenantCost = tenantCosts.filter(x=>!tenant || x.metric.tenant===tenant).reduce((sum,x)=>sum+n(x.value?.[1]),0);
  const selectedTenantResources = tenantResources.filter(x=>!tenant || x.metric.tenant===tenant).reduce((sum,x)=>sum+n(x.value?.[1]),0);
  const currency = visible.find(x=>x.currency)?.currency || tenantCosts[0]?.metric.currency || 'CNY';

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
      <Chip label={`${resources} visible resources`}/><Chip label={`${human(selectedTenantResources)} tenant resources`}/><Chip label={`${human(occupancy)} occupancy units`}/><Chip color="primary" label={`${human(score)} bifurcation score`}/><Chip color="secondary" label={`${currency} ${human(cost || selectedTenantCost)} metered cost`}/>
    </Box>
    <SectionBox title="Tenant → Resource type → Purpose"><FlowDiagram rows={visible}/></SectionBox>
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
