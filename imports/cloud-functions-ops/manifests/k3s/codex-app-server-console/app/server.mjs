import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import crypto from 'node:crypto';
import path from 'node:path';
import { URL } from 'node:url';

const port = Number(process.env.PORT || 8080);
const authBaseUrl = (process.env.AUTH_BASE_URL || 'https://api.re8ch.com/auth').replace(/\/+$/, '');
const authService = process.env.AUTH_SERVICE || 'codex';
const appBasePath = process.env.APP_BASE_PATH || '/codex-console';
const codexHost = process.env.CODEX_APP_SERVER_HOST || 'codex-app-server.codex-app-server.svc.cluster.local';
const codexPort = Number(process.env.CODEX_APP_SERVER_PORT || 4500);
const codexToken = process.env.CODEX_APP_SERVER_TOKEN || '';
const codexCwd = process.env.CODEX_DEFAULT_CWD || '/workspace';
const operatorGroups = new Set((process.env.OPERATOR_GROUPS || 'service:codex:operator,platform:admin,re8ch:admin')
  .split(',').map((value) => value.trim()).filter(Boolean));
const internalDirectHosts = new Set((process.env.INTERNAL_DIRECT_HOSTS || '')
  .split(',').map((value) => value.trim().toLowerCase()).filter(Boolean));
const internalSourcePrefixes = (process.env.INTERNAL_SOURCE_PREFIXES || '10.181.22.')
  .split(',').map((value) => value.trim()).filter(Boolean);
const serviceTokenPath = '/var/run/secrets/kubernetes.io/serviceaccount/token';
const serviceCaPath = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt';
const namespacePath = '/var/run/secrets/kubernetes.io/serviceaccount/namespace';
const k8sHost = process.env.KUBERNETES_SERVICE_HOST;
const k8sPort = process.env.KUBERNETES_SERVICE_PORT_HTTPS || '443';
const indexHtml = fs.readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), 'index.html'), 'utf8');
const serviceToken = fs.readFileSync(serviceTokenPath, 'utf8').trim();
const serviceCa = fs.readFileSync(serviceCaPath);
const ownNamespace = fs.readFileSync(namespacePath, 'utf8').trim();
const startedAt = Date.now();

const controlMethods = new Set([
  'config/read', 'config/value/write', 'config/batchWrite', 'configRequirements/read',
  'config/mcpServer/reload', 'model/list', 'experimentalFeature/list',
  'experimentalFeature/enablement/set', 'collaborationMode/list', 'skills/list',
  'skills/config/write', 'hooks/list', 'mcpServerStatus/list', 'mcpServer/resource/read',
  'thread/list', 'thread/loaded/list', 'thread/read', 'thread/turn/list', 'thread/start',
  'thread/resume', 'thread/fork', 'thread/name/set', 'thread/metadata/update',
  'thread/archive', 'thread/unarchive', 'thread/compact/start', 'thread/unsubscribe',
  'turn/start', 'turn/steer', 'turn/interrupt', 'review/start', 'account/read',
  'account/rateLimits/read', 'account/usage/read', 'app/list', 'plugin/list',
]);

const encodeWsFrame = (value, opcode = 1) => {
  const payload = Buffer.from(value);
  const mask = crypto.randomBytes(4);
  let header;
  if (payload.length < 126) header = Buffer.from([0x80 | opcode, 0x80 | payload.length]);
  else if (payload.length < 65536) {
    header = Buffer.alloc(4); header[0] = 0x80 | opcode; header[1] = 0xfe; header.writeUInt16BE(payload.length, 2);
  } else {
    header = Buffer.alloc(10); header[0] = 0x80 | opcode; header[1] = 0xff; header.writeBigUInt64BE(BigInt(payload.length), 2);
  }
  const masked = Buffer.alloc(payload.length);
  for (let index = 0; index < payload.length; index += 1) masked[index] = payload[index] ^ mask[index % 4];
  return Buffer.concat([header, mask, masked]);
};

