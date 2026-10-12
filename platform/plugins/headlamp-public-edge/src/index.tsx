import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { makeCustomResourceClass } from '@kinvolk/headlamp-plugin/lib/lib/k8s/crd';
import { Alert, Box, Chip, Stack, Tab, Tabs, Typography } from '@mui/material';
import React from 'react';

const PublicEdge = makeCustomResourceClass({
  apiInfo: [{ group: 'networking.re8ch.com', version: 'v1alpha1' }],
  kind: 'PublicEdge',
  pluralName: 'publicedges',
  singularName: 'publicedge',
  isNamespaced: false,
});
const HTTPRoute = K8s.ResourceClasses.HTTPRoute;
const Ingress = makeCustomResourceClass({
  apiInfo: [{ group: 'networking.k8s.io', version: 'v1' }],
  kind: 'Ingress',
  pluralName: 'ingresses',
  singularName: 'ingress',
  isNamespaced: true,
});

function condition(item: any, type: string) {
  const data = item?.jsonData || item;
  return data?.status?.conditions?.find((x: any) => x.type === type)?.status === 'True';
}

const raw = (item: any) => item?.jsonData || item || {};

type ExternalSite = {
  name: string;
  hostnames: string[];
  provider: string;
  delivery: string;
  origin: string;
  dnsMode: string;
  sourceOfTruth: string;
  notes?: string;
};

function externalSitesFrom(configMap: any): ExternalSite[] {
  try {
    const parsed = JSON.parse(configMap?.data?.['sites.json'] || '{"sites":[]}');
    return Array.isArray(parsed.sites)
      ? parsed.sites.filter(
          (site: any) =>
            typeof site?.name === 'string' &&
            Array.isArray(site.hostnames) &&
            site.hostnames.every((host: any) => typeof host === 'string')
        )
      : [];
  } catch (_) {
    return [];
  }
}

function nameserversFrom(configMap: any): string[] {
  try {
    const parsed = JSON.parse(configMap?.data?.['nameservers.json'] || '{"nameservers":[]}');
    return Array.isArray(parsed.nameservers)
      ? parsed.nameservers.filter((value: any) => typeof value === 'string')
      : [];
  } catch (_) {
    return [];
  }
}

const normalizedDnsName = (value: string) => value.replace(/\.$/, '').toLowerCase();
const isReady = (item: any) =>
  item?.status?.conditions?.some(
    (condition: any) => condition.type === 'Ready' && condition.status === 'True'
  );

