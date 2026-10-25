export const MAX_AGE_MS = 15 * 60 * 1000;
export function runtimeState(status, sha, now = Date.now()) {
  if (!status || status.sha !== sha) return {state: 'unknown', reason: 'No evidence for this revision'};
  const observed = Date.parse(status.created_at);
  if (!Number.isFinite(observed) || observed > now || now - observed >= MAX_AGE_MS)
    return {state: 'unknown', reason: 'Verification evidence expired or has an invalid timestamp'};
  if (status.state === 'success') return {state: 'success', reason: 'Flux reported successful reconciliation'};
  if (status.state === 'failure' || status.state === 'error') return {state: 'failure', reason: 'Flux reported a failure'};
  return {state: 'unknown', reason: 'Reconciliation has not completed'};
}
export function latestStatuses(statuses) {
  const result = new Map();
  for (const status of statuses) {
    const old = result.get(status.context);
    if (!old || Date.parse(status.created_at) > Date.parse(old.created_at)) result.set(status.context, status);
  }
  return [...result.values()];
}

export const RESOURCE_TYPES = [
  ['kustomization', 'Kustomizations'], ['helmrelease', 'Helm Releases'],
  ['gitrepository', 'Git Repositories'], ['ocirepository', 'OCI Repositories'],
  ['helmrepository', 'Helm Repositories'], ['helmchart', 'Helm Charts'],
  ['alert', 'Alerts'], ['provider', 'Providers'], ['receiver', 'Receivers'],
];
export function resourceGroups(statuses, sha, now = Date.now()) {
  const latest = latestStatuses(statuses);
  return RESOURCE_TYPES.map(([kind, title]) => {
    const resources = latest.filter(s => s.context.startsWith(`flux/${kind}/`)).map(s => ({
      ...s, name: s.context.slice(`flux/${kind}/`.length), kind,
      evidence: runtimeState({...s, sha}, sha, now),
    }));
    return {kind, title, resources, total: resources.length,
      success: resources.filter(s => s.evidence.state === 'success').length,
      failure: resources.filter(s => s.evidence.state === 'failure').length,
      unknown: resources.filter(s => s.evidence.state === 'unknown').length};
  });
}
