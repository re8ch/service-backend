'use strict';
// The host injects the existing authenticated MCP connection; no URLs or credentials.
async function runAcceptance({cases, catalog, call, checkpoint, now = () => new Date().toISOString(), wait = ms => new Promise(r => setTimeout(r, ms))}) {
  const decode = envelope => {
    if (envelope?.isError) throw new Error('MCP_ERROR');
    return envelope.structuredContent ?? JSON.parse(envelope.content.find(x => x.type === 'text').text);
  };
  const checks = ['status_code_conformance', 'content_type_conformance', 'response_headers_conformance', 'response_schema_conformance'];
  const transient = value => /identity_unavailable|HTTP 503|HTTP 502|connection refused|timed? out|PLUGIN_HTTP_BUSY/i.test(value);
  const report = {version: 1, startedAt: now(), scope: 'finite_GET_cases_only', cases: []};
  const ids = new Set();
  for (const c of cases) {
    if (!c || typeof c.id !== 'string' || ids.has(c.id) || c.backend !== 'native' || c.toolName !== 'plugin.operation.http-check' || c.expectedStatus !== 200 || !/^[a-f0-9]{64}$/.test(c.arguments?.schemaHash) || c.arguments?.operationId !== c.id || !/^apis\/[a-z0-9.-]+\/[a-z0-9]+$/.test(c.arguments?.groupVersion)) throw new Error('Invalid finite GET case');
    ids.add(c.id);
  }
  let available;
  try { available = decode(await catalog()); } catch { available = null; }
  const tool = available?.tools?.find(t => t.backend === 'native' && t.name === 'plugin.operation.http-check');
  for (const c of cases) {
    const row = {caseId: c.id, status: 'not_run', attempts: []};
    report.cases.push(row);
    if (!tool || available?.unavailableBackends?.includes('native')) {
      row.status = 'blocked'; row.reason = 'backend_or_catalog_unavailable';
      await checkpoint(report); continue;
    }
    for (let i = 0; i < 3; i++) {
      const attempt = {number: i + 1, startedAt: now()};
      let retry = false;
      try {
        const envelope = await call({backend: c.backend, toolName: c.toolName, arguments: c.arguments});
        if (envelope?.isError) {
          retry = transient(JSON.stringify(envelope));
          attempt.status = retry ? 'blocked' : 'failed';
          attempt.reason = retry ? 'transient_transport' : 'tool_error';
        } else {
          const result = decode(envelope);
          const valid = result.cluster === 're8ch-k3s' && result.operationId === c.id && result.schemaHash === c.arguments.schemaHash && result.engine === 'schemathesis' && result.scope === 'one_finite_GET_response' && result.networkRequests === 1 && result.bodyReturned === false && /^audit:\/\/operations\/op_[a-z0-9]+$/.test(result.auditRef) && Array.isArray(result.checks) && result.checks.length === 4 && checks.every(name => result.checks.filter(x => x.check === name).length === 1);
          const passed = valid && result.statusCode === 200 && result.httpConformance === 'passed' && result.checks.every(x => x.status === 'passed');
          attempt.status = passed ? 'passed' : 'failed';
          attempt.reason = passed ? 'contract_passed' : 'contract_failed_or_invalid';
          // Deliberate projection: never persist a raw envelope/body/error message.
          if (valid) attempt.evidence = {cluster: result.cluster, operationId: result.operationId, schemaHash: result.schemaHash, auditRef: result.auditRef, statusCode: result.statusCode, checks: result.checks.map(x => ({check: x.check, status: ['passed','failed','error'].includes(x.status) ? x.status : 'error'}))};
        }
      } catch (error) {
        retry = transient(String(error));
        attempt.status = retry ? 'blocked' : 'failed';
        attempt.reason = retry ? 'transient_transport' : 'invalid_response_or_transport';
      }
      attempt.finishedAt = now();
      row.attempts.push(attempt); row.status = attempt.status;
      row.recoveredAfterRetry = row.status === 'passed' && row.attempts.length > 1;
      await checkpoint(report);
      if (!retry || i === 2) break;
      await wait((i + 1) * 1000);
    }
  }
  report.finishedAt = now();
  report.summary = Object.fromEntries(['passed','failed','blocked','not_run'].map(status => [status, report.cases.filter(c => c.status === status).length]));
  report.complete = report.cases.length > 0 && report.cases.every(c => c.status === 'passed');
  await checkpoint(report);
  return report;
}
if (typeof module !== 'undefined') module.exports = {runAcceptance};
