import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

const catalog = JSON.parse(open('./contracts.json'));
const contractFailures = new Rate('contract_failures');

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
  for (const probe of catalog.probes) {
    const response = http.get(probe.url, {
      headers: probe.accept ? { Accept: probe.accept } : {},
      redirects: 0,
      timeout: '15s',
      tags: { probe: probe.id },
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
