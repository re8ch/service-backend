import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Chip, Typography } from '@mui/material';
import { makeCustomResourceClass } from '@kinvolk/headlamp-plugin/lib/lib/k8s/crd';

const PublicEdge = makeCustomResourceClass({apiInfo:[{group:'networking.re8ch.com',version:'v1alpha1'}],kind:'PublicEdge',pluralName:'publicedges',singularName:'publicedge',isNamespaced:false});
const HTTPRoute = K8s.ResourceClasses.HTTPRoute;

function condition(item:any, type:string) {
  return item.status?.conditions?.find((x:any)=>x.type===type)?.status === 'True';
}

function Dashboard() {
  const [edges, edgeError] = PublicEdge.useList({} as any);
  const [routes, routeError] = HTTPRoute.useList({} as any);
  const [pods] = K8s.ResourceClasses.Pod.useList({} as any);
  const [slices] = K8s.ResourceClasses.EndpointSlice.useList({} as any);
  const [ingresses] = K8s.ResourceClasses.Ingress.useList({} as any);
  const publicRoutes = (routes || []).filter((x:any)=>x.spec?.hostnames?.length);
  const podByIp = new Map((pods || []).filter((x:any)=>x.status?.podIP).map((x:any)=>[x.status.podIP,x]));

  const routeRows = publicRoutes.flatMap((route:any)=>(route.spec.hostnames || []).map((host:string)=>{
    const authority=(ingresses || []).find((x:any)=>x.metadata?.annotations?.['external-dns.alpha.kubernetes.io/hostname']?.split(',').includes(host));
    const refs=(route.spec.rules || []).flatMap((r:any)=>r.backendRefs || []);
    const backends=refs.map((b:any)=>`${route.metadata.namespace}/${b.name}:${b.port}`).join(', ');
    const backendNames=new Set(refs.map((b:any)=>b.name));
    const endpointIPs=(slices || []).filter((s:any)=>s.metadata?.namespace===route.metadata.namespace && backendNames.has(s.metadata?.labels?.['kubernetes.io/service-name'])).flatMap((s:any)=>(s.endpoints || []).filter((e:any)=>e.conditions?.ready!==false).flatMap((e:any)=>e.addresses || []));
    const nodes=[...new Set(endpointIPs.map((ip:string)=>podByIp.get(ip)?.spec?.nodeName).filter(Boolean))].join(', ') || '-';
    const parents=(route.spec.parentRefs || []).map((p:any)=>`${p.namespace || route.metadata.namespace}/${p.name}#${p.sectionName || '*'}`).join(' → ');
    return {host,edge:authority?.metadata?.annotations?.['networking.re8ch.com/selected-public-edge'] || '-',target:authority?.metadata?.annotations?.['external-dns.alpha.kubernetes.io/target'] || '-',parents,backends,nodes,accepted:(route.status?.parents || []).every((p:any)=>(p.conditions || []).some((c:any)=>c.type==='Accepted'&&c.status==='True'))};
  }));

  return <Box sx={{p:2}}>
    <Typography variant="h4">Public Edge</Typography>
    <Typography color="text.secondary">公网 Candidate、地域与容量，以及 ExternalDNS authority → Gateway listener → Service 的实际发布路径。</Typography>
    {(edgeError || routeError) && <Alert severity="error">无法读取 PublicEdge/Gateway API：{String(edgeError || routeError)}</Alert>}
    <SectionBox title={`Candidates (${(edges || []).length})`}>
      <Table data={edges || []} columns={[
        {header:'Candidate',accessorFn:(x:any)=>x.metadata.name},
        {header:'Area / Region',accessorFn:(x:any)=>`${x.spec.area} / ${x.spec.region}`},
        {header:'Node',accessorFn:(x:any)=>x.spec.nodeName || '-'},
        {header:'Public endpoint',accessorFn:(x:any)=>x.spec.endpoint?.value || '-'},
        {header:'Gateway VIP',accessorFn:(x:any)=>x.spec.gatewayVIP},
        {header:'Capacity',accessorFn:(x:any)=>`${x.spec.capacityMbps} Mbps`},
        {header:'State',accessorFn:(x:any)=><StatusLabel status={condition(x,'Ready')?'success':'error'}>{x.spec.draining?'Draining':condition(x,'Ready')?'Ready':'Unavailable'}</StatusLabel>},
        {header:'Classes',accessorFn:(x:any)=><Box sx={{display:'flex',gap:.5,flexWrap:'wrap'}}>{(x.spec.serviceClasses || []).map((v:string)=><Chip key={v} size="small" label={v}/>)}</Box>}
      ] as any}/>
    </SectionBox>
    <SectionBox title={`Published services (${routeRows.length})`}>
      <Table data={routeRows} columns={[
        {header:'Hostname',accessorKey:'host'},
        {header:'Selected exit',accessorFn:(x:any)=>`${x.edge} (${x.target})`},
        {header:'Gateway path',accessorKey:'parents'},
        {header:'Backend service',accessorKey:'backends'},
        {header:'Actual Pod nodes',accessorKey:'nodes'},
        {header:'Route',accessorFn:(x:any)=><StatusLabel status={x.accepted?'success':'error'}>{x.accepted?'Accepted':'Problem'}</StatusLabel>}
      ] as any}/>
    </SectionBox>
    <Typography variant="caption" color="text.secondary">Resolved Pod 节点由 EndpointSlice/Pod 实时状态补充；当前发现 {podByIp.size} 个可寻址 Pod。</Typography>
  </Box>;
}

registerSidebarEntry({name:'public-edge',url:'/public-edge',icon:'mdi:wan',parent:'',label:'Public Edge'});
registerRoute({path:'/public-edge',sidebar:'public-edge',name:'Public Edge',component:()=> <Dashboard/>});
