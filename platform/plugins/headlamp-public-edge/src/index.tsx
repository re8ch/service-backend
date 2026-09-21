import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Chip, Typography } from '@mui/material';
import { makeCustomResourceClass } from '@kinvolk/headlamp-plugin/lib/lib/k8s/crd';

const PublicEdge = makeCustomResourceClass({apiInfo:[{group:'networking.re8ch.com',version:'v1alpha1'}],kind:'PublicEdge',pluralName:'publicedges',singularName:'publicedge',isNamespaced:false});
const HTTPRoute = K8s.ResourceClasses.HTTPRoute;

function condition(item:any, type:string) {
  const data=item?.jsonData || item;
  return data?.status?.conditions?.find((x:any)=>x.type===type)?.status === 'True';
}

const raw=(item:any)=>item?.jsonData || item || {};

function Dashboard() {
  const [edges, edgeError] = PublicEdge.useList({} as any);
  const [routes, routeError] = HTTPRoute.useList({} as any);
  const [nodes, nodeError] = K8s.ResourceClasses.Node.useList({} as any);
  const [pods] = K8s.ResourceClasses.Pod.useList({} as any);
  const [slices] = K8s.ResourceClasses.EndpointSlice.useList({} as any);
  const [ingresses] = K8s.ResourceClasses.Ingress.useList({} as any);
  const publicRoutes = (routes || []).map(raw).filter((x:any)=>x.spec?.hostnames?.length);
  const podByIp = new Map((pods || []).map(raw).filter((x:any)=>x.status?.podIP).map((x:any)=>[x.status.podIP,x]));
  const endpointSlices = (slices || []).map(raw);
  const ingressObjects = (ingresses || []).map(raw);
  const edgeByName = new Map((edges || []).map(raw).map((x:any)=>[x.metadata?.name,x]));
  const nodeByName = new Map((nodes || []).map(raw).map((x:any)=>[x.metadata?.name,x]));
  const edgeState = (edge:any) => {
    const node = nodeByName.get(edge?.spec?.nodeName) as any;
    if (!node) return 'Node missing';
    if (!node.status?.conditions?.some((c:any)=>c.type==='Ready' && c.status==='True')) return 'Node NotReady';
    if (!edge?.spec?.enabled || edge?.spec?.draining) return 'Disabled / draining';
    const observed = Date.parse(edge?.status?.observedAt || '');
    if (!Number.isFinite(observed) || Math.abs(Date.now()-observed)>60000) return 'Stale evidence';
    if (edge?.status?.observedGeneration && edge.status.observedGeneration!==edge?.metadata?.generation) return 'Old generation';
    if (!condition(edge,'Ready')) return edge?.status?.conditions?.find((c:any)=>c.type==='Ready')?.reason || 'Unavailable';
    if (!Object.values(edge?.status?.services || {}).some((s:any)=>s.ready)) return 'No ready service';
    return 'Ready';
  };

  const routeRows = publicRoutes.flatMap((route:any)=>(route.spec.hostnames || []).map((host:string)=>{
    const authority=ingressObjects.find((x:any)=>x.metadata?.annotations?.['external-dns.alpha.kubernetes.io/hostname']?.split(',').includes(host) ||
      (x.spec?.rules || []).some((rule:any)=>rule.host===host));
    const refs=(route.spec.rules || []).flatMap((r:any)=>r.backendRefs || []);
    const backends=refs.map((b:any)=>`${route.metadata.namespace}/${b.name}:${b.port}`).join(', ');
    const backendNames=new Set(refs.map((b:any)=>b.name));
    const readySlices=endpointSlices.filter((s:any)=>s.metadata?.namespace===route.metadata.namespace && backendNames.has(s.metadata?.labels?.['kubernetes.io/service-name']));
    const endpointIPs=readySlices.flatMap((s:any)=>(s.endpoints || []).filter((e:any)=>e.conditions?.ready===true).flatMap((e:any)=>e.addresses || []));
    const readyPods=endpointIPs.map((ip:string)=>podByIp.get(ip)).filter((p:any)=>p?.status?.conditions?.some((c:any)=>c.type==='Ready'&&c.status==='True'));
    const nodes=[...new Set(readyPods.map((p:any)=>p.spec?.nodeName).filter(Boolean))].join(', ') || '-';
    const parents=(route.spec.parentRefs || []).map((p:any)=>`${p.namespace || route.metadata.namespace}/${p.name}#${p.sectionName || '*'}`).join(' → ');
    const routeParents=route.status?.parents || [];
    const accepted=routeParents.length>0 && routeParents.every((p:any)=>['Accepted','ResolvedRefs'].every(type=>(p.conditions || []).some((c:any)=>c.type===type&&c.status==='True')));
    const hasBackends=refs.length>0 && [...backendNames].every(name=>readySlices.some((s:any)=>s.metadata?.labels?.['kubernetes.io/service-name']===name && (s.endpoints || []).some((e:any)=>e.conditions?.ready===true && (e.addresses || []).some((ip:string)=>podByIp.get(ip)?.status?.conditions?.some((c:any)=>c.type==='Ready'&&c.status==='True')))));
    const selectedEdge=authority?.metadata?.annotations?.['networking.re8ch.com/selected-public-edge'];
    const exitReady=selectedEdge && edgeState(edgeByName.get(selectedEdge))==='Ready';
    const ready=accepted && hasBackends && Boolean(exitReady) && Boolean(authority?.metadata?.annotations?.['external-dns.alpha.kubernetes.io/target']);
    return {host,edge:selectedEdge || '-',target:authority?.metadata?.annotations?.['external-dns.alpha.kubernetes.io/target'] || '-',parents,backends,nodes,accepted,ready,reason:!accepted?'Route not accepted/resolved':!hasBackends?'No ready backend Pod':!exitReady?'No ready public exit':!authority?.metadata?.annotations?.['external-dns.alpha.kubernetes.io/target']?'No published DNS target':'Ready in cluster'};
  }));

  return <Box sx={{p:2}}>
    <Typography variant="h4">Public Edge</Typography>
    <Typography color="text.secondary">公网 Candidate、地域与容量，以及 ExternalDNS authority → Gateway listener → Service 的实际发布路径。</Typography>
    {(edgeError || routeError || nodeError) && <Alert severity="error">无法读取 PublicEdge/Gateway/Node API：{String(edgeError || routeError || nodeError)}</Alert>}
    <SectionBox title={`Candidates (${(edges || []).length})`}>
      <Table data={(edges || []).map(raw)} columns={[
        {header:'Candidate',accessorFn:(x:any)=>x?.metadata?.name || '-'},
        {header:'Area / Region',accessorFn:(x:any)=>`${x?.spec?.area || '-'} / ${x?.spec?.region || '-'}`},
        {header:'Node',accessorFn:(x:any)=>x?.spec?.nodeName || '-'},
        {header:'Public endpoint',accessorFn:(x:any)=>x?.spec?.endpoint?.value || '-'},
        {header:'Gateway VIP',accessorFn:(x:any)=>x?.spec?.gatewayVIP || '-'},
        {header:'Capacity',accessorFn:(x:any)=>x?.spec?.capacityMbps == null ? '-' : `${x.spec.capacityMbps} Mbps`},
        {header:'State',accessorFn:(x:any)=><StatusLabel status={edgeState(x)==='Ready'?'success':'error'}>{edgeState(x)}</StatusLabel>},
        {header:'NPA evidence',accessorFn:(x:any)=>`${x?.status?.networkEvidence?.state || '-'} · ${x?.status?.networkEvidence?.reason || '-'}`},
        {header:'Service probes',accessorFn:(x:any)=>{const values=Object.values(x?.status?.services || {}) as any[]; return `${values.filter(v=>v.ready).length}/${values.length} ready`; }},
        {header:'Classes',accessorFn:(x:any)=><Box sx={{display:'flex',gap:.5,flexWrap:'wrap'}}>{(Array.isArray(x?.spec?.serviceClasses) ? x.spec.serviceClasses : []).map((v:string)=><Chip key={v} size="small" label={v}/>)}</Box>}
      ] as any}/>
    </SectionBox>
    <SectionBox title={`Published services (${routeRows.length})`}>
      <Table data={routeRows} columns={[
        {header:'Hostname',accessorKey:'host'},
        {header:'Selected exit',accessorFn:(x:any)=>`${x.edge} (${x.target})`},
        {header:'Gateway path',accessorKey:'parents'},
        {header:'Backend service',accessorKey:'backends'},
        {header:'Actual Pod nodes',accessorKey:'nodes'},
        {header:'Ingress class',accessorFn:(x:any)=>{const authority=ingressObjects.find((i:any)=>(i.spec?.rules || []).some((r:any)=>r.host===x.host)); return authority?.spec?.ingressClassName || '-';}},
        {header:'Route',accessorFn:(x:any)=><StatusLabel status={x.accepted?'success':'error'}>{x.accepted?'Accepted':'Problem'}</StatusLabel>},
        {header:'In-cluster path',accessorFn:(x:any)=><StatusLabel status={x.ready?'success':'error'}>{x.reason}</StatusLabel>}
      ] as any}/>
    </SectionBox>
    <Typography variant="caption" color="text.secondary">集群内 Ready 不证明公网 DNS 或 TLS 可达；请同时查看 PublicEdge public DNS authority 的外部监测。当前发现 {podByIp.size} 个可寻址 Pod。</Typography>
  </Box>;
}

registerSidebarEntry({name:'public-edge',url:'/public-edge',icon:'mdi:wan',parent:'',label:'Public Edge'});
registerRoute({path:'/public-edge',sidebar:'public-edge',name:'Public Edge',component:()=> <Dashboard/>});
