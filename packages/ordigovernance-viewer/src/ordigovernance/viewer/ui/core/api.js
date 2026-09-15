/* Cold-path API client (fetch wrapper).

All endpoints are parameterized by run_id; the client knows nothing
about run lifecycle or business semantics. Base URL is injected so the
same client works against any deployment root. Showcase/forest/demo
endpoints are app-private and live in the consuming application's own
copy of this client, never in the shared viewer package.
*/

export function initApi({ baseUrl = "", fetchImpl = fetch } = {}) {
  const json = async (path) => {
    const r = await fetchImpl(`${baseUrl}${path}`);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  };

  return {
    listRuns: () => json("/api/runs"),
    getRun: (runId) => json(`/api/runs/${runId}`),
    activeStatus: () => json("/api/runs/active/status"),
    getTree: (runId, rootId) =>
      json(`/api/runs/${runId}/tree?root_id=${encodeURIComponent(rootId)}`),
    getGenerations: (runId, rootId) =>
      json(`/api/runs/${runId}/generations?root_id=${encodeURIComponent(rootId)}`),
    getGraph: (runId, rootId) =>
      json(`/api/runs/${runId}/graph?root_id=${encodeURIComponent(rootId)}`),
    getAudit: (runId, taskId = null) =>
      json(`/api/runs/${runId}/audit` +
           (taskId ? `?task_id=${encodeURIComponent(taskId)}` : "")),
    getStats: (runId) => json(`/api/runs/${runId}/stats`),
    getResults: (runId) => json(`/api/runs/${runId}/results`),
    validate: (runId, rootId = null) =>
      json(`/api/runs/${runId}/validate` +
           (rootId ? `?root_id=${encodeURIComponent(rootId)}` : "")),
    getGenerationContent: (runId, taskId, eid) =>
      json(`/api/runs/${runId}/generations/` +
           `${encodeURIComponent(taskId)}/${encodeURIComponent(eid)}/content`),
    startRun: (intent, params = {}) =>
      fetchImpl(`${baseUrl}/api/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent, params }),
      }),
    hitlPause: (nodeId) =>
      fetchImpl(`${baseUrl}/api/hitl/pause/${encodeURIComponent(nodeId)}`,
                { method: "POST" }),
    hitlResume: (rootId) =>
      fetchImpl(`${baseUrl}/api/hitl/resume/${encodeURIComponent(rootId)}`,
                { method: "POST" }),
    hitlRetry: (nodeId) =>
      fetchImpl(`${baseUrl}/api/hitl/retry/${encodeURIComponent(nodeId)}`,
                { method: "POST" }),
    hitlReceipt: (actionId) =>
      json(`/api/hitl/receipt/${encodeURIComponent(actionId)}`),
    getConfig: () => json("/api/config"),
  };
}