import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { makeCustomResourceClass } from '@kinvolk/headlamp-plugin/lib/lib/k8s/crd';
import { Alert, Box, Button, Chip, Stack, Typography } from '@mui/material';
import React from 'react';

const NS = 're8ch-secretary';
const OBS_NS = 'observability-system';
const STREAM_URL = '/secretary-api/v1/event-stream';
const SECRET_KEYS: Record<string, string[]> = {
  'secretary-runtime': ['webhook-secret', 'query-token', 'bluebubbles-webhook-token', 'voice-webhook-token', 'identity-bindings-json', 'neo4j-uri', 'neo4j-user', 'neo4j-password', 'litellm-api-key'],
  'secretary-email': ['email', 'username', 'password', 'tls-fingerprint-sha256', 'recipient', 'recipient-id'],
  'secretary-db-credentials': ['DATABASE_URL_RW', 'DATABASE_URL_MIGRATOR'],
};

function vmResource(kind:string, pluralName:string, version='v1beta1') {
  return makeCustomResourceClass({apiInfo:[{group:'operator.victoriametrics.com',version}],kind,pluralName,singularName:pluralName.replace(/s$/,''),isNamespaced:true});
}

const VMAlert = vmResource('VMAlert','vmalerts');
const VMAlertmanager = vmResource('VMAlertmanager','vmalertmanagers');
const VMAlertmanagerConfig = vmResource('VMAlertmanagerConfig','vmalertmanagerconfigs');
const VMRule = vmResource('VMRule','vmrules');
const VMServiceScrape = vmResource('VMServiceScrape','vmservicescrapes');
const VLSingle = vmResource('VLSingle','vlsingles','v1');
const VLAgent = vmResource('VLAgent','vlagents','v1');
const raw=(item:any)=>item?.jsonData || item || {};

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
  const [networkPolicies] = K8s.ResourceClasses.NetworkPolicy.useList({namespace:NS} as any);
  const [vmAlerts, vmAlertError] = VMAlert.useList({namespace:OBS_NS} as any);
  const [alertmanagers, alertmanagerError] = VMAlertmanager.useList({namespace:OBS_NS} as any);
  const [alertmanagerConfigs, configError] = VMAlertmanagerConfig.useList({} as any);
  const [rules, ruleError] = VMRule.useList({} as any);
  const [scrapes, scrapeError] = VMServiceScrape.useList({} as any);
  const [logStores, logStoreError] = VLSingle.useList({namespace:OBS_NS} as any);
  const [logAgents, logAgentError] = VLAgent.useList({namespace:OBS_NS} as any);
  const [items, setItems] = React.useState<any[]>([]);
  const [cursor, setCursor] = React.useState(0);
  const [streamError, setStreamError] = React.useState('');
  const [loading, setLoading] = React.useState(false);

  const load = React.useCallback(async (reset=false) => {
    setLoading(true); setStreamError('');
    try {
      const after = reset ? 0 : cursor;
      const response = await fetch(`${STREAM_URL}?after_seq=${after}&limit=200${reset?'&latest=true':''}`, {credentials:'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setItems(current=>reset ? data.items : [...current, ...data.items]);
      setCursor(data.next_after_seq || after);
    } catch (error) { setStreamError(`事件流读取失败：${String(error)}`); }
    finally { setLoading(false); }
  }, [cursor]);

  React.useEffect(()=>{ void load(true); }, []);

  const referencedSecretKeys = new Map<string,Set<string>>();
  for (const deployment of deployments || []) {
    const containers=[...(deployment.spec?.template?.spec?.containers || []), ...(deployment.spec?.template?.spec?.initContainers || [])];
    for (const container of containers) for (const env of container.env || []) {
      const ref=env.valueFrom?.secretKeyRef;
      if (!ref?.name || !ref?.key) continue;
      if (!referencedSecretKeys.has(ref.name)) referencedSecretKeys.set(ref.name,new Set());
      referencedSecretKeys.get(ref.name)!.add(ref.key);
    }
  }
  const secretRows = Object.entries(SECRET_KEYS).map(([name, expected])=>{
    const keys=referencedSecretKeys.get(name) || new Set<string>();
    return {name, referenced:expected.filter(x=>keys.has(x)).length, expected:expected.length,
      missing:expected.filter(x=>!keys.has(x))};
  });
  const eventRows=[...items].sort((a,b)=>Number(b.seq)-Number(a.seq));
  const incidentCount=new Set(items.map(x=>x.event?.incident_id).filter(Boolean)).size;
  const ackCount=items.filter(x=>x.event?.type==='responsibility.acknowledged').length;
  const failedCount=items.filter(x=>['health.failed','delivery.failed','incident.reopened'].includes(x.event?.type)).length;
  const attemptedCount=items.filter(x=>x.event?.type==='delivery.attempted').length;
  const providerAcceptedCount=items.filter(x=>x.event?.type==='delivery.provider_accepted').length;
  const unknownCount=items.filter(x=>x.event?.type==='delivery.unknown').length;
  const monitoringError=vmAlertError || alertmanagerError || configError || ruleError || scrapeError || logStoreError || logAgentError;
  const dataPlaneRows=[
    ...(logStores || []).map((x:any)=>({...raw(x),displayKind:'VLSingle'})),
    ...(logAgents || []).map((x:any)=>({...raw(x),displayKind:'VLAgent'})),
    ...(vmAlerts || []).map((x:any)=>({...raw(x),displayKind:'VMAlert'})),
    ...(alertmanagers || []).map((x:any)=>({...raw(x),displayKind:'VMAlertmanager'})),
  ];

  return <Box sx={{p:2}}>
    <Typography variant="h4">Secretary</Typography>
    <Typography color="text.secondary">责任确认、通知通达与可信事件流；所有 Secret 仅检查键是否存在。</Typography>
    {(deploymentError || podError) && <Alert severity="error">无法读取 re8ch-secretary：{String(deploymentError || podError)}</Alert>}
    <Stack direction="row" spacing={1} sx={{my:2,flexWrap:'wrap'}}>
      <Chip label={`Workloads ${(deployments || []).filter(ready).length}/${(deployments || []).length} Ready`} color={(deployments || []).every(ready)?'success':'error'}/>
      <Chip label={`Pods ${(pods || []).filter((x:any)=>x.status?.phase==='Running').length}/${(pods || []).length} Running`}/>
      <Chip label={`Services ${(services || []).length}`}/><Chip label={`NetworkPolicies ${(networkPolicies || []).length}`}/>
      <Chip label={`Incidents ${incidentCount}`}/><Chip label={`ACK ${ackCount}`} color="success"/><Chip label={`Failures ${failedCount}`} color={failedCount?'warning':'default'}/>
      <Chip label={`Attempts ${attemptedCount}`}/><Chip label={`Provider accepted ${providerAcceptedCount}`}/><Chip label={`Unknown ${unknownCount}`} color={unknownCount?'warning':'default'}/>
    </Stack>
    {monitoringError && <Alert severity="warning">部分 monitoring CRD 不可见：{String(monitoringError)}。权限错误不会被解释为空配置。</Alert>}
    <SectionBox title="Monitoring data plane">
      <Table data={dataPlaneRows} columns={[
        {header:'Kind',accessorKey:'displayKind'},
        {header:'Resource',accessorFn:(x:any)=>`${x.metadata?.namespace}/${x.metadata?.name}`},
        {header:'Desired replicas',accessorFn:(x:any)=>x.spec?.replicaCount ?? x.spec?.replicas ?? (x.displayKind==='VLAgent'?'per node':'1')},
        {header:'Status',accessorFn:(x:any)=>{const state=x.status?.status || x.status?.phase || x.status?.conditions?.find((c:any)=>c.type==='Ready')?.status || 'Declared'; return <StatusLabel status={['operational','ready','True'].includes(String(state))?'success':'warning'}>{String(state)}</StatusLabel>;}},
        {header:'Retention / target',accessorFn:(x:any)=>x.spec?.retentionPeriod || x.spec?.remoteWrite?.[0]?.url || '-'},
      ] as any}/>
    </SectionBox>
    <SectionBox title="GitOps monitoring configuration">
      <Stack direction="row" spacing={1} sx={{mb:1,flexWrap:'wrap'}}>
        <Chip label={`Scrape configs ${(scrapes || []).length}`}/><Chip label={`Rule sets ${(rules || []).length}`}/><Chip label={`Namespace routes ${(alertmanagerConfigs || []).length}`}/>
      </Stack>
      <Table data={(alertmanagerConfigs || []).map(raw)} columns={[
        {header:'Namespace / policy',accessorFn:(x:any)=>`${x.metadata?.namespace}/${x.metadata?.name}`},
        {header:'Owner',accessorFn:(x:any)=>x.metadata?.annotations?.['observability.re8ch.com/owner'] || '-'},
        {header:'Default receiver',accessorFn:(x:any)=>x.spec?.route?.receiver || '-'},
        {header:'Routes',accessorFn:(x:any)=>(x.spec?.route?.routes || []).length},
        {header:'Receivers',accessorFn:(x:any)=>(x.spec?.receivers || []).length},
      ] as any}/>
      <Typography variant="caption" color="text.secondary">Direct receiver success only proves the capability declared by that provider; it never implies human acknowledgement.</Typography>
    </SectionBox>
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
        {header:'Referenced keys',accessorFn:(x:any)=><StatusLabel status={x.referenced===x.expected?'success':'warning'}>{x.referenced}/{x.expected}</StatusLabel>},
        {header:'Missing',accessorFn:(x:any)=>x.missing.join(', ') || 'None'},
      ] as any}/>
    </SectionBox>
    <SectionBox title="Authoritative event stream">
      <Stack direction={{xs:'column',md:'row'}} spacing={1} sx={{mb:2}}>
        <Button variant="contained" disabled={loading} onClick={()=>load(true)}>Refresh</Button>
        <Button variant="outlined" disabled={loading || !cursor} onClick={()=>load(false)}>Load next</Button>
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
      <Typography variant="caption" color="text.secondary">Cursor: {cursor || '-'} · loaded facts: {items.length}. Access is inherited from the authenticated Headlamp administrator session.</Typography>
    </SectionBox>
  </Box>;
}

registerSidebarEntry({name:'secretary',url:'/secretary',icon:'mdi:account-tie-voice',parent:'',label:'Secretary'});
registerRoute({path:'/secretary',sidebar:'secretary',name:'Secretary',component:()=> <Dashboard/>});
