// PublicEdge dispatcher: a Cloudflare Worker Cron Trigger.
//
// GitHub `schedule` drops most runs of high-frequency crons, and the r640
// runner cannot be relied on to reach GitHub through its mainland egress.
// This Worker runs outside both: every 15 minutes it dispatches the
// PublicEdge authority workflow and checks that a conclusive observation
// exists within STALE_AFTER_MINUTES. When none exists it opens one issue;
// when observations are fresh again it closes that issue.

const API = "https://api.github.com";
export const STALE_TITLE = "[PublicEdge] authority observation stale";
export const INCONCLUSIVE_TITLE = "PublicEdge inconclusive";

function settings(env) {
  return {
    repo: env.GITHUB_REPOSITORY || "re8ch/service-backend",
    workflow: env.WORKFLOW_FILE || "public-edge-authority.yml",
    ref: env.WORKFLOW_REF || "main",
    staleMinutes: Number(env.STALE_AFTER_MINUTES || 60),
  };
}

export function client(env, fetchImpl = fetch) {
  if (!env.GITHUB_TOKEN) throw new Error("GITHUB_TOKEN secret is not configured");
  return async (method, path, body) => {
    const response = await fetchImpl(API + path, {
      method,
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        "user-agent": "re8ch-public-edge-dispatcher",
        ...(body ? {"content-type": "application/json"} : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      throw new Error(`${method} ${path} -> ${response.status}: ${(await response.text()).slice(0, 300)}`);
    }
    return response.status === 204 ? null : response.json();
  };
}

// A run is conclusive when it completed and at least one vantage produced an
// authority verdict, i.e. its job did not report "PublicEdge inconclusive".
async function conclusive(gh, repo, run) {
  if (run.status !== "completed" || !["success", "failure"].includes(run.conclusion)) return false;
  const {jobs} = await gh("GET", `/repos/${repo}/actions/runs/${run.id}/jobs?per_page=10`);
  for (const job of jobs) {
    if (job.conclusion === "failure") return true;  // an authority failure is a verdict
    if (job.conclusion !== "success") continue;
    const annotations = await gh("GET", `/repos/${repo}/check-runs/${job.id}/annotations?per_page=50`);
    if (!annotations.some((a) => a.title === INCONCLUSIVE_TITLE)) return true;
  }
  return false;
}

export async function newestConclusive(gh, repo, workflow, now, staleMinutes) {
  const since = new Date(now - staleMinutes * 60_000);
  const {workflow_runs: runs} = await gh(
    "GET", `/repos/${repo}/actions/workflows/${workflow}/runs?per_page=10&status=completed`);
  for (const run of runs) {
    if (new Date(run.updated_at) < since) break;
    if (await conclusive(gh, repo, run)) return run;
  }
  return null;
}

async function openStaleIssue(gh, repo) {
  const q = encodeURIComponent(`repo:${repo} is:issue is:open in:title "${STALE_TITLE}"`);
  const {items} = await gh("GET", `/search/issues?q=${q}&per_page=5`);
  return items.find((item) => item.title === STALE_TITLE) || null;
}

export async function tick(env, now = Date.now(), fetchImpl = fetch) {
  const {repo, workflow, ref, staleMinutes} = settings(env);
  const gh = client(env, fetchImpl);
  const result = {dispatched: false, stale: null, issue: null, errors: []};

  try {
    await gh("POST", `/repos/${repo}/actions/workflows/${workflow}/dispatches`, {ref});
    result.dispatched = true;
  } catch (error) {
    result.errors.push(`dispatch: ${error.message}`);
  }

  const fresh = await newestConclusive(gh, repo, workflow, now, staleMinutes);
  result.stale = !fresh;
  const issue = await openStaleIssue(gh, repo);
  if (!fresh && !issue) {
    const created = await gh("POST", `/repos/${repo}/issues`, {
      title: STALE_TITLE,
      body: [
        `No conclusive PublicEdge authority observation in the last ${staleMinutes} minutes.`,
        "",
        `Workflow: https://github.com/${repo}/actions/workflows/${workflow}`,
        `Dispatch this tick: ${result.dispatched ? "ok" : result.errors.join("; ")}`,
        "",
        "Opened by the PublicEdge dispatcher Worker. It closes this issue when a conclusive observation appears.",
      ].join("\n"),
    });
    result.issue = {action: "opened", number: created.number};
  } else if (fresh && issue) {
    await gh("POST", `/repos/${repo}/issues/${issue.number}/comments`, {
      body: `Conclusive observation is fresh again: ${fresh.html_url}`,
    });
    await gh("PATCH", `/repos/${repo}/issues/${issue.number}`, {state: "closed", state_reason: "completed"});
    result.issue = {action: "closed", number: issue.number};
  }
  return result;
}

export default {
  async scheduled(event, env, ctx) {
    const result = await tick(env, event.scheduledTime);
    console.log(JSON.stringify(result));
    if (result.errors.length) throw new Error(result.errors.join("; "));
  },
};