class CodexRpcSocket {
  constructor() { this.buffer = Buffer.alloc(0); this.messages = []; this.waiters = []; this.handshake = false; }
  async connect() {
    if (!codexToken) throw new Error('Codex capability token is not configured');
    this.socket = net.createConnection({host: codexHost, port: codexPort});
    this.socket.on('data', (chunk) => this.onData(chunk));
    this.socket.on('error', (error) => this.rejectAll(error));
    const key = crypto.randomBytes(16).toString('base64');
    this.socket.write([
      'GET / HTTP/1.1', `Host: ${codexHost}:${codexPort}`, 'Upgrade: websocket', 'Connection: Upgrade',
      `Sec-WebSocket-Key: ${key}`, 'Sec-WebSocket-Version: 13', `Authorization: Bearer ${codexToken}`, '\r\n',
    ].join('\r\n'));
    await this.waitFor((message) => message?.handshake === true, 10000);
  }
  onData(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    if (!this.handshake) {
      const end = this.buffer.indexOf('\r\n\r\n');
      if (end < 0) return;
      const head = this.buffer.subarray(0, end).toString(); this.buffer = this.buffer.subarray(end + 4);
      if (!head.startsWith('HTTP/1.1 101')) return this.rejectAll(new Error(`WebSocket handshake failed: ${head.split('\r\n')[0]}`));
      this.handshake = true; this.push({handshake: true});
    }
    while (this.buffer.length >= 2) {
      const opcode = this.buffer[0] & 0x0f; let length = this.buffer[1] & 0x7f; let offset = 2;
      if (length === 126) { if (this.buffer.length < 4) return; length = this.buffer.readUInt16BE(2); offset = 4; }
      else if (length === 127) { if (this.buffer.length < 10) return; length = Number(this.buffer.readBigUInt64BE(2)); offset = 10; }
      const masked = Boolean(this.buffer[1] & 0x80); const required = offset + (masked ? 4 : 0) + length;
      if (this.buffer.length < required) return;
      let mask; if (masked) { mask = this.buffer.subarray(offset, offset + 4); offset += 4; }
      const payload = Buffer.from(this.buffer.subarray(offset, offset + length)); this.buffer = this.buffer.subarray(required);
      if (masked) for (let index = 0; index < payload.length; index += 1) payload[index] ^= mask[index % 4];
      if (opcode === 9) { this.socket.write(encodeWsFrame(payload.toString(), 10)); continue; }
      if (opcode === 8) { this.rejectAll(new Error('Codex WebSocket closed')); return; }
      if (opcode === 1) { try { this.push(JSON.parse(payload.toString())); } catch {} }
    }
  }
  push(message) {
    const index = this.waiters.findIndex((waiter) => waiter.predicate(message));
    if (index >= 0) { const [waiter] = this.waiters.splice(index, 1); clearTimeout(waiter.timer); waiter.resolve(message); }
    else this.messages.push(message);
  }
  waitFor(predicate, timeout = 30000) {
    const index = this.messages.findIndex(predicate);
    if (index >= 0) return Promise.resolve(this.messages.splice(index, 1)[0]);
    return new Promise((resolve, reject) => {
      const waiter = {predicate, resolve, reject};
      waiter.timer = setTimeout(() => { this.waiters = this.waiters.filter((item) => item !== waiter); reject(new Error('Codex RPC timeout')); }, timeout);
      this.waiters.push(waiter);
    });
  }
  rejectAll(error) { for (const waiter of this.waiters) { clearTimeout(waiter.timer); waiter.reject(error); } this.waiters = []; }
  send(method, id, params) { this.socket.write(encodeWsFrame(JSON.stringify({method, id, params}))); }
  close() { this.socket?.end(encodeWsFrame('', 8)); }
}

