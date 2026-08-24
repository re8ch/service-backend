import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Chip, Typography } from '@mui/material';

type Status = {node:string; timestamp:number; declaredGeo:string; region?:string; publicIdentity?:any; consumer?:any; isCnExit:boolean; isGlobalExit:boolean; probes?:Record<string,Record<string,any>>};

function parseStatus(item:any): Status | null {
  try { return JSON.parse(item.jsonData?.data?.['status.json'] || item.data?.['status.json']); } catch { return null; }
}

function ProbeSummary({status, groups=['cn','global']}:{status:Status; groups?:string[]}) {
  const entries = Object.entries(status.probes || {}).filter(([geo]) => groups.includes(geo)).flatMap(([geo, probes]) => Object.entries(probes).map(([name, value]:any) => ({geo,name,...value})));
  return <Box sx={{display:'flex',gap:0.5,flexWrap:'wrap'}}>{entries.map(x => <Chip key={`${x.geo}-${x.name}`} size="small" color={x.ok?'success':'error'} label={`${x.name} ${x.latencyMs ?? '-'}ms`} />)}</Box>;
}

const identityColumns = [
  {header:'Node', accessorKey:'node'},
  {header:'Geo / Region', accessorFn:(x:Status)=>`${x.declaredGeo} / ${x.region || '-'}`},
  {header:'Public IP', accessorFn:(x:Status)=>x.publicIdentity?.ip || '-'},
  {header:'Observed location', accessorFn:(x:Status)=>[x.publicIdentity?.country,x.publicIdentity?.region,x.publicIdentity?.city].filter(Boolean).join(' / ') || '-'},
];

function Dashboard() {
  const [maps, error] = K8s.ResourceClasses.ConfigMap.useList({namespace:'egress-fabric'} as any);
  const statuses = (maps || []).filter((x:any) => x.metadata?.labels?.['app.kubernetes.io/component'] === 'mihomos-node-status').map(parseStatus).filter(Boolean) as Status[];
  const stale = statuses.filter(x => Date.now()/1000-x.timestamp > 180).length;
  return <Box sx={{p:2}}>
    <Typography variant="h4">Mihomos Cluster</Typography>
    <Typography color="text.secondary">Provider 发布健康出口，Consumer 展示每个节点通过本机规则代理访问国内与国际服务的情况。</Typography>
    {error && <Alert severity="error">无法读取状态 ConfigMap：{String(error)}</Alert>}
    {stale > 0 && <Alert severity="warning">{stale} 个节点状态超过 180 秒未更新</Alert>}
    <SectionBox title={`Provider (${statuses.filter(x => x.isCnExit || x.isGlobalExit).length})`}>
      <Table data={statuses.filter(x => x.isCnExit || x.isGlobalExit)} columns={[
        ...identityColumns,
        {header:'Exit roles', accessorFn:(x:Status)=>[x.isCnExit?'CN Egress':'',x.isGlobalExit?'Global Egress':''].filter(Boolean).join(', ')},
        {header:'Provider reachability', accessorFn:(x:Status)=><ProbeSummary status={x} groups={[x.isCnExit?'cn':'',x.isGlobalExit?'global':''].filter(Boolean)}/>},
        {header:'Updated', accessorFn:(x:Status)=>new Date(x.timestamp*1000).toLocaleString()}
      ] as any}/>
    </SectionBox>
    <SectionBox title={`Consumer (${statuses.length})`}>
      <Table data={statuses} columns={[
        ...identityColumns,
        {header:'Consumer', accessorFn:(x:Status)=><StatusLabel status={x.consumer?.ok?'success':'error'}>{x.consumer?.ok?'Ready':'Down'}</StatusLabel>},
        {header:'CN + Global reachability', accessorFn:(x:Status)=><ProbeSummary status={x}/>},
        {header:'Updated', accessorFn:(x:Status)=>new Date(x.timestamp*1000).toLocaleString()}
      ] as any}/>
    </SectionBox>
  </Box>;
}

registerSidebarEntry({name:'mihomos-cluster',url:'/mihomos-cluster',icon:'mdi:airport',parent:'',label:'Mihomos Cluster'});
registerRoute({path:'/mihomos-cluster',sidebar:'mihomos-cluster',name:'Mihomos Cluster',component:()=> <Dashboard/>});
