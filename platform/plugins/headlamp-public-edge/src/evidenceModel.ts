// Read-only interpretation of published facts, never a second eligibility controller.
export const isFederated = (edge: any) => edge?.spec?.discovery?.source === 'AmongCluster';
const condition = (values: any[], name: string) =>
  (values || []).find(value => value.type === name);

export function candidateState(
  edge: any,
  node: any,
  nodesAvailable = true,
  now = Date.now()
): string {
  if (!edge?.spec?.enabled || edge.spec.draining) return 'Disabled / draining';
  const remote = isFederated(edge);
  if (!remote) {
    if (!nodesAvailable) return 'Unknown: local Node data unavailable';
    if (!node) return 'Node missing';
    if (condition(node.status?.conditions, 'Ready')?.status !== 'True') return 'Node NotReady';
  }
  const observed = Date.parse(edge?.status?.observedAt || '');
  if (!Number.isFinite(observed)) return 'Unknown: observation missing';
  if (now - observed > 60000 || observed > now + 5000) return 'Stale evidence';
  if (edge.status.observedGeneration === undefined) return 'Unknown: generation missing';
  if (edge.status.observedGeneration !== edge.metadata?.generation) return 'Old generation';
  if (remote) {
    const discovery = edge.spec.discovery;
    const network = edge.status.networkEvidence;
    const federation = network?.federation;
    if (
      !discovery.advertisementRef?.uid ||
      !discovery.importedServiceRef?.uid ||
      !discovery.endpointId
    )
      return 'Unknown: unbound federation';
    if (!federation) return 'Unknown: federation observation missing';
    if (federation.eligible === false) return federation.reason || 'Federation rejected';
    if (federation.eligible !== true) return 'Unknown: federation eligibility missing';
    if (
      federation.advertisementRef?.uid !== discovery.advertisementRef.uid ||
      federation.importedServiceRef?.uid !== discovery.importedServiceRef.uid ||
      federation.publisherRef !== discovery.clusterRef ||
      federation.serviceIdentity !== discovery.serviceRef ||
      federation.endpointId !== discovery.endpointId
    )
      return 'Unknown: federation identity mismatch';
    if (!Number.isFinite(federation.expiresAt)) return 'Unknown: federation expiry missing';
    if (now >= federation.expiresAt * 1000) return 'Federation expired';
    if (network.eligible === false) return network.reason || 'Network evidence rejected';
    if (
      network.eligible !== true ||
      !network.assessmentRef?.uid ||
      !network.assessmentRef?.scope?.sourceNode
    )
      return 'Unknown: scoped network evidence missing';
    const validUntil = Date.parse(network.validUntil || '');
    if (!Number.isFinite(validUntil)) return 'Unknown: network validity missing';
    if (now >= validUntil) return 'Network evidence expired';
    const source = network.assessmentRef.scope.sourceNode;
    const expected = discovery.assessmentRefs?.[source] || discovery.assessmentRef;
    if (!expected || expected.uid !== network.assessmentRef.uid || expected.sourceNode !== source)
      return 'Unknown: assessment reference mismatch';
    const backend = condition(edge.status.backendConditions, 'ServiceBackendReady');
    if (!backend) return 'Unknown: backend observation missing';
    if (backend.status !== 'True') return backend.reason || 'Backend unavailable';
  } else if (condition(edge.status.conditions, 'Ready')?.status !== 'True') {
    return condition(edge.status.conditions, 'Ready')?.reason || 'Unavailable';
  }
  const services: any[] = Object.values(edge.status.services || {});
  if (!services.length) return 'Unknown: service probes missing';
  if (!services.some(value => value.ready === true)) return 'No ready service';
  return 'Ready';
}

export function qualityState(quality: any): string {
  if (!quality || !(quality.probes > 0) || !(quality.finished > 0))
    return 'Unknown: no completed probes';
  if (quality.finished < quality.probes) return 'Pending probes';
  return quality.ready === true ? 'Ready' : quality.ready === false ? 'Problem' : 'Unknown';
}

export function backendState(
  refs: any[],
  slices: any[],
  pods: Map<string, any>,
  available = true
): string {
  if (!available) return 'Unknown: backend data unavailable';
  if (!refs.length) return 'No backend reference';
  for (const ref of refs) {
    if (ref.kind && ref.kind !== 'Service') return 'Unknown: non-Service backend';
    const matching = slices.filter(
      slice =>
        slice.metadata?.namespace === ref.namespace &&
        slice.metadata?.labels?.['kubernetes.io/service-name'] === ref.name
    );
    if (!matching.length) return 'No EndpointSlice';
    const endpoints = matching.flatMap(slice => slice.endpoints || slice.spec?.endpoints || []);
    const ready = endpoints.filter(endpoint => endpoint.conditions?.ready === true);
    if (!ready.length) return 'No ready endpoint';
    const localPods = ready.flatMap(endpoint =>
      (endpoint.addresses || []).map((ip: string) => pods.get(ip))
    );
    if (localPods.some(pod => condition(pod?.status?.conditions, 'Ready')?.status === 'True'))
      continue;
    if (ready.some(endpoint => endpoint.targetRef?.kind !== 'Pod') || localPods.some(pod => !pod))
      return 'Unknown: remote endpoint Pod not observed';
    return 'Backend Pod NotReady';
  }
  return 'Ready';
}

export function federationDetails(edge: any) {
  const discovery = edge.spec?.discovery || {};
  const network = edge.status?.networkEvidence || {};
  const observed = network.federation || {};
  return {
    owner: observed.publisherRef || discovery.clusterRef || 'Unknown',
    identity: observed.serviceIdentity || discovery.serviceRef || 'Unknown',
    endpoint: observed.endpointId || discovery.endpointId || 'Unknown',
    source: network.assessmentRef?.scope?.sourceNode || 'Unknown',
    expires: Number.isFinite(observed.expiresAt)
      ? new Date(observed.expiresAt * 1000).toISOString()
      : 'Unknown',
    reason: [observed.reason, network.reason].filter(Boolean).join(' / ') || 'Unknown',
  };
}