const codexRpc = async (method, params = {}) => {
  if (!controlMethods.has(method)) throw new Error(`RPC method is not enabled: ${method}`);
  const client = new CodexRpcSocket();
  try {
    await client.connect();
    client.send('initialize', 1, {
      clientInfo: {name: 're8ch_codex_control_plane', title: 'RE8CH Codex Control Plane', version: '0.1.0'},
      capabilities: {experimentalApi: true, mcpServerOpenaiFormElicitation: true},
    });
    const initialized = await client.waitFor((message) => message.id === 1, 15000);
    if (initialized.error) throw new Error(initialized.error.message || 'Codex initialize failed');
    client.socket.write(encodeWsFrame(JSON.stringify({method: 'initialized', params: {}})));
    client.send(method, 2, params);
    const response = await client.waitFor((message) => message.id === 2, 120000);
    if (response.error) throw new Error(response.error.message || JSON.stringify(response.error));
    return response.result;
  } finally { client.close(); }
};

const readBody = async (req) => {
  let raw = '';
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 1024 * 1024) throw new Error('request body too large');
  }
  return raw ? JSON.parse(raw) : {};
};

const json = (res, status, value) => {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'same-origin',
  });
  res.end(body);
};

const requestJson = (url, options = {}) => new Promise((resolve, reject) => {
  const parsed = new URL(url);
  const client = parsed.protocol === 'https:' ? https : http;
  const request = client.request(parsed, options, (response) => {
    let body = '';
    response.setEncoding('utf8');
    response.on('data', (chunk) => {
      body += chunk;
      if (body.length > 4 * 1024 * 1024) request.destroy(new Error('response too large'));
    });
    response.on('end', () => {
      let value = {};
      try { value = body ? JSON.parse(body) : {}; } catch { value = {raw: body}; }
      if ((response.statusCode || 500) >= 300) {
        const error = new Error(`HTTP ${response.statusCode}`);
        error.status = response.statusCode;
        error.body = value;
        reject(error);
        return;
      }
      resolve(value);
    });
  });
  request.setTimeout(options.timeout || 4000, () => request.destroy(new Error('request timeout')));
  request.on('error', reject);
  request.end();
});

const k8sGet = (apiPath) => requestJson(`https://${k8sHost}:${k8sPort}${apiPath}`, {
  method: 'GET',
  ca: serviceCa,
  headers: {authorization: `Bearer ${serviceToken}`, accept: 'application/json'},
  timeout: 5000,
});

const verifyIdentity = async (req) => {
  if (isTrustedInternal(req)) {
    return {
      subject: 'internal-operator',
      email: 'internal-operator@re8ch.local',
      name: '内网运营',
      groups: [...operatorGroups],
      authSource: 'trusted-internal',
    };
  }
  const cookie = req.headers.cookie || '';
  const authorization = req.headers.authorization || '';
  if (!cookie && !authorization) return null;
  try {
    const identity = await requestJson(`${authBaseUrl}/me`, {
      method: 'GET',
      headers: {
        ...(cookie ? {cookie} : {}),
        ...(authorization ? {authorization} : {}),
        'user-agent': req.headers['user-agent'] || 'codex-app-server-console',
        'x-forwarded-host': req.headers['x-forwarded-host'] || req.headers.host || '',
        'x-forwarded-proto': req.headers['x-forwarded-proto'] || 'https',
      },
      timeout: 4000,
    });
    return identity?.identity || null;
  } catch {
    return null;
  }
};

const isTrustedInternal = (req) => {
  const host = String(req.headers.host || '').toLowerCase();
  const source = String(req.socket.remoteAddress || '').replace(/^::ffff:/, '');
  return internalDirectHosts.has(host) &&
    internalSourcePrefixes.some((prefix) => source.startsWith(prefix));
};

const parseCpu = (value = '0') => {
  if (value.endsWith('n')) return Number(value.slice(0, -1)) / 1_000_000;
  if (value.endsWith('u')) return Number(value.slice(0, -1)) / 1_000;
  if (value.endsWith('m')) return Number(value.slice(0, -1));
  return Number(value) * 1000;
};

