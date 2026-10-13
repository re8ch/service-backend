import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';
import {createService, configFromEnv} from './server.mjs';

class FakeStagehand {
  static options;
  constructor(options) { FakeStagehand.options = options; }
  async init() {}
  async close() {}
  page = {currentUrl: 'about:blank', goto: async url => { this.page.currentUrl = url; }, url: () => this.page.currentUrl};
  context = {activePage: () => this.page};
  async act(instruction) { return {instruction}; }
  async observe(instruction) { return [{description: instruction}]; }
  async extract(instruction) { return {extraction: instruction}; }
}

const request = (port, path, options = {}) => new Promise((resolve, reject) => {
  const req = http.request({port, path, method: options.method ?? 'GET', headers: options.headers}, res => {
    const chunks = []; res.on('data', chunk => chunks.push(chunk));
    res.on('end', () => resolve({status: res.statusCode, body: Buffer.concat(chunks).toString()}));
  });
  req.on('error', reject); if (options.body) req.write(JSON.stringify(options.body)); req.end();
});

test('requires runtime secrets', () => {
  assert.throws(() => configFromEnv({}), /STAGEHAND_API_TOKEN/);
  assert.throws(() => configFromEnv({STAGEHAND_API_TOKEN: 'x'}), /LITELLM_API_KEY/);
});

test('creates one authenticated session wired to LiteLLM and Steel', async t => {
  const calls = [];
  const fakeFetch = async (url, options) => {
    calls.push([url, options?.method]);
    return {ok: true, json: async () => ({id: 'session-1'})};
  };
  const service = createService({apiToken: 'secret', llmApiKey: 'llm-key', model: 'openai/kimi-k2-5',
    llmBaseUrl: 'http://litellm:4000/v1', steelUrl: 'http://steel:3000', steelInternalHost: 'steel:3000'},
  {fetch: fakeFetch, Stagehand: FakeStagehand});
  const server = http.createServer(service.handler).listen(0);
  t.after(async () => { await service.close(); server.close(); });
  const port = server.address().port;
  assert.equal((await request(port, '/healthz')).status, 200);
  assert.equal((await request(port, '/v1/sessions', {method: 'POST'})).status, 401);
  const created = await request(port, '/v1/sessions', {method: 'POST',
    headers: {authorization: 'Bearer secret', 'content-type': 'application/json'}, body: {url: 'https://example.com'}});
  assert.equal(created.status, 201);
  assert.equal(FakeStagehand.options.model.baseURL, 'http://litellm:4000/v1');
  assert.equal(FakeStagehand.options.localBrowserLaunchOptions.cdpUrl, 'ws://steel:3000/v1/sessions/session-1/cdp');
  assert.equal((await request(port, '/v1/sessions', {method: 'POST', headers: {authorization: 'Bearer secret'}})).status, 409);
  assert.equal(calls[0][0], 'http://steel:3000/v1/sessions');
});
