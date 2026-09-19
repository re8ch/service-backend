import { registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { request } from '@kinvolk/headlamp-plugin/lib/ApiProxy';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem, Select, Tab, Tabs, TextField, Typography } from '@mui/material';
import React, { useEffect, useMemo, useState } from 'react';
import { AllocationLane, ResourceCorridors } from './ResourceCorridors';
import { TENANT_TOOL_GROUPS, ToolGroup } from './tenant-tool-catalog';

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
type KubeObject = { apiVersion?: string; kind?: string; metadata?: {name?: string; namespace?: string; labels?: Labels; annotations?: Labels; creationTimestamp?: string; uid?: string}; spec?: Record<string, any>; status?: Record<string, any>; eventTime?: string; lastTimestamp?: string; reason?: string; note?: string; message?: string; regarding?: {kind?:string; namespace?:string; name?:string} };
type TenantView = {
  name: string; displayName: string; lifecycle: string; visibility: string;
  hostingMode: string; byocAllowed: boolean; runtimeNamespace: string;
  businessNamespaces: string[]; observedNamespaces: string[];
  identities: KubeObject[]; grants: KubeObject[]; registrations: KubeObject[];
};
type Row = {
  tenant: string; resource: string; domain: string; meter: string;
  occupancy: number; occupiedTime: number; occurrences: number;
  intensity: number; fanout: number; score: number; cost: number;
  currency: string; priced: boolean; resourceType: string; purpose: string;
};
type CapacityFact = {id:string; resourceClass:string; source:string; unit:string; capacity:number|null; allocated:number; used:number|null; scope:string; evidence:string};
type FactEvent = {id:string; time:string; tenant:string; trace:string; stage:string; resource:string; transition:string; detail:string; source:string};

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
  const response: any = await withTimeout(request(path, {method: 'GET'}), `Birfucate metric ${query}`);
  if (response?.status !== 'success') throw new Error(response?.error || 'Birfucate query failed');
  return response?.data?.result || [];
}

async function apiList(path: string): Promise<KubeObject[]> {
  const response: any = await withTimeout(request(path, {method: 'GET'}), path);
  return response?.items || [];
}

async function withTimeout<T>(promise: Promise<T>, source: string, milliseconds=8000): Promise<T> {
  return await Promise.race([
    promise,
    new Promise<T>((_resolve,reject)=>setTimeout(()=>reject(new Error(`${source} timed out after ${milliseconds}ms`)),milliseconds)),
  ]);
}

async function optionalApiList(path: string): Promise<{items:KubeObject[]; error:string}> {
  try { return {items: await apiList(path), error:''}; }
  catch (caught) { return {items:[], error:String(caught)}; }
}

function quantity(value: unknown): number {
  const raw=String(value ?? '').trim();
  if (!raw) return 0;
  const match=raw.match(/^([0-9.]+)([a-zA-Z]+)?$/);
  if (!match) return n(raw);
  const amount=n(match[1]); const suffix=match[2] || '';
  const scale:Record<string,number>={m:.001,Ki:1024,Mi:1024**2,Gi:1024**3,Ti:1024**4,k:1000,M:1000**2,G:1000**3};
  return amount*(scale[suffix] || 1);
}