const parseMemory = (value = '0') => {
  const match = String(value).match(/^([\d.]+)([KMGTE]i)?$/);
  if (!match) return 0;
  const scale = {Ki: 1 / 1024, Mi: 1, Gi: 1024, Ti: 1024 ** 2};
  return Number(match[1]) * (scale[match[2]] || 1 / (1024 ** 2));
};

const labelsMatch = (selector = {}, labels = {}) =>
  Object.entries(selector).every(([key, value]) => labels[key] === value);

const isAppServer = (deployment) => {
  const name = deployment.metadata?.name || '';
  if (name.includes('codex-app-server-console')) return false;
  const labels = deployment.metadata?.labels || {};
  const annotations = deployment.metadata?.annotations || {};
  const containers = deployment.spec?.template?.spec?.containers || [];
  return name.includes('codex-app-server') ||
    labels['app.kubernetes.io/component'] === 'app-server' ||
    annotations['codex.re8ch.com/managed'] === 'true' ||
    containers.some((container) =>
      (container.command || []).join(' ').includes('codex app-server') ||
      (container.args || []).join(' ').includes('codex app-server'));
};

const stateFrom = (ready, taskState, terminating) => {
  if (terminating) return 'terminating';
  if (!ready) return 'unhealthy';
  if (['queued', 'pending', '排队'].includes(taskState)) return 'queued';
  if (['running', 'working', '处理中', 'busy'].includes(taskState)) return 'busy';
  if (['blocked', 'waiting', '等待输入'].includes(taskState)) return 'blocked';
  return 'idle';
};

const identityAccess = (identity) => {
  const groups = Array.isArray(identity?.groups) ? identity.groups : [];
  return {
    operator: groups.some((group) => operatorGroups.has(group)),
    subject: identity?.subject || identity?.sub || '',
    email: String(identity?.email || '').toLowerCase(),
  };
};

