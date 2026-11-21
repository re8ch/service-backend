import assert from "node:assert/strict";
import test from "node:test";
import {tick, STALE_TITLE, INCONCLUSIVE_TITLE} from "./index.mjs";

const NOW = Date.parse("2026-11-18T10:00:00Z");
const env = {GITHUB_TOKEN: "t"};

function fake(routes) {
  const calls = [];
  const impl = async (url, init) => {
    const path = url.replace("https://api.github.com", "");
    calls.push(`${init.method} ${path}`);
    for (const [pattern, reply] of routes) {
      if (`${init.method} ${path}`.startsWith(pattern)) {
        const value = typeof reply === "function" ? reply(init) : reply;
        if (value instanceof Response) return value;
        return value === null ? new Response(null, {status: 204}) : Response.json(value);
      }
    }
    throw new Error(`unexpected ${init.method} ${path}`);
  };
  return {impl, calls};
}

const run = (id, minutesAgo, conclusion = "success") => ({
  id, status: "completed", conclusion, html_url: `https://x/${id}`,
  updated_at: new Date(NOW - minutesAgo * 60_000).toISOString(),
});

test("fresh conclusive run: dispatches, no issue", async () => {
  const {impl, calls} = fake([
    ["POST /repos/re8ch/service-backend/actions/workflows/public-edge-authority.yml/dispatches", null],
    ["GET /repos/re8ch/service-backend/actions/workflows/", {workflow_runs: [run(1, 10)]}],
    ["GET /repos/re8ch/service-backend/actions/runs/1/jobs", {jobs: [{id: 11, conclusion: "success"}]}],
    ["GET /repos/re8ch/service-backend/check-runs/11/annotations", []],
    ["GET /search/issues", {items: []}],
  ]);
  const result = await tick(env, NOW, impl);
  assert.deepEqual(result, {dispatched: true, stale: false, issue: null, errors: []});
  assert.ok(!calls.some((c) => c.startsWith("POST /repos/re8ch/service-backend/issues")));
});

test("only inconclusive runs: opens one stale issue", async () => {
  const {impl} = fake([
    ["POST /repos/re8ch/service-backend/actions/workflows/", null],
    ["GET /repos/re8ch/service-backend/actions/workflows/", {workflow_runs: [run(1, 10)]}],
    ["GET /repos/re8ch/service-backend/actions/runs/1/jobs", {jobs: [{id: 11, conclusion: "success"}]}],
    ["GET /repos/re8ch/service-backend/check-runs/11/annotations", [{title: INCONCLUSIVE_TITLE}]],
    ["GET /search/issues", {items: []}],
    ["POST /repos/re8ch/service-backend/issues", (init) => {
      assert.equal(JSON.parse(init.body).title, STALE_TITLE);
      return {number: 42};
    }],
  ]);
  const result = await tick(env, NOW, impl);
  assert.equal(result.stale, true);
  assert.deepEqual(result.issue, {action: "opened", number: 42});
});

test("authority failure counts as conclusive", async () => {
  const {impl} = fake([
    ["POST /repos/re8ch/service-backend/actions/workflows/", null],
    ["GET /repos/re8ch/service-backend/actions/workflows/", {workflow_runs: [run(1, 5, "failure")]}],
    ["GET /repos/re8ch/service-backend/actions/runs/1/jobs", {jobs: [{id: 11, conclusion: "failure"}]}],
    ["GET /search/issues", {items: []}],
  ]);
  assert.equal((await tick(env, NOW, impl)).stale, false);
});

test("old runs are stale; existing issue is not duplicated", async () => {
  const {impl, calls} = fake([
    ["POST /repos/re8ch/service-backend/actions/workflows/", null],
    ["GET /repos/re8ch/service-backend/actions/workflows/", {workflow_runs: [run(1, 120)]}],
    ["GET /search/issues", {items: [{number: 7, title: STALE_TITLE}]}],
  ]);
  const result = await tick(env, NOW, impl);
  assert.equal(result.stale, true);
  assert.equal(result.issue, null);
  assert.ok(!calls.some((c) => c.includes("/jobs")));
});

test("fresh again: comments and closes the stale issue", async () => {
  const {impl, calls} = fake([
    ["POST /repos/re8ch/service-backend/actions/workflows/", null],
    ["GET /repos/re8ch/service-backend/actions/workflows/", {workflow_runs: [run(1, 3)]}],
    ["GET /repos/re8ch/service-backend/actions/runs/1/jobs", {jobs: [{id: 11, conclusion: "success"}]}],
    ["GET /repos/re8ch/service-backend/check-runs/11/annotations", []],
    ["GET /search/issues", {items: [{number: 7, title: STALE_TITLE}]}],
    ["POST /repos/re8ch/service-backend/issues/7/comments", {}],
    ["PATCH /repos/re8ch/service-backend/issues/7", {}],
  ]);
  const result = await tick(env, NOW, impl);
  assert.deepEqual(result.issue, {action: "closed", number: 7});
  assert.ok(calls.includes("PATCH /repos/re8ch/service-backend/issues/7"));
});

test("dispatch failure is reported but staleness still evaluated", async () => {
  const {impl} = fake([
    ["POST /repos/re8ch/service-backend/actions/workflows/", new Response("nope", {status: 403})],
    ["GET /repos/re8ch/service-backend/actions/workflows/", {workflow_runs: []}],
    ["GET /search/issues", {items: [{number: 7, title: STALE_TITLE}]}],
  ]);
  const result = await tick(env, NOW, impl);
  assert.equal(result.dispatched, false);
  assert.equal(result.stale, true);
  assert.match(result.errors[0], /403/);
});

test("missing token fails loudly", async () => {
  await assert.rejects(() => tick({}, NOW, async () => {}), /GITHUB_TOKEN/);
});