function Dashboard() {
  const [tab, setTab] = React.useState('candidates');
  const [edges, edgeError] = PublicEdge.useList({} as any);
  const [routes, routeError] = HTTPRoute.useList({} as any);
  const [nodes, nodeError] = K8s.ResourceClasses.Node.useList({} as any);
  const [pods] = K8s.ResourceClasses.Pod.useList({} as any);
  const [daemonSets] = K8s.ResourceClasses.DaemonSet.useList({} as any);
  const [slices] = K8s.ResourceClasses.EndpointSlice.useList({} as any);
  const [ingresses] = Ingress.useList({} as any);
  const [configMaps, configError] = K8s.ResourceClasses.ConfigMap.useList({} as any);
  const publicRoutes = (routes || []).map(raw).filter((x: any) => x.spec?.hostnames?.length);
  const podByIp = new Map(
    (pods || [])
      .map(raw)
      .filter((x: any) => x.status?.podIP)
      .map((x: any) => [x.status.podIP, x])
  );
  const endpointSlices = (slices || []).map(raw);
  const ingressObjects = (ingresses || []).map(raw);
  const recordsConfigMap = (configMaps || [])
    .map(raw)
    .find(
      (x: any) =>
        x.metadata?.namespace === 'regional-routing' &&
        x.metadata?.name === 'public-edge-manager-records'
    );
  const sitesConfigMap = (configMaps || [])
    .map(raw)
    .find(
      (x: any) =>
        x.metadata?.namespace === 'kube-system' && x.metadata?.name === 'public-edge-catalog'
    );
  const nameserversConfigMap = (configMaps || [])
    .map(raw)
    .find(
      (x: any) =>
        x.metadata?.namespace === 'regional-routing' &&
        x.metadata?.name === 'public-edge-nameservers'
    );
  const externalSites = externalSitesFrom(sitesConfigMap);
  const nameserversInUse = nameserversFrom(nameserversConfigMap);
  let records: any = { services: {}, zones: [] };
  try {
    records = JSON.parse(recordsConfigMap?.data?.['records.json'] || '{"services":{},"zones":[]}');
  } catch (_) {
    /* surfaced below */
  }
  const authoritativeZones = (records.zones || []).map((zone: string) => zone.replace(/\.$/, ''));
  const serviceDefinitions = records.services || {};
  const edgeByName = new Map((edges || []).map(raw).map((x: any) => [x.metadata?.name, x]));
  const nodeByName = new Map((nodes || []).map(raw).map((x: any) => [x.metadata?.name, x]));
  const selectedNameservers = new Set(nameserversInUse.map(normalizedDnsName));
  const powerDnsPods = (pods || [])
    .map(raw)
    .filter(
      (pod: any) =>
        pod.metadata?.namespace === 'regional-routing' &&
        pod.metadata?.labels?.['app.kubernetes.io/name'] === 'powerdns-cloud-authorities'
    );
  const powerDnsDaemonSet = (daemonSets || [])
    .map(raw)
    .find(
      (daemonSet: any) =>
        daemonSet.metadata?.namespace === 'regional-routing' &&
        daemonSet.metadata?.name === 'powerdns-cloud-authorities'
    );
  const powerDnsRows = powerDnsPods.map((pod: any) => {
    const nodeName = pod.spec?.nodeName || '-';
    const node = nodeByName.get(nodeName) as any;
    const nameserver = nodeName === '-' ? '-' : `ns-${nodeName}.re8ch.com.`;
    const podReady = isReady(pod);
    const nodeReady = isReady(node);
    const inUse = selectedNameservers.has(normalizedDnsName(nameserver));
    const publicIp =
      node?.status?.addresses?.find((address: any) => address.type === 'ExternalIP')?.address ||
      '-';
    const restarts = (pod.status?.containerStatuses || []).reduce(
      (total: number, container: any) => total + (container.restartCount || 0),
      0
    );
    return {
      nameserver,
      node: nodeName,
      publicIp,
      pod: pod.metadata?.name || '-',
      podReady,
      nodeReady,
      inUse,
      healthy: podReady && nodeReady && inUse,
      restarts,
    };
  });
  const powerDnsReady = powerDnsRows.filter(
    (server: any) => server.podReady && server.nodeReady
  ).length;
  const powerDnsHealthyInUse = powerDnsRows.filter((server: any) => server.healthy).length;
  const powerDnsDesired = powerDnsDaemonSet?.status?.desiredNumberScheduled ?? powerDnsRows.length;
  const dnsAuthorities = ingressObjects
    .filter((ingress: any) => {
      const labels = ingress.metadata?.labels || {};
      const annotations = ingress.metadata?.annotations || {};
      const enabled =
        labels['public.re8ch.com/external-dns'] === 'enabled' ||
        labels['networking.re8ch.com/dns-authority'] === 'public-edge';
      return enabled && Boolean(annotations['external-dns.alpha.kubernetes.io/hostname']);
    })
    .flatMap((ingress: any) => {
      const annotations = ingress.metadata?.annotations || {};
      const target = annotations['external-dns.alpha.kubernetes.io/target'] || '-';
      const selected =
        annotations['networking.re8ch.com/selected-public-edge'] ||
        (edges || []).map(raw).find((edge: any) => edge.spec?.endpoint?.value === target)?.metadata
          ?.name ||
        '-';
      return annotations['external-dns.alpha.kubernetes.io/hostname']
        .split(',')
        .map((hostname: string) => ({
          hostname: hostname.trim(),
          target,
          candidate: selected,
          provider: annotations['networking.re8ch.com/provider'] || 'ExternalDNS',
          ttl: annotations['external-dns.alpha.kubernetes.io/ttl'] || '-',
          source: `${ingress.metadata?.namespace}/${ingress.metadata?.name}`,
          ingressClass: ingress.spec?.ingressClassName || '-',
        }))
        .filter((authority: any) => authority.hostname);
    })
    .sort((a: any, b: any) => a.hostname.localeCompare(b.hostname));
  const edgeState = (edge: any) => {
    const node = nodeByName.get(edge?.spec?.nodeName) as any;
    if (!node) return 'Node missing';
    if (!node.status?.conditions?.some((c: any) => c.type === 'Ready' && c.status === 'True'))
      return 'Node NotReady';
    if (!edge?.spec?.enabled || edge?.spec?.draining) return 'Disabled / draining';
    const observed = Date.parse(edge?.status?.observedAt || '');
    if (!Number.isFinite(observed) || Math.abs(Date.now() - observed) > 60000)
      return 'Stale evidence';
    if (
      edge?.status?.observedGeneration &&
      edge.status.observedGeneration !== edge?.metadata?.generation
    )
      return 'Old generation';
    if (!condition(edge, 'Ready'))
      return (
        edge?.status?.conditions?.find((c: any) => c.type === 'Ready')?.reason || 'Unavailable'
      );
    if (!Object.values(edge?.status?.services || {}).some((s: any) => s.ready))
      return 'No ready service';
    return 'Ready';
  };
  const stateColor = (state: string) =>
    state === 'Ready'
      ? 'success'
      : ['No ready service', 'Old generation'].includes(state)
      ? 'warning'
      : 'error';

  const routeRows = publicRoutes.flatMap((route: any) =>
    (route.spec.hostnames || []).map((host: string) => {
      const normalizedHost = host.replace(/\.$/, '');
      const selfManaged = authoritativeZones.some(
        (zone: string) => normalizedHost === zone || normalizedHost.endsWith(`.${zone}`)
      );
      const serviceName = serviceDefinitions[`${normalizedHost}.`]?.service;
      const authority = ingressObjects.find(
        (x: any) =>
          x.metadata?.annotations?.['external-dns.alpha.kubernetes.io/hostname']
            ?.split(',')
            .includes(host) || (x.spec?.rules || []).some((rule: any) => rule.host === host)
      );
      const refs = (route.spec.rules || []).flatMap((r: any) => r.backendRefs || []);
      const backends = refs
        .map((b: any) => `${route.metadata.namespace}/${b.name}:${b.port}`)
        .join(', ');
      const backendNames = new Set(refs.map((b: any) => b.name));
      const readySlices = endpointSlices.filter(
        (s: any) =>
          s.metadata?.namespace === route.metadata.namespace &&
          backendNames.has(s.metadata?.labels?.['kubernetes.io/service-name'])
      );
      const sliceEndpoints = (s: any) => s.endpoints || s.spec?.endpoints || [];
      const endpointIPs = readySlices.flatMap((s: any) =>
        sliceEndpoints(s)
          .filter((e: any) => e.conditions?.ready === true)
          .flatMap((e: any) => e.addresses || [])
      );
      const readyPods = endpointIPs
        .map((ip: string) => podByIp.get(ip))
        .filter((p: any) =>
          p?.status?.conditions?.some((c: any) => c.type === 'Ready' && c.status === 'True')
        );
      const nodes =
        [...new Set(readyPods.map((p: any) => p.spec?.nodeName).filter(Boolean))].join(', ') || '-';
      const parents = (route.spec.parentRefs || [])
        .map(
          (p: any) => `${p.namespace || route.metadata.namespace}/${p.name}#${p.sectionName || '*'}`
        )
        .join(' → ');
      const routeParents = route.status?.parents || [];
      const accepted =
        routeParents.length > 0 &&
        routeParents.every((p: any) =>
          ['Accepted', 'ResolvedRefs'].every(type =>
            (p.conditions || []).some((c: any) => c.type === type && c.status === 'True')
          )
        );
      const hasBackends =
        refs.length > 0 &&
        [...backendNames].every(name =>
          readySlices.some(
            (s: any) =>
              s.metadata?.labels?.['kubernetes.io/service-name'] === name &&
              sliceEndpoints(s).some(
                (e: any) =>
                  e.conditions?.ready === true &&
                  (e.addresses || []).some((ip: string) =>
                    podByIp
                      .get(ip)
                      ?.status?.conditions?.some(
                        (c: any) => c.type === 'Ready' && c.status === 'True'
                      )
                  )
              )
          )
        );
      const serviceReadyEdges = (edges || [])
        .map(raw)
        .filter(
          (edge: any) =>
            edgeState(edge) === 'Ready' &&
            (!serviceName || edge.status?.services?.[serviceName]?.ready === true)
        )
        .sort((a: any, b: any) => (b.spec?.capacityMbps || 0) - (a.spec?.capacityMbps || 0));
      const publishedEdge =
        authority?.metadata?.annotations?.['networking.re8ch.com/selected-public-edge'];
      const selected = publishedEdge ? edgeByName.get(publishedEdge) : serviceReadyEdges[0];
      const selectedEdge = publishedEdge || selected?.metadata?.name;
      const target =
        authority?.metadata?.annotations?.['external-dns.alpha.kubernetes.io/target'] ||
        (selfManaged ? selected?.spec?.endpoint?.value : '');
      const exitReady = selfManaged
        ? serviceReadyEdges.length > 0
        : Boolean(selectedEdge && edgeState(edgeByName.get(selectedEdge)) === 'Ready');
      const ready = accepted && hasBackends && exitReady && (selfManaged || Boolean(target));
      return {
        host,
        edge: selectedEdge || '-',
        target: target || '-',
        parents,
        backends,
        nodes,
        accepted,
        ready,
        selfManaged,
        reason: !accepted
          ? 'Route not accepted/resolved'
          : !hasBackends
          ? 'No ready backend Pod'
          : !exitReady
          ? 'No ready public exit'
          : !selfManaged && !target
          ? 'No published DNS target'
          : 'Ready in cluster',
      };
    })
  );

  const serviceTable = (rows: any[]) => (
    <Table
      data={rows}
      columns={
        [
          { header: 'Hostname', accessorKey: 'host' },
          {
            header: 'Selected exit',
            accessorFn: (x: any) => `${x.edge} (${x.target})`,
          },
          { header: 'Gateway path', accessorKey: 'parents' },
          { header: 'Backend service', accessorKey: 'backends' },
          { header: 'Actual Pod nodes', accessorKey: 'nodes' },
          {
            header: 'Ingress class',
            accessorFn: (x: any) => {
              const authority = ingressObjects.find((i: any) =>
                (i.spec?.rules || []).some((r: any) => r.host === x.host)
              );
              return authority?.spec?.ingressClassName || '-';
            },
          },
          {
            header: 'Route',
            accessorFn: (x: any) => (
              <StatusLabel status={x.accepted ? 'success' : 'error'}>
                {x.accepted ? 'Accepted' : 'Problem'}
              </StatusLabel>
            ),
          },
          {
            header: 'In-cluster path',
            accessorFn: (x: any) => (
              <StatusLabel status={x.ready ? 'success' : 'error'}>{x.reason}</StatusLabel>
            ),
          },
        ] as any
      }
    />
  );

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h4">Public Edge</Typography>
      <Typography color="text.secondary">
        公网 Candidate、地域与容量，以及 ExternalDNS authority → Gateway listener → Service
        的实际发布路径。
      </Typography>
      {(edgeError || routeError || nodeError || configError) && (
        <Alert severity="error">
          无法读取 PublicEdge/Gateway/Node/ConfigMap API：
          {String(edgeError || routeError || nodeError || configError)}
        </Alert>
      )}
      <Stack direction="row" gap={1} flexWrap="wrap" sx={{ my: 2 }}>
        <Chip label={`Candidates ${(edges || []).length}`} />
        <Chip
          color={nameserversInUse.length > 1 ? 'success' : 'warning'}
          label={`Public DNS in use ${nameserversInUse.length}`}
        />
        <Chip
          color={powerDnsReady === powerDnsDesired && powerDnsDesired > 0 ? 'success' : 'warning'}
          label={`PowerDNS runtime ${powerDnsReady}/${powerDnsDesired} Ready`}
        />
        <Chip
          color={powerDnsHealthyInUse > 1 ? 'success' : 'warning'}
          label={`PowerDNS healthy & in use ${powerDnsHealthyInUse}`}
        />
      </Stack>
      <Tabs
        value={tab}
        onChange={(_, value) => setTab(value)}
        variant="scrollable"
        scrollButtons="auto"
        sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}
      >
        <Tab value="candidates" label="Candidates" />
        <Tab value="dns" label="DNS Authorities" />
        <Tab value="routes" label="Service Routes" />
        <Tab value="cdn" label="External CDN" />
      </Tabs>
      {tab === 'candidates' && (
        <SectionBox title={`Candidates (${(edges || []).length})`}>
          <Table
            data={(edges || []).map(raw)}
            columns={
              [
                {
                  header: 'Candidate',
                  accessorFn: (x: any) => x?.metadata?.name || '-',
                },
                {
                  header: 'Area / Region',
                  accessorFn: (x: any) => `${x?.spec?.area || '-'} / ${x?.spec?.region || '-'}`,
                },
                {
                  header: 'Node',
                  accessorFn: (x: any) => x?.spec?.nodeName || '-',
                },
                {
                  header: 'Public endpoint',
                  accessorFn: (x: any) => x?.spec?.endpoint?.value || '-',
                },
                {
                  header: 'Gateway VIP',
                  accessorFn: (x: any) => x?.spec?.gatewayVIP || '-',
                },
                {
                  header: 'Capacity',
                  accessorFn: (x: any) =>
                    x?.spec?.capacityMbps === null || x?.spec?.capacityMbps === undefined
                      ? '-'
                      : `${x.spec.capacityMbps} Mbps`,
                },
                {
                  header: 'State',
                  accessorFn: (x: any) => (
                    <StatusLabel status={stateColor(edgeState(x))}>{edgeState(x)}</StatusLabel>
                  ),
                },
                {
                  header: 'NPA evidence',
                  accessorFn: (x: any) =>
                    `${x?.status?.networkEvidence?.state || '-'} · ${
                      x?.status?.networkEvidence?.reason || '-'
                    }`,
                },
                {
                  header: 'Service probes',
                  accessorFn: (x: any) => {
                    const values = Object.values(x?.status?.services || {}) as any[];
                    return `${values.filter(v => v.ready).length}/${values.length} ready`;
                  },
                },
                {
                  header: 'Classes',
                  accessorFn: (x: any) => (
                    <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                      {(Array.isArray(x?.spec?.serviceClasses) ? x.spec.serviceClasses : []).map(
                        (v: string) => (
                          <Chip key={v} size="small" label={v} />
                        )
                      )}
                    </Box>
                  ),
                },
              ] as any
            }
          />
        </SectionBox>
      )}
      {tab === 'dns' && (
        <>
          <SectionBox title={`Public DNS nameservers in use (${nameserversInUse.length})`}>
            <Table
              data={nameserversInUse.map(nameserver => ({ nameserver }))}
              columns={[{ header: 'Elected nameserver', accessorKey: 'nameserver' }] as any}
            />
          </SectionBox>
          <SectionBox
            title={`PowerDNS health (${powerDnsHealthyInUse} healthy & in use / ${powerDnsDesired} desired)`}
          >
            <Alert severity={powerDnsHealthyInUse > 1 ? 'success' : 'warning'} sx={{ mb: 2 }}>
              “In use”来自 controller 维护的 public-edge-nameservers；“Healthy”同时要求 Pod
              Ready、Node Ready，且该 nameserver 已通过公网 UDP/TCP 53 资格检查进入使用集合。
            </Alert>
            <Table
              data={powerDnsRows}
              columns={
                [
                  { header: 'Nameserver', accessorKey: 'nameserver' },
                  { header: 'Node', accessorKey: 'node' },
                  { header: 'Public IP', accessorKey: 'publicIp' },
                  { header: 'Pod', accessorKey: 'pod' },
                  {
                    header: 'Runtime',
                    accessorFn: (x: any) => (
                      <StatusLabel status={x.podReady && x.nodeReady ? 'success' : 'error'}>
                        {x.podReady && x.nodeReady ? 'Ready' : 'Not Ready'}
                      </StatusLabel>
                    ),
                  },
                  {
                    header: 'Public DNS',
                    accessorFn: (x: any) => (
                      <StatusLabel status={x.inUse ? 'success' : 'warning'}>
                        {x.inUse ? 'In use' : 'Not elected'}
                      </StatusLabel>
                    ),
                  },
                  {
                    header: 'Health',
                    accessorFn: (x: any) => (
                      <StatusLabel status={x.healthy ? 'success' : 'error'}>
                        {x.healthy ? 'Healthy' : 'Unhealthy / unused'}
                      </StatusLabel>
                    ),
                  },
                  { header: 'Restarts', accessorKey: 'restarts' },
                ] as any
              }
            />
          </SectionBox>
          <SectionBox title={`Published hostname authorities (${dnsAuthorities.length})`}>
            <Table
              data={dnsAuthorities}
              columns={
                [
                  { header: 'Hostname', accessorKey: 'hostname' },
                  { header: 'DNS target', accessorKey: 'target' },
                  { header: 'Candidate', accessorKey: 'candidate' },
                  { header: 'Provider', accessorKey: 'provider' },
                  { header: 'TTL', accessorKey: 'ttl' },
                  { header: 'Ingress class', accessorKey: 'ingressClass' },
                  { header: 'Authority resource', accessorKey: 'source' },
                ] as any
              }
            />
          </SectionBox>
        </>
      )}
      {tab === 'routes' && (
        <>
          <SectionBox
            title={`Authoritative DNS services (${
              routeRows.filter((x: any) => x.selfManaged).length
            })`}
          >
            {serviceTable(routeRows.filter((x: any) => x.selfManaged))}
          </SectionBox>
          <SectionBox
            title={`Legacy published services (${
              routeRows.filter((x: any) => !x.selfManaged).length
            })`}
          >
            {serviceTable(routeRows.filter((x: any) => !x.selfManaged))}
          </SectionBox>
        </>
      )}
      {tab === 'cdn' && (
        <SectionBox title={`External CDN sites (${externalSites.length})`}>
          <Table
            data={externalSites.flatMap(site => site.hostnames.map(host => ({ ...site, host })))}
            columns={
              [
                { header: 'Hostname', accessorKey: 'host' },
                { header: 'Site', accessorKey: 'name' },
                { header: 'Provider', accessorKey: 'provider' },
                { header: 'Delivery', accessorKey: 'delivery' },
                { header: 'Origin', accessorKey: 'origin' },
                { header: 'DNS mode', accessorKey: 'dnsMode' },
                { header: 'Source of truth', accessorKey: 'sourceOfTruth' },
                { header: 'Notes', accessorFn: (x: any) => x.notes || '-' },
              ] as any
            }
          />
        </SectionBox>
      )}
      <Typography variant="caption" color="text.secondary">
        集群内 Ready 不证明公网 DNS 或 TLS 可达；请同时查看 PublicEdge public DNS authority
        的外部监测。当前发现 {podByIp.size} 个可寻址 Pod。
      </Typography>
    </Box>
  );
}

registerSidebarEntry({
  name: 'public-edge',
  url: '/public-edge',
  icon: 'mdi:wan',
  parent: '',
  label: 'Public Edge',
});
registerRoute({
  path: '/public-edge',
  sidebar: 'public-edge',
  name: 'Public Edge',
  component: () => <Dashboard />,
});