const getFleet = async (identity) => {
  const [deploymentList, podList, serviceList, metricsList] = await Promise.all([
    k8sGet('/apis/apps/v1/deployments?limit=500'),
    k8sGet('/api/v1/pods?limit=1000'),
    k8sGet('/api/v1/services?limit=1000'),
    k8sGet('/apis/metrics.k8s.io/v1beta1/pods?limit=1000').catch(() => ({items: []})),
  ]);
  const pods = podList.items || [];
  const services = serviceList.items || [];
  const metrics = new Map((metricsList.items || []).map((item) => [
    `${item.metadata.namespace}/${item.metadata.name}`, item,
  ]));

  const access = identityAccess(identity);
  const instances = (deploymentList.items || []).filter(isAppServer).map((deployment) => {
    const metadata = deployment.metadata || {};
    const namespace = metadata.namespace;
    const selector = deployment.spec?.selector?.matchLabels || {};
    const relatedPods = pods.filter((pod) =>
      pod.metadata?.namespace === namespace && labelsMatch(selector, pod.metadata?.labels || {}));
    const pod = relatedPods
      .filter((candidate) => !candidate.metadata?.deletionTimestamp)
      .sort((a, b) => String(b.metadata?.creationTimestamp).localeCompare(String(a.metadata?.creationTimestamp)))[0] || relatedPods[0];
    const conditions = pod?.status?.conditions || [];
    const ready = conditions.some((condition) => condition.type === 'Ready' && condition.status === 'True');
    const statuses = pod?.status?.containerStatuses || [];
    const restarts = statuses.reduce((sum, status) => sum + (status.restartCount || 0), 0);
    const annotations = {...(metadata.annotations || {}), ...(pod?.metadata?.annotations || {})};
    const podMetric = metrics.get(`${namespace}/${pod?.metadata?.name}`);
    const usage = (podMetric?.containers || []).reduce((total, container) => ({
      cpuMillicores: total.cpuMillicores + parseCpu(container.usage?.cpu),
      memoryMiB: total.memoryMiB + parseMemory(container.usage?.memory),
    }), {cpuMillicores: 0, memoryMiB: 0});
    const relatedService = services.find((service) =>
      service.metadata?.namespace === namespace &&
      Object.keys(service.spec?.selector || {}).length > 0 &&
      labelsMatch(service.spec.selector, pod?.metadata?.labels || {}));
    const taskState = annotations['codex.re8ch.com/task-state'] || '';
    const healthState = ready ? 'healthy' : pod?.status?.phase === 'Running' ? 'degraded' : 'offline';
    const activityState = stateFrom(ready, taskState, Boolean(pod?.metadata?.deletionTimestamp));
    const createdAt = pod?.metadata?.creationTimestamp || metadata.creationTimestamp;
    return {
      id: `${namespace}/${metadata.name}`,
      namespace,
      name: metadata.name,
      pod: pod?.metadata?.name || null,
      node: pod?.spec?.nodeName || deployment.spec?.template?.spec?.nodeSelector?.['kubernetes.io/hostname'] || null,
      image: deployment.spec?.template?.spec?.containers?.[0]?.image || null,
      replicas: {
        desired: deployment.spec?.replicas || 0,
        ready: deployment.status?.readyReplicas || 0,
        available: deployment.status?.availableReplicas || 0,
      },
      health: healthState,
      activity: activityState,
      phase: pod?.status?.phase || 'Pending',
      restarts,
      createdAt,
      owner: annotations['codex.re8ch.com/owner'] || annotations['re8ch.com/owner'] || '未标注',
      userId: annotations['codex.re8ch.com/user-id'] || null,
      task: {
        id: annotations['codex.re8ch.com/task-id'] || null,
        title: annotations['codex.re8ch.com/task-title'] || null,
        state: taskState || null,
        stage: annotations['codex.re8ch.com/task-stage'] || null,
        updatedAt: annotations['codex.re8ch.com/task-updated-at'] || null,
        threadId: annotations['codex.re8ch.com/thread-id'] || null,
      },
      usage: {
        cpuMillicores: Math.round(usage.cpuMillicores * 10) / 10,
        memoryMiB: Math.round(usage.memoryMiB * 10) / 10,
      },
      endpoint: relatedService ? {
        service: `${relatedService.metadata.name}.${namespace}.svc.cluster.local`,
        port: relatedService.spec?.ports?.[0]?.port || 4500,
      } : null,
      observedAt: new Date().toISOString(),
    };
  }).filter((instance) => access.operator ||
    (instance.userId && instance.userId === access.subject) ||
    (instance.owner && instance.owner.toLowerCase() === access.email)
  ).sort((a, b) => {
    const order = {busy: 0, blocked: 1, queued: 2, unhealthy: 3, idle: 4, terminating: 5};
    return (order[a.activity] ?? 9) - (order[b.activity] ?? 9) || a.id.localeCompare(b.id);
  });

  const summary = instances.reduce((result, instance) => {
    result.total += 1;
    result[instance.health] = (result[instance.health] || 0) + 1;
    result[instance.activity] = (result[instance.activity] || 0) + 1;
    result.cpuMillicores += instance.usage.cpuMillicores;
    result.memoryMiB += instance.usage.memoryMiB;
    return result;
  }, {total: 0, healthy: 0, degraded: 0, offline: 0, busy: 0, blocked: 0, queued: 0, idle: 0, cpuMillicores: 0, memoryMiB: 0});
  summary.cpuMillicores = Math.round(summary.cpuMillicores * 10) / 10;
  summary.memoryMiB = Math.round(summary.memoryMiB * 10) / 10;
  return {summary, instances, scope: access.operator ? 'operator' : 'self', observedAt: new Date().toISOString()};
};

