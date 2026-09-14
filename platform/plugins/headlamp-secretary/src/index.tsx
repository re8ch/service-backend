import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Button, Chip, Stack, TextField, Typography } from '@mui/material';
import React from 'react';

const NS = 're8ch-secretary';
const STREAM_URL = '/secretary-api/v1/event-stream';
const SECRET_KEYS: Record<string, string[]> = {
  'secretary-runtime': ['webhook-secret', 'query-token', 'bluebubbles-webhook-token', 'voice-webhook-token', 'identity-bindings-json', 'neo4j-uri', 'neo4j-user', 'neo4j-password', 'litellm-api-key'],
  'secretary-email': ['email', 'username', 'password', 'tls-fingerprint-sha256', 'recipient', 'recipient-id'],
  'secretary-db-credentials': ['DATABASE_URL_RW', 'DATABASE_URL_MIGRATOR'],
};

function ready(deployment:any) {
  return Number(deployment?.status?.readyReplicas || 0) === Number(deployment?.spec?.replicas || 0);
}

function fmtTime(value?:string) {
  return value ? new Date(value).toLocaleString() : '-';
}

function Dashboard() {
  const [deployments, deploymentError] = K8s.ResourceClasses.Deployment.useList({namespace:NS} as any);
  const [pods, podError] = K8s.ResourceClasses.Pod.useList({namespace:NS} as any);
  const [services] = K8s.ResourceClasses.Service.useList({namespace:NS} as any);
  const [secrets] = K8s.ResourceClasses.Secret.useList({namespace:NS} as any);
  const [networkPolicies] = K8s.ResourceClasses.NetworkPolicy.useList({namespace:NS} as any);
  const [token, setToken] = React.useState(()=>sessionStorage.getItem('re8ch-secretary-query-token') || '');
  const [items, setItems] = React.useState<any[]>([]);
  const [cursor, setCursor] = React.useState(0);
  const [streamError, setStreamError] = React.useState('');
  const [loading, setLoading] = React.useState(false);

  async function load(reset=false) {
    if (!token) { setStreamError('请输入 Secretary query token。'); return; }
    setLoading(true); setStreamError('');
    try {
      sessionStorage.setItem('re8ch-secretary-query-token', token);
      const after = reset ? 0 : cursor;
      const response = await fetch(`${STREAM_URL}?after_seq=${after}&limit=200`, {headers:{'X-Secretary-Query-Token':token}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setItems(reset ? data.items : [...items, ...data.items]);
      setCursor(data.next_after_seq || after);
    } catch (error) { setStreamError(`事件流读取失败：${String(error)}`); }
    finally { setLoading(false); }
  }

  const secretRows = Object.entries(SECRET_KEYS).map(([name, expected])=>{
    const secret=(secrets || []).find((x:any)=>x.metadata?.name===name);
    const keys=new Set(Object.keys(secret?.data || {}));
    return {name, configured:expected.filter(x=>keys.has(x)).length, expected:expected.length,
      missing:expected.filter(x=>!keys.has(x))};
  });
  const eventRows=[...items].reverse();
  const incidentCount=new Set(items.map(x=>x.event?.incident_id).filter(Boolean)).size;
  const ackCount=items.filter(x=>x.event?.type==='responsibility.acknowledged').length;
  const failedCount=items.filter(x=>['health.failed','delivery.failed','incident.reopened'].includes(x.event?.type)).length;

  return <Box sx={{p:2}}>
    <Typography variant="h4">Secretary</Typography>
    <Typography color="text.secondary">责任确认、通知通达与可信事件流；所有 Secret 仅检查键是否存在。</Typography>
    {(deploymentError || podError) && <Alert severity="error">无法读取 re8ch-secretary：{String(deploymentError || podError)}</Alert>}
    <Stack direction="row" spacing={1} sx={{my:2,flexWrap:'wrap'}}>
      <Chip label={`Workloads ${(deployments || []).filter(ready).length}/${(deployments || []).length} Ready`} color={(deployments || []).every(ready)?'success':'error'}/>
      <Chip label={`Pods ${(pods || []).filter((x:any)=>x.status?.phase==='Running').length}/${(pods || []).length} Running`}/>
      <Chip label={`Services ${(services || []).length}`}/><Chip label={`NetworkPolicies ${(networkPolicies || []).length}`}/>
      <Chip label={`Incidents ${incidentCount}`}/><Chip label={`ACK ${ackCount}`} color="success"/><Chip label={`Failures ${failedCount}`} color={failedCount?'warning':'default'}/>
    </Stack>
    <SectionBox title="Runtime services">
      <Table data={deployments || []} columns={[
        {header:'Deployment',accessorFn:(x:any)=>x.metadata?.name},
        {header:'Ready',accessorFn:(x:any)=><StatusLabel status={ready(x)?'success':'error'}>{x.status?.readyReplicas || 0}/{x.spec?.replicas || 0}</StatusLabel>},
        {header:'Node policy',accessorFn:(x:any)=>x.spec?.template?.spec?.nodeSelector?.['kubernetes.io/hostname'] || '-'},
        {header:'Image',accessorFn:(x:any)=>String(x.spec?.template?.spec?.containers?.[0]?.image || '-').replace(/^.*\//,'')},
      ] as any}/>
    </SectionBox>
    <SectionBox title="Configuration readiness (values hidden)">
      <Table data={secretRows} columns={[
        {header:'Secret',accessorKey:'name'},
        {header:'Configured keys',accessorFn:(x:any)=><StatusLabel status={x.configured===x.expected?'success':'warning'}>{x.configured}/{x.expected}</StatusLabel>},
        {header:'Missing',accessorFn:(x:any)=>x.missing.join(', ') || 'None'},
      ] as any}/>
    </SectionBox>
    <SectionBox title="Authoritative event stream">
      <Stack direction={{xs:'column',md:'row'}} spacing={1} sx={{mb:2}}>
        <TextField size="small" type="password" label="Secretary query token" value={token} onChange={e=>setToken(e.target.value)} sx={{minWidth:360}}/>
        <Button variant="contained" disabled={loading} onClick={()=>load(true)}>Load latest</Button>
        <Button variant="outlined" disabled={loading || !cursor} onClick={()=>load(false)}>Load next</Button>
        <Button color="inherit" onClick={()=>{sessionStorage.removeItem('re8ch-secretary-query-token');setToken('');setItems([]);setCursor(0);}}>Forget token</Button>
      </Stack>
      {streamError && <Alert severity="error" sx={{mb:1}}>{streamError}</Alert>}
      <Table data={eventRows} columns={[
        {header:'Seq',accessorFn:(x:any)=>x.seq},
        {header:'Incident',accessorFn:(x:any)=>x.event?.incident_id || '-'},
        {header:'Fact',accessorFn:(x:any)=><Chip size="small" label={x.event?.type || '-'}/>},
        {header:'Subject',accessorFn:(x:any)=>x.event?.subject || '-'},
        {header:'Authority',accessorFn:(x:any)=>x.event?.authority || '-'},
        {header:'Source',accessorFn:(x:any)=>x.event?.source || '-'},
        {header:'Observed',accessorFn:(x:any)=>fmtTime(x.event?.observed_at)},
      ] as any}/>
      <Typography variant="caption" color="text.secondary">Cursor: {cursor || '-'} · loaded facts: {items.length}. Query token stays in this browser tab session.</Typography>
    </SectionBox>
  </Box>;
}

registerSidebarEntry({name:'secretary',url:'/secretary',icon:'mdi:account-tie-voice',parent:'',label:'Secretary'});
registerRoute({path:'/secretary',sidebar:'secretary',name:'Secretary',component:()=> <Dashboard/>});
