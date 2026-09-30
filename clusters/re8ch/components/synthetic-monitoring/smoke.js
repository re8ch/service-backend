import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';
import exec from 'k6/execution';

const catalog = JSON.parse(open('./contracts.json'));
const contractFailures = new Rate('contract_failures');
const vantage = __ENV.PROBE_VANTAGE || 'external';
const cadence = __ENV.PROBE_CADENCE || 'all';
const probes = catalog.probes.filter((probe) =>
  probe.vantage === vantage && (cadence === 'all' || probe.cadence === cadence));
const expectedStatuses = Object.fromEntries(
  [...new Set(probes.map((probe) => probe.status))].map((status) => [status, http.expectedStatuses(status)]));
if (probes.length === 0) {
  throw new Error(`No probes for ${vantage}/${cadence}`);
}

export const options = {
  scenarios: {
    smoke: { executor: 'shared-iterations', vus: 1, iterations: 3, maxDuration: '2m' },
  },
  thresholds: {
    contract_failures: ['rate==0'],
    http_req_duration: ['p(95)<15000'],
  },
};

export default function () {
  for (const probe of probes) {
    const runID = `${Date.now()}-${exec.scenario.iterationInTest}`;
    const response = http.get(probe.url, {
      headers: {
        ...(probe.accept ? { Accept: probe.accept } : {}),
        'X-Observable-Synthetic': 'true',
        'X-Observable-Run-ID': runID,
        'X-Observable-Journey': probe.id,
        'X-Observable-Agent': 'robot',
        'X-Observable-Load-Class': 'probe',
        baggage: `synthetic_request=true,observable.journey=${probe.id},observable.agent=robot,observable.load_class=probe`,
      },
      redirects: 0,
      timeout: '15s',
      responseCallback: expectedStatuses[probe.status],
      tags: { probe: probe.id, owner: probe.owner, vantage, cadence: probe.cadence, load_class: 'probe' },
    });
    let valid = response.status === probe.status;
    if (valid && probe.jsonField) {
      try {
        valid = response.json(probe.jsonField) === probe.equals;
      } catch (_) {
        valid = false;
      }
    }
    if (valid && probe.header) {
      valid = String(response.headers[probe.header] || '').includes(probe.contains);
    }
    contractFailures.add(!valid, { probe: probe.id });
    check(response, { [`${probe.id} contract`]: () => valid }, { probe: probe.id });
  }
}