const server = http.createServer(async (req, res) => {
  const requestUrl = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  if (requestUrl.pathname === '/healthz' || requestUrl.pathname === '/readyz') {
    return json(res, 200, {ok: true, namespace: ownNamespace, uptimeSeconds: Math.floor((Date.now() - startedAt) / 1000)});
  }
  if (requestUrl.pathname === '/api/auth') {
    const identity = await verifyIdentity(req);
    if (!identity) {
      const returnTo = `https://${req.headers['x-forwarded-host'] || req.headers.host}${appBasePath}/`;
      return json(res, 401, {
        authenticated: false,
        loginUrl: `${authBaseUrl}/start?service=${encodeURIComponent(authService)}&return_to=${encodeURIComponent(returnTo)}`,
      });
    }
    return json(res, 200, {
      authenticated: true,
      scope: identityAccess(identity).operator ? 'operator' : 'self',
      identity: {
        sub: identity.subject || identity.sub || null,
        email: identity.email || null,
        name: identity.name || identity.preferred_username || identity.email || 'RE8CH 用户',
      },
    });
  }
  if (requestUrl.pathname === '/api/instances') {
    const identity = await verifyIdentity(req);
    if (!identity) return json(res, 401, {error: 'unauthorized'});
    try {
      return json(res, 200, await getFleet(identity));
    } catch (error) {
      console.error('fleet discovery failed', error);
      return json(res, 502, {error: 'fleet_discovery_failed', detail: error.message});
    }
  }
  if (requestUrl.pathname === '/api/control/snapshot') {
    const identity = await verifyIdentity(req);
    if (!identity) return json(res, 401, {error: 'unauthorized'});
    if (!identityAccess(identity).operator) return json(res, 403, {error: 'operator_required'});
    const calls = {
      config: ['config/read', {includeLayers: true}],
      requirements: ['configRequirements/read', {}],
      models: ['model/list', {limit: 100, includeHidden: true}],
      features: ['experimentalFeature/list', {}],
      collaborationModes: ['collaborationMode/list', {}],
      skills: ['skills/list', {cwds: [codexCwd], forceReload: false}],
      hooks: ['hooks/list', {cwds: [codexCwd]}],
      mcp: ['mcpServerStatus/list', {limit: 100, detail: 'toolsAndAuthOnly'}],
      threads: ['thread/list', {limit: 50, sortKey: 'updated_at', sourceKinds: []}],
      loadedThreads: ['thread/loaded/list', {}],
      account: ['account/read', {refreshToken: false}],
      apps: ['app/list', {forceRefetch: false}],
    };
    const entries = await Promise.all(Object.entries(calls).map(async ([name, [method, params]]) => {
      try { return [name, {ok: true, value: await codexRpc(method, params)}]; }
      catch (error) { return [name, {ok: false, error: error.message}]; }
    }));
    return json(res, 200, {
      target: {host: codexHost, port: codexPort, cwd: codexCwd},
      capabilities: [...controlMethods].sort(),
      data: Object.fromEntries(entries),
      observedAt: new Date().toISOString(),
    });
  }
  if (requestUrl.pathname === '/api/control/rpc' && req.method === 'POST') {
    const identity = await verifyIdentity(req);
    if (!identity) return json(res, 401, {error: 'unauthorized'});
    if (!identityAccess(identity).operator) return json(res, 403, {error: 'operator_required'});
    try {
      const body = await readBody(req);
      if (!controlMethods.has(body.method)) return json(res, 400, {error: 'method_not_enabled'});
      return json(res, 200, {method: body.method, result: await codexRpc(body.method, body.params || {})});
    } catch (error) {
      return json(res, 502, {error: 'codex_rpc_failed', detail: error.message});
    }
  }
  if (requestUrl.pathname === '/' || requestUrl.pathname === '/index.html') {
    res.writeHead(200, {
      'content-type': 'text/html; charset=utf-8',
      'cache-control': 'no-store',
      'content-security-policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'self'",
      'x-content-type-options': 'nosniff',
      'referrer-policy': 'same-origin',
    });
    return res.end(indexHtml.replaceAll('__APP_BASE_PATH__', isTrustedInternal(req) ? '' : appBasePath));
  }
  json(res, 404, {error: 'not_found'});
});

server.listen(port, '0.0.0.0', () => {
  console.log(JSON.stringify({event: 'listening', port, appBasePath, ownNamespace}));
});
