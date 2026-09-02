import { registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { clusterRequest } from '@kinvolk/headlamp-plugin/lib/ApiProxy';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem, Select, TextField, Typography } from '@mui/material';
import React, { useEffect, useMemo, useState } from 'react';

const VM_PROXY = '/api/v1/namespaces/qianwen-ops/services/http:panel-victoria-metrics:8428/proxy';
const METRICS = [
  'birfucate_occupancy_current',
  'birfucate_occupancy_units_total',
  'birfucate_bifurcation_occurrences_total',
  'birfucate_bifurcation_intensity',
  'birfucate_bifurcation_fanout',
  'birfucate_bifurcation_score_total',
] as const;

type Labels = Record<string, string>;
type Sample = { metric: Labels; value: [number, string] };
type Row = {
  tenant: string; resource: string; domain: string; meter: string;
  occupancy: number; occupiedTime: number; occurrences: number;
  intensity: number; fanout: number; score: number;
};

function n(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function human(value: number): string {
  return new Intl.NumberFormat(undefined, {maximumFractionDigits: 2, notation: Math.abs(value) >= 100000 ? 'compact' : 'standard'}).format(value);
}

async function instantQuery(query: string): Promise<Sample[]> {
  const path = `${VM_PROXY}/api/v1/query?query=${encodeURIComponent(query)}`;
  const response: any = await clusterRequest(path, {method: 'GET'});
  if (response?.status !== 'success') throw new Error(response?.error || 'VictoriaMetrics query failed');
  return response?.data?.result || [];
}

function key(labels: Labels): string {
  return [labels.tenant || '', labels.resource_ref || '', labels.domain || '', labels.meter || ''].join('\u0000');
}

function Dashboard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [tenant, setTenant] = useState('');
  const [domain, setDomain] = useState('');
  const [resource, setResource] = useState('');

  async function refresh() {
    setLoading(true); setError('');
    try {
      const results = await Promise.all(METRICS.map(metric => instantQuery(`last_over_time(${metric}[10m])`)));
      const byKey = new Map<string, Row>();
      results.forEach((samples, index) => samples.forEach(sample => {
        const labels = sample.metric || {};
        if (!labels.tenant || !labels.resource_ref) return;
        const id = key(labels);
        const row = byKey.get(id) || {tenant: labels.tenant, resource: labels.resource_ref, domain: labels.domain || '-', meter: labels.meter || '-', occupancy: 0, occupiedTime: 0, occurrences: 0, intensity: 0, fanout: 0, score: 0};
        const value = n(sample.value?.[1]);
        if (index === 0) row.occupancy = value;
        if (index === 1) row.occupiedTime = value;
        if (index === 2) row.occurrences = value;
        if (index === 3) row.intensity = value;
        if (index === 4) row.fanout = value;
        if (index === 5) row.score = value;
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

  return <Box sx={{p: 2}}>
    <Box sx={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:2,mb:1}}>
      <Box><Typography variant="h4">Birfucate Browser</Typography><Typography color="text.secondary">租户资源占用与消费行为分叉的只读观察面</Typography></Box>
      <Button variant="outlined" onClick={() => void refresh()} disabled={loading}>{loading ? '读取中…' : '刷新'}</Button>
    </Box>
    <Alert severity="info" sx={{mb:2}}>只读页面：不会创建、更新、暂停、删除资源，也不会显示凭据或执行计费。</Alert>
    {error && <Alert severity="error" sx={{mb:2}}>无法读取 Birfucate 指标：{error}</Alert>}
    <Box sx={{display:'flex',gap:2,flexWrap:'wrap',mb:2}}>
      <FormControl size="small" sx={{minWidth:180}}><InputLabel>Tenant</InputLabel><Select value={tenant} label="Tenant" onChange={e=>setTenant(String(e.target.value))}><MenuItem value="">全部</MenuItem>{tenants.map(x=><MenuItem key={x} value={x}>{x}</MenuItem>)}</Select></FormControl>
      <FormControl size="small" sx={{minWidth:160}}><InputLabel>Domain</InputLabel><Select value={domain} label="Domain" onChange={e=>setDomain(String(e.target.value))}><MenuItem value="">全部</MenuItem>{domains.map(x=><MenuItem key={x} value={x}>{x}</MenuItem>)}</Select></FormControl>
      <TextField size="small" label="Resource contains" value={resource} onChange={e=>setResource(e.target.value)}/>
      <Chip label={`${resources} resources`}/><Chip label={`${human(occupancy)} occupancy units`}/><Chip color="primary" label={`${human(score)} bifurcation score`}/>
    </Box>
    <SectionBox title={`Resource branches (${visible.length})`}>
      <Table data={visible} columns={[
        {header:'Tenant / Resource',accessorFn:(x:Row)=><Box><Typography variant="body2">{x.tenant}</Typography><Typography variant="caption" color="text.secondary">{x.resource}</Typography></Box>},
        {header:'Branch',accessorFn:(x:Row)=><Box sx={{display:'flex',gap:.5}}><Chip size="small" label={x.domain}/><Chip size="small" variant="outlined" label={x.meter}/></Box>},
        {header:'Occupancy',accessorFn:(x:Row)=><Box>{human(x.occupancy)}<Typography variant="caption" display="block" color="text.secondary">Σ {human(x.occupiedTime)}</Typography></Box>},
        {header:'Occurrences',accessorFn:(x:Row)=>human(x.occurrences)},
        {header:'Intensity',accessorFn:(x:Row)=>human(x.intensity)},
        {header:'Fanout',accessorFn:(x:Row)=>human(x.fanout)},
        {header:'Bifurcation',accessorFn:(x:Row)=><StatusLabel status={x.score > 0 ? 'success' : 'warning'}>{human(x.score)}</StatusLabel>},
      ] as any}/>
    </SectionBox>
    <Typography variant="caption" color="text.secondary">数据源：VictoriaMetrics 中最近 10 分钟的 Birfucate 指标。Tenant 筛选仅用于浏览；授权由 Headlamp 与 Kubernetes services/proxy RBAC 执行。</Typography>
  </Box>;
}

registerSidebarEntry({name:'birfucate-browser',url:'/birfucate',icon:'mdi:source-branch',parent:'',label:'Birfucate'});
registerRoute({path:'/birfucate',sidebar:'birfucate-browser',name:'Birfucate Browser',component:()=> <Dashboard/>});