function compactTime(value:string):string {
  if (!value) return '-';
  const date=new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function compactDetail(value:unknown):string {
  const text=String(value ?? '').replace(/\s+/g,' ').trim();
  return text.length > 240 ? `${text.slice(0,237)}…` : text;
}

function objectTrace(item:KubeObject):string {
  const values={...(item.metadata?.annotations || {}),...(item.metadata?.labels || {})};
  const keys=['re8ch.com/trace-id','trace-id','trace_id','re8ch.com/task-id','task-id','task_id','invoke-id','correlation-id'];
  return keys.map(key=>values[key]).find(Boolean) || String(item.spec?.idempotencyKey || item.status?.claimHash || '-');
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
  const [tenantCosts, setTenantCosts] = useState<Sample[]>([]);
  const [tenantResources, setTenantResources] = useState<Sample[]>([]);
  const [tenantViews, setTenantViews] = useState<TenantView[]>([]);
  const [workloads, setWorkloads] = useState<KubeObject[]>([]);
  const [serviceInstances, setServiceInstances] = useState<KubeObject[]>([]);
  const [consumptionBindings, setConsumptionBindings] = useState<KubeObject[]>([]);
  const [consumables, setConsumables] = useState<KubeObject[]>([]);
  const [resourceClaims, setResourceClaims] = useState<KubeObject[]>([]);
  const [resourceQuotas, setResourceQuotas] = useState<KubeObject[]>([]);
  const [nodes, setNodes] = useState<KubeObject[]>([]);
  const [clusterEvents, setClusterEvents] = useState<KubeObject[]>([]);
  const [namespaces, setNamespaces] = useState<KubeObject[]>([]);
  const [pods, setPods] = useState<KubeObject[]>([]);
  const [pvcs, setPvcs] = useState<KubeObject[]>([]);
  const [resourceClass, setResourceClass] = useState('compute.workload.v1');
  const [sourceErrors, setSourceErrors] = useState<string[]>([]);
  const [traceFilter, setTraceFilter] = useState('');

  async function refresh() {
    setLoading(true); setError('');
    try {
      const metricResults=await Promise.all(METRICS.map(metric=>instantQuery(metric).then(items=>({items,error:''})).catch(caught=>({items:[] as Sample[],error:String(caught)}))));
      const results=metricResults.map(result=>result.items);
      const paths=[
        '/apis/tenancy.re8ch.com/v1alpha1/tenants','/apis/tenancy.re8ch.com/v1alpha1/tenantidentitybindings','/apis/tenancy.re8ch.com/v1alpha1/tenantgrants',
        '/apis/finops.re8ch.com/v1alpha1/clusterregistrations','/api/v1/namespaces','/apis/apps/v1/deployments','/apis/apps/v1/statefulsets','/apis/apps/v1/daemonsets',
        '/apis/batch/v1/jobs','/apis/batch/v1/cronjobs','/apis/nuclio.io/v1beta1/nucliofunctions','/apis/tenancy.re8ch.com/v1alpha1/tenantserviceinstances',
        '/apis/finops.re8ch.com/v1alpha1/clusterconnections','/apis/finops.re8ch.com/v1alpha1/consumptionbindings','/apis/finops.re8ch.com/v1alpha1/consumables',
        '/apis/tenancy.re8ch.com/v1alpha1/tenantresourceclaims','/api/v1/resourcequotas','/api/v1/nodes','/apis/events.k8s.io/v1/events','/api/v1/pods','/api/v1/persistentvolumeclaims',
      ];
      const kubeResults=await Promise.all(paths.map(optionalApiList));
      const [tenantObjects, identityObjects, grantObjects, registrations, namespaceObjects, deployments, statefulSets, daemonSets, jobs, cronJobs, nuclioFunctions, instances, clusterConnections, bindings, consumableObjects, claims, quotas, nodeObjects, events, podObjects, pvcObjects]=kubeResults.map(result=>result.items);
      setSourceErrors([...new Set([...metricResults.map(x=>x.error),...kubeResults.map(x=>x.error)].filter(Boolean))]);
      const typedWorkloads = [
        ...deployments.map(x=>({...x,kind:'Deployment'})), ...statefulSets.map(x=>({...x,kind:'StatefulSet'})),
        ...daemonSets.map(x=>({...x,kind:'DaemonSet'})), ...jobs.map(x=>({...x,kind:'Job'})),
        ...cronJobs.map(x=>({...x,kind:'CronJob'})), ...nuclioFunctions.map(x=>({...x,kind:'NuclioFunction'})),
      ];
      setWorkloads(typedWorkloads); setServiceInstances(instances); setConsumptionBindings(bindings);
      setConsumables(consumableObjects); setResourceClaims(claims); setResourceQuotas(quotas); setNodes(nodeObjects); setClusterEvents(events); setNamespaces(namespaceObjects); setPods(podObjects); setPvcs(pvcObjects);
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
          observedNamespaces: namespaceObjects.filter(namespace => Object.values(namespace.metadata?.labels || {}).includes(name) && Object.keys(namespace.metadata?.labels || {}).some(key=>key.includes('tenant'))).map(namespace => namespace.metadata?.name || '').filter(Boolean),
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
  const tenantLabelKeys=['saas.re8ch.com/tenant','re8ch.com/tenant','tenancy.re8ch.com/tenant','app.kubernetes.io/tenant'];
  const namespaceOwners = new Map<string,string>();
  tenantViews.forEach(view=>[view.runtimeNamespace,...view.businessNamespaces,...view.observedNamespaces].filter(name=>name && name!=='-').forEach(name=>namespaceOwners.set(name,view.name)));
  namespaces.forEach(item=>{const owner=tenantLabelKeys.map(key=>item.metadata?.labels?.[key] || item.metadata?.annotations?.[key]).find(Boolean);if(owner&&item.metadata?.name)namespaceOwners.set(item.metadata.name,owner);});
  resourceClaims.forEach(item=>{const owner=String(item.spec?.tenantRef || '');const runtime=String(item.status?.runtimeNamespace || item.status?.runtimeNamespaceRef || item.status?.workloadRef?.namespace || '');if(owner&&runtime)namespaceOwners.set(runtime,owner);});
  const objectOwner=(item:KubeObject)=>tenantLabelKeys.map(key=>item.metadata?.labels?.[key] || item.metadata?.annotations?.[key]).find(Boolean) || namespaceOwners.get(item.metadata?.namespace || '') || '';
  const visibleWorkloads = workloads.filter(item => {
    const owner=objectOwner(item);
    return owner && (!tenant || owner===tenant);
  });
  const visibleInstances = serviceInstances.filter(item=>!tenant || item.spec?.tenantRef===tenant);
  const visibleBindings = consumptionBindings.filter(item=>!tenant || item.spec?.tenantRef===tenant);
  const classLabels:Record<string,string> = {
    'cluster.cpu':'CPU requests','cluster.memory':'Memory requests','cluster.network-egress':'Network egress',
    'compute.workload.v1':'Workloads','storage.volume.v1':'Volumes','database.postgresql.shared.v1':'PostgreSQL',
    'network.tenant.v1':'Tenant network','network.among-clusters.v1':'AmongClusters','observability.grafana.v1':'Grafana',
    'registry.harbor.v1':'Harbor registry','storage.bucket.v1':'Object storage','ai.model-gateway.v1':'Model gateway','sandbox.runtime.v1':'Sandbox',
  };
  const selectedGrants=visibleTenantViews.flatMap(view=>view.grants);

  const tenantNamespaces=new Set(visibleTenantViews.flatMap(view=>[view.runtimeNamespace,...view.businessNamespaces,...view.observedNamespaces]).filter(name=>name && name!=='-'));
  const quotaFacts:CapacityFact[]=resourceQuotas.filter(item=>tenantNamespaces.has(item.metadata?.namespace || '')).flatMap(item=>{
    const hard=item.status?.hard || item.spec?.hard || {}; const used=item.status?.used || {};
    return Object.keys(hard).map(dimension=>({id:`quota:${item.metadata?.namespace}:${item.metadata?.name}:${dimension}`,resourceClass:dimension,source:'ResourceQuota',unit:dimension,capacity:quantity(hard[dimension]),allocated:quantity(hard[dimension]),used:quantity(used[dimension]),scope:item.metadata?.namespace || '-',evidence:`${item.metadata?.namespace}/${item.metadata?.name}`}));
  });
  const nodeCapacity=(field:string):number|null=>nodes.length ? nodes.reduce((sum,item)=>sum+quantity(item.status?.allocatable?.[field]),0) : null;
  const clusterFacts:CapacityFact[]=[
    {id:'cluster:cpu',resourceClass:'cluster.cpu',source:'Node.status.allocatable',unit:'cores',capacity:nodeCapacity('cpu'),allocated:quotaFacts.filter(x=>x.resourceClass.includes('cpu')).reduce((sum,x)=>sum+x.allocated,0),used:null,scope:'cluster',evidence:nodes.length?`${nodes.length} nodes`:'节点来源不可读'},
    {id:'cluster:memory',resourceClass:'cluster.memory',source:'Node.status.allocatable',unit:'bytes',capacity:nodeCapacity('memory'),allocated:quotaFacts.filter(x=>x.resourceClass.includes('memory')).reduce((sum,x)=>sum+x.allocated,0),used:null,scope:'cluster',evidence:nodes.length?`${nodes.length} nodes`:'节点来源不可读'},
    {id:'cluster:pods',resourceClass:'cluster.pods',source:'Node.status.allocatable',unit:'pods',capacity:nodeCapacity('pods'),allocated:quotaFacts.filter(x=>x.resourceClass==='pods').reduce((sum,x)=>sum+x.allocated,0),used:pods.length?visibleWorkloads.length:null,scope:'cluster',evidence:nodes.length?`${nodes.length} nodes`:'节点来源不可读'},
  ];
  const consumableFacts:CapacityFact[]=consumables.filter(item=>item.spec?.lifecycle==='Approved').map(item=>{
    const resourceClass=String(item.spec?.serviceClass || item.metadata?.name || 'unknown');
    const matching=visibleBindings.filter(binding=>binding.spec?.consumableRef===item.metadata?.name || binding.spec?.consumableRef===resourceClass);
    const declared=item.status?.capacity?.limit ?? item.spec?.capacity?.limit;
    return {id:`consumable:${item.metadata?.name}`,resourceClass,source:'Consumable + ConsumptionBinding',unit:String(item.status?.capacity?.unit || item.spec?.capacity?.unit || 'instances'),capacity:declared == null ? null : n(declared),allocated:matching.length,used:matching.filter(binding=>String(binding.status?.phase || '').toLowerCase()==='ready').length,scope:String(item.spec?.owner || 'platform'),evidence:declared == null ? '未声明出租上限' : `${item.metadata?.name}`};
  });
  const capacityFacts=[...clusterFacts,...consumableFacts,...quotaFacts];
  const globalResourceClasses=[...new Set(['cluster.cpu','cluster.memory','compute.workload.v1','storage.volume.v1','cluster.network-egress',...consumables.map(item=>String(item.spec?.serviceClass || item.metadata?.name || '')).filter(Boolean)])];
  const tenantAllocation=(tenantName:string,selected:string):AllocationLane=>{
    const grants=tenantViews.find(view=>view.name===tenantName)?.grants.filter(item=>item.spec?.consumableRef===selected).length || 0;
    const bindings=consumptionBindings.filter(item=>item.spec?.tenantRef===tenantName && (item.spec?.consumableRef===selected || item.spec?.serviceClass===selected));
    const claims=resourceClaims.filter(item=>item.spec?.tenantRef===tenantName && (item.spec?.capability===selected || item.spec?.resourceClass===selected));
    const ownedPods=pods.filter(item=>objectOwner(item)===tenantName && String(item.status?.phase || '').toLowerCase()!=='succeeded');
    const ownedPvcs=pvcs.filter(item=>objectOwner(item)===tenantName);
    const metricRows=rows.filter(item=>item.tenant===tenantName && (item.resourceType===selected || item.resource===selected || item.domain===selected));
    const podRequest=(name:string)=>ownedPods.reduce((sum,pod)=>sum+(pod.spec?.containers || []).reduce((containerSum:number,container:any)=>containerSum+quantity(container.resources?.requests?.[name]),0),0);
    const pvcBytes=ownedPvcs.reduce((sum,pvc)=>sum+quantity(pvc.status?.capacity?.storage || pvc.spec?.resources?.requests?.storage),0);
    const metered=metricRows.reduce((sum,item)=>sum+(selected==='cluster.network-egress' && item.meter!=='network_egress_byte' ? 0 : item.occupiedTime || item.occupancy),0);
    const observed=selected==='cluster.cpu'?podRequest('cpu'):selected==='cluster.memory'?podRequest('memory'):selected==='compute.workload.v1'?ownedPods.length:selected==='storage.volume.v1'?pvcBytes:metered;
    const declared=bindings.length+claims.length;
    const allocated=['cluster.cpu','cluster.memory','storage.volume.v1'].includes(selected)?observed:declared;
    return {tenant:tenantName,allocated,observed,evidence:`${grants} grant · ${bindings.length} binding · ${claims.length} claim`};
  };
  const allocationLanes=tenants.map(name=>tenantAllocation(name,resourceClass)).filter(lane=>lane.allocated>0 || lane.observed>0 || tenantViews.find(view=>view.name===lane.tenant)?.grants.some(grant=>grant.spec?.consumableRef===resourceClass));
  const selectedCapacity=(resourceClass==='compute.workload.v1'?clusterFacts.find(item=>item.resourceClass==='cluster.pods')?.capacity:capacityFacts.find(item=>item.resourceClass===resourceClass && item.scope==='cluster')?.capacity) ?? consumableFacts.find(item=>item.resourceClass===resourceClass)?.capacity ?? null;
  const globalClassRows=globalResourceClasses.map(name=>{const consumable=consumables.find(item=>item.spec?.serviceClass===name || item.metadata?.name===name);const lanes=tenants.map(value=>tenantAllocation(value,name));return {name,label:classLabels[name]||name,lifecycle:String(consumable?.spec?.lifecycle || 'Undeclared'),access:String(consumable?.spec?.accessContract?.kind || consumable?.spec?.accessContract || '-'),meters:(consumable?.spec?.meters || []).map((meter:any)=>meter.name || meter).join(', ') || '-',ceiling:capacityFacts.find(item=>item.resourceClass===name)?.capacity ?? null,allocated:lanes.reduce((sum,lane)=>sum+lane.allocated,0),observed:lanes.reduce((sum,lane)=>sum+lane.observed,0)};});
  const selectedToolGroups=tenant ? TENANT_TOOL_GROUPS.filter(group=>group.resourceClass==='all service classes' || selectedGrants.some(grant=>grant.spec?.consumableRef===group.resourceClass)) : [];
  const eventOf=(item:KubeObject, stage:string, transition:string, detail:string, time?:string):FactEvent=>({id:`${stage}:${item.metadata?.uid || item.metadata?.namespace || ''}:${item.metadata?.name || ''}:${time || ''}`,time:time || item.metadata?.creationTimestamp || '',tenant:String(item.spec?.tenantRef || item.metadata?.labels?.['re8ch.com/tenant'] || '-'),trace:objectTrace(item),stage,resource:`${item.kind || stage}/${item.metadata?.namespace ? `${item.metadata.namespace}/` : ''}${item.metadata?.name || '-'}`,transition,detail:compactDetail(detail),source:item.apiVersion || 'kubernetes'});
  const factEvents:FactEvent[]=[
    ...visibleTenantViews.flatMap(view=>view.grants.map(item=>eventOf({...item,kind:'TenantGrant'},'grant',String(item.spec?.lifecycle || 'Observed'),String(item.spec?.consumableRef || '-')))),
    ...visibleInstances.map(item=>eventOf({...item,kind:'TenantServiceInstance'},'allocation',String(item.status?.phase || item.spec?.lifecycle || 'Observed'),`${item.spec?.serviceClass || '-'} · ${item.spec?.plan || '-'}`)),
    ...visibleBindings.map(item=>eventOf({...item,kind:'ConsumptionBinding'},'binding',String(item.status?.phase || 'Bound'),`${item.spec?.consumableRef || '-'} · quota ${JSON.stringify(item.spec?.quota || {})}`,String(item.spec?.startsAt || item.metadata?.creationTimestamp || ''))),
    ...resourceClaims.filter(item=>!tenant || item.spec?.tenantRef===tenant).map(item=>eventOf({...item,kind:'TenantResourceClaim'},'invoke',String(item.status?.phase || item.spec?.desiredState || 'Pending'),`${item.spec?.capability || '-'} · ttl ${item.spec?.ttlSeconds || '-'}s`)),
    ...clusterEvents.filter(item=>!tenant || (item.metadata?.namespace ? tenantNamespaces.has(item.metadata.namespace) : true)).map(item=>eventOf(item,'kubernetes',String(item.reason || 'Event'),String(item.note || item.message || ''),String(item.eventTime || item.lastTimestamp || item.metadata?.creationTimestamp || ''))),
  ].filter(item=>(!tenant || item.tenant===tenant || item.tenant==='-') && (!traceFilter || `${item.trace} ${item.resource} ${item.detail}`.toLowerCase().includes(traceFilter.toLowerCase()))).sort((a,b)=>Date.parse(b.time || '0')-Date.parse(a.time || '0')).slice(0,100);
  const traceCount=new Set(factEvents.map(event=>event.trace).filter(value=>value && value!=='-')).size;

  return <Box sx={{p: 2}}>
    <Box sx={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:2,mb:1}}>
      <Box><Typography variant="h4">Bifurcate Facts</Typography><Typography color="text.secondary">容量、分配与 Invoke 资源流的只读事实面</Typography></Box>
      <Button variant="outlined" onClick={() => void refresh()} disabled={loading}>{loading ? '读取中…' : '刷新'}</Button>
    </Box>
    <Alert severity={loading ? 'info' : sourceErrors.length || error ? 'warning' : 'success'} sx={{mb:2}}>
      Kubernetes 对象是事实来源；Birfucate 指标仅补充计量。{loading ? ' 正在等待各事实来源，当前数字不作为最终事实。' : sourceErrors.length || error ? ` ${sourceErrors.length + (error ? 1 : 0)} 个采集端点超时或不可用；未知值不会显示为容量 0。` : ' 当前采集端点正常。'}
    </Alert>
    <Box sx={{display:'flex',gap:2,flexWrap:'wrap',mb:2}}>
      <FormControl size="small" sx={{minWidth:180}}><InputLabel>Tenant</InputLabel><Select value={tenant} label="Tenant" onChange={e=>setTenant(String(e.target.value))}><MenuItem value="">全部</MenuItem>{tenants.map(x=><MenuItem key={x} value={x}>{x}</MenuItem>)}</Select></FormControl>
      <FormControl size="small" sx={{minWidth:160}}><InputLabel>Domain</InputLabel><Select value={domain} label="Domain" onChange={e=>setDomain(String(e.target.value))}><MenuItem value="">全部</MenuItem>{domains.map(x=><MenuItem key={x} value={x}>{x}</MenuItem>)}</Select></FormControl>
      <TextField size="small" label="Resource contains" value={resource} onChange={e=>setResource(e.target.value)}/>
      <TextField size="small" label="Trace / task / resource" value={traceFilter} onChange={e=>setTraceFilter(e.target.value)}/>
      <Chip label={`${resources} visible resources`}/><Chip label={`${visibleWorkloads.length} owned workloads`}/><Chip label={`${human(selectedTenantResources)} tenant resources`}/><Chip label={`${human(occupancy)} occupancy units`}/><Chip color="primary" label={`${human(score)} bifurcation score`}/><Chip color="secondary" label={`${currency} ${human(cost || selectedTenantCost)} metered cost`}/>
    </Box>
    <Box sx={{display:'grid',gridTemplateColumns:{xs:'1fr',md:'repeat(4,1fr)'},gap:1.5,mb:2}}>
      {[
        ['可出租资源类',consumableFacts.length,'Approved Consumables'],
        ['有明确上限',capacityFacts.filter(x=>x.capacity != null && x.capacity > 0).length,`${capacityFacts.filter(x=>x.capacity == null).length} 项未声明`],
        ['当前分配',capacityFacts.reduce((sum,x)=>sum+x.allocated,0),'配额/绑定事实'],
        ['Invoke / Trace',traceCount,`${factEvents.length} 条近期事件`],
      ].map(([label,value,detail])=><Box key={String(label)} sx={{border:'1px solid',borderColor:'divider',borderRadius:1,p:1.5}}><Typography variant="overline" color="text.secondary">{label}</Typography><Typography variant="h5">{human(Number(value))}</Typography><Typography variant="caption" color="text.secondary">{detail}</Typography></Box>)}
    </Box>
    <SectionBox title={`Capacity ledger (${capacityFacts.length})`}>
      <Table data={capacityFacts} columns={[
        {header:'Resource / scope',accessorFn:(x:CapacityFact)=><Box><Typography variant="body2">{x.resourceClass}</Typography><Typography variant="caption" color="text.secondary">{x.scope} · {x.unit}</Typography></Box>},
        {header:'Capacity ceiling',accessorFn:(x:CapacityFact)=>x.capacity == null ? <StatusLabel status="warning">未声明</StatusLabel> : human(x.capacity)},
        {header:'Allocated',accessorFn:(x:CapacityFact)=>human(x.allocated)},
        {header:'Observed used',accessorFn:(x:CapacityFact)=>x.used == null ? '-' : human(x.used)},
        {header:'Remaining',accessorFn:(x:CapacityFact)=>x.capacity == null ? '-' : human(Math.max(0,x.capacity-x.allocated))},
        {header:'Evidence',accessorFn:(x:CapacityFact)=><Box><Typography variant="body2">{x.source}</Typography><Typography variant="caption" color="text.secondary">{x.evidence}</Typography></Box>},
      ] as any}/>
    </SectionBox>
    <SectionBox title={`Invoke and allocation events (${factEvents.length})`}>
      <Table data={factEvents} columns={[
        {header:'Time',accessorFn:(x:FactEvent)=><Typography variant="caption">{compactTime(x.time)}</Typography>},
        {header:'Trace / task',accessorFn:(x:FactEvent)=><Typography variant="caption" sx={{fontFamily:'monospace'}}>{x.trace}</Typography>},
        {header:'Stage',accessorFn:(x:FactEvent)=><Chip size="small" variant="outlined" label={x.stage}/>},
        {header:'Resource',accessorFn:(x:FactEvent)=><Box><Typography variant="body2">{x.resource}</Typography><Typography variant="caption" color="text.secondary">tenant {x.tenant}</Typography></Box>},
        {header:'Transition',accessorFn:(x:FactEvent)=><StatusLabel status={/failed|error|revoked/i.test(x.transition)?'error':/ready|active|bound|approved/i.test(x.transition)?'success':'info'}>{x.transition}</StatusLabel>},
        {header:'Observed fact',accessorFn:(x:FactEvent)=><Box><Typography variant="body2">{x.detail || '-'}</Typography><Typography variant="caption" color="text.secondary">{x.source}</Typography></Box>},
      ] as any}/>
    </SectionBox>
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
    <SectionBox title="Global resource allocation">
      <Tabs value={resourceClass} onChange={(_event,value)=>setResourceClass(value)} variant="scrollable" scrollButtons="auto" aria-label="Resource type">
        {globalResourceClasses.map(name=><Tab key={name} value={name} label={classLabels[name] || name}/>) }
      </Tabs>
      <Alert severity={selectedCapacity == null ? 'info' : 'success'} sx={{my:1}}>{selectedCapacity == null ? '该资源类尚未声明可出租容量上限，因此只展示已分配与已观测事实，不伪造“未分配”数量。' : `容量上限 ${human(selectedCapacity)}；未分配量由上限减去已分配量得到。`}</Alert>
      <ResourceCorridors resourceClass={resourceClass} unit={capacityFacts.find(item=>item.resourceClass===resourceClass)?.unit || (resourceClass==='compute.workload.v1'?'pods':'units')} lanes={allocationLanes} capacity={selectedCapacity}/>
    </SectionBox>
    <SectionBox title={`Global resource classes (${globalClassRows.length})`}>
      <Table data={globalClassRows} columns={[
        {header:'Resource class',accessorFn:(x:any)=><Box><Typography variant="body2">{x.label}</Typography><Typography variant="caption" color="text.secondary">{x.name}</Typography></Box>},
        {header:'Lifecycle / access',accessorFn:(x:any)=><Box><StatusLabel status={x.lifecycle==='Approved'?'success':'warning'}>{x.lifecycle}</StatusLabel><Typography variant="caption" display="block" color="text.secondary">{x.access}</Typography></Box>},
        {header:'Capacity ceiling',accessorFn:(x:any)=>x.ceiling == null ? <StatusLabel status="warning">未声明</StatusLabel> : human(x.ceiling)},
        {header:'Allocated / observed',accessorFn:(x:any)=>`${human(x.allocated)} / ${human(x.observed)}`},
        {header:'Meters',accessorFn:(x:any)=>x.meters},
      ] as any}/>
    </SectionBox>
    {tenant&&<SectionBox title={`${tenant} Tenant API capabilities (${selectedToolGroups.reduce((sum,group)=>sum+group.tools.length,0)})`}>
      <Alert severity="info" sx={{mb:1}}>这里只显示当前业务租户已获授权资源类对应的 Tenant API 工具；工具数量不是资源容量，也不参与全局分配图。</Alert>
      <Table data={selectedToolGroups} columns={[
        {header:'Resource category',accessorFn:(x:ToolGroup)=><Box><Typography variant="body2">{x.category}</Typography><Typography variant="caption" color="text.secondary">{x.resourceClass}</Typography></Box>},
        {header:'Flow class',accessorFn:(x:ToolGroup)=><StatusLabel status={x.disposition==='retained'?'success':x.disposition==='external'?'warning':'info'}>{x.disposition==='retained'?'储蓄 / 回流':x.disposition==='external'?'外部租户分流':'本租户消耗'}</StatusLabel>},
        {header:'Tools',accessorFn:(x:ToolGroup)=><Box><Chip size="small" label={`${x.tools.length} tools`} sx={{mr:.5}}/>{x.tools.map(tool=><Chip key={tool} size="small" variant="outlined" label={tool} sx={{mr:.5,mb:.5}}/>)}</Box>},
      ] as any}/>
    </SectionBox>}
    {tenant&&<SectionBox title={`${tenant} owned namespace workloads (${visibleWorkloads.length})`}>
      <Table data={visibleWorkloads} columns={[
        {header:'Namespace / Workload',accessorFn:(x:KubeObject)=><Box><Typography variant="body2">{x.metadata?.namespace} / {x.metadata?.name}</Typography><Typography variant="caption" color="text.secondary">{x.kind}</Typography></Box>},
        {header:'Tenant',accessorFn:(x:KubeObject)=>objectOwner(x)||'-'},
        {header:'Harbor-backed image',accessorFn:(x:KubeObject)=>{const podSpec=x.kind==='CronJob'?x.spec?.jobTemplate?.spec?.template?.spec:x.kind==='Job'?x.spec?.template?.spec:x.spec?.template?.spec;const images=(podSpec?.containers||[]).map((container:any)=>container.image||'');const harbor=images.filter((image:string)=>image.includes('registry.re8ch.com'));return harbor.length?<StatusLabel status="success">{harbor.length} image(s)</StatusLabel>:<StatusLabel status="warning">未发现</StatusLabel>;}},
        {header:'Resource class',accessorFn:()=> 'compute.workload.v1'},
      ] as any}/>
    </SectionBox>}
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
