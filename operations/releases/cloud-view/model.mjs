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
