import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { assertRendered, configuration, expectedCells, inventoryFrom, run } from './acceptance.mjs';

const env = { STAGEHAND_MODEL: 'openai/gpt-4.1', STAGEHAND_MODEL_API_KEY: 'test-only',
  STAGEHAND_CDP_URL: 'http://127.0.0.1:9222',
  RE8CH_DIRECTORY_DETAIL_URL: 'https://tools.re8ch.com/catalog/test/server/0.1.0' };
const inventory = inventoryFrom(JSON.parse(await readFile(new URL('../registry.runtime.json', import.meta.url))));

test('rejects unrelated targets, remote browsers and credentials in URLs', () => {
  assert.equal(configuration(env).target, env.RE8CH_DIRECTORY_DETAIL_URL);
  for (const target of ['https://example.com/catalog/a/b/c',
    'https://tools.re8ch.com/api/admin', 'https://tools.re8ch.com/catalog/a/b/c?token=x',
    'https://user:password@tools.re8ch.com/catalog/a/b/c']) {
    assert.throws(() => configuration({ ...env, RE8CH_DIRECTORY_DETAIL_URL: target }));
  }
  assert.throws(() => configuration({ ...env, STAGEHAND_CDP_URL: 'http://remote:9222' }));
});

test('missing configuration cannot produce semantic success or initiate browser work', async () => {
  const report = await run({});
  assert.equal(report.inventoryNavigation, 'not_run');
  assert.equal(report.semanticConformance, 'not_run');
  assert.equal(report.failureStage, 'configuration');
  assert.deepEqual(report.cases, []);
});

test('detects missing rows, stale counts and false acceptance labels', () => {
  const rows = inventory.operations;
  const observed = { status: `Showing ${rows.length} of ${rows.length} inventoried operations`,
    rows: rows.map(expectedCells) };
  assert.doesNotThrow(() => assertRendered(observed, rows, rows.length));
  assert.throws(() => assertRendered({ ...observed, rows: observed.rows.slice(1) }, rows, rows.length));
  assert.throws(() => assertRendered({ ...observed, status: 'Showing 0 of 0 inventoried operations' }, rows, rows.length));
  const misleading = structuredClone(observed);
  misleading.rows[0][3] = 'HTTP: passed UI semantics: passed';
  assert.throws(() => assertRendered(misleading, rows, rows.length));
});

test('empty results must preserve the total and display the explicit empty state', () => {
  assert.doesNotThrow(() => assertRendered({ status: 'Showing 0 of 618 inventoried operations',
    rows: [['No operations match this filter.']] }, [], 618));
  assert.throws(() => assertRendered({ status: 'Showing 0 of 618 inventoried operations', rows: [] }, [], 618));
});
