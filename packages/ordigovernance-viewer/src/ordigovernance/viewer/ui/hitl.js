/* HITL actions: pause / resume / retry against the active run.

Actions are queue-shaped and execute asynchronously; each call returns
the acceptance receipt and waitReceipt polls for the execution receipt.
*/

export function initHitl({ api, log }) {
  const waitReceipt = async (actionId, timeoutMs = 6000) => {
    const deadline = Date.now() + timeoutMs;
    let runEnded = false;
    while (Date.now() < deadline) {
      try {
        const receipt = await api.hitlReceipt(actionId);
        if (receipt) return receipt;
      } catch { /* 404 while pending */ }
      if (runEnded) return null;
      try {
        const s = await api.activeStatus();
        if (s.status && s.status !== "running") runEnded = true;
      } catch { /* keep polling until the deadline */ }
      await new Promise((res) => setTimeout(res, 400));
    }
    return null;
  };

  const pause = async (nodeId) => {
    const r = await api.hitlPause(nodeId);
    const d = await r.json();
    if (!r.ok) {
      log(`[hitl] pause ${nodeId} rejected: ${d.detail}`);
      return null;
    }
    log(`[hitl] pause ${nodeId} accepted: ${d.action_id}`);
    const rc = await waitReceipt(d.action_id);
    log(rc
      ? `[hitl] pause ${nodeId} ${rc.status}: ${rc.detail || ""}`
      : `[hitl] pause ${nodeId} receipt timeout`);
    return rc;
  };

  const resume = async (rootId) => {
    const r = await api.hitlResume(rootId);
    const d = await r.json();
    if (!r.ok) {
      log(`[hitl] resume rejected: ${d.detail}`);
      return null;
    }
    log(`[hitl] resume tree accepted: ${d.action_id}`);
    const rc = await waitReceipt(d.action_id);
    log(rc
      ? `[hitl] resume tree ${rc.status}: ${rc.detail || ""}`
      : `[hitl] resume tree receipt timeout`);
    return rc;
  };

  const retry = async (nodeId) => {
    const r = await api.hitlRetry(nodeId);
    const d = await r.json();
    if (!r.ok) {
      log(`[hitl] retry ${nodeId} rejected: ${d.detail}`);
      return null;
    }
    log(`[hitl] retry ${nodeId} accepted: ${d.action_id}`);
    const rc = await waitReceipt(d.action_id);
    log(rc
      ? `[hitl] retry ${nodeId} ${rc.status}: ${rc.detail || ""}`
      : `[hitl] retry ${nodeId} receipt timeout`);
    return rc;
  };

  return { pause, resume, retry, waitReceipt };
}