/* Version comparison: two runs side by side from cold-path data.

Governed non-LLM call event types (business-owned vocabulary) are
injected so the component stays business-neutral; the summary groups
calls by type and keeps call ids for line-by-line diffing.
*/

export function initCompare({ api, dom, renderTree, governedTypes, rootId, workerPrefix = null, workerLabel = "researchers" }) {
  const overlay = dom.get("compareOverlay");
  const title = dom.get("compareTitle");
  const body = dom.get("compareBody");

  const open = async (runA, runB) => {
    overlay.classList.remove("hidden");
    title.textContent = `${runA}  vs  ${runB}`;
    body.innerHTML = "loading...";
    try {
      const [da, db] = await Promise.all([
        fetchRunData(runA), fetchRunData(runB),
      ]);
      body.innerHTML = "";
      for (const [runId, d] of [[runA, da], [runB, db]]) {
        body.appendChild(buildColumn(runId, d));
      }
    } catch (err) {
      body.textContent = `compare failed: ${err}`;
    }
  };

  const close = () => overlay.classList.add("hidden");

  async function fetchRunData(runId) {
    const [tree, gens, audit, graph, results, validate] =
      await Promise.all([
        api.getTree(runId, rootId),
        api.getGenerations(runId, rootId),
        api.getAudit(runId),
        api.getGraph(runId, rootId),
        api.getResults(runId),
        api.validate(runId),
      ]);
    return { tree, gens, audit, graph, results, validate };
  }

    function summarize(d) {
    const nodeCount = (d.graph.task_ids || []).length;
    // Worker tally is opt-in: the id prefix is business vocabulary,
    // injected by the caller (docs/pitfalls.md 13.18).
    const workerCount = workerPrefix
      ? (d.graph.task_ids || [])
          .filter((t) => t.startsWith(workerPrefix)).length
      : null;
    let totalTokens = 0, totalCost = 0;
    const callsByType = {};
    governedTypes.forEach((t) => { callsByType[t] = []; });
    let memoHits = 0, memoPuts = 0, archiveOps = 0;

    (Array.isArray(d.audit) ? d.audit : []).forEach((e) => {
      const u = (e.payload && e.payload.usage) || {};
      if (u.total_tokens) totalTokens += u.total_tokens;
      if ((e.payload || {}).cost_units) totalCost += e.payload.cost_units;
      if (governedTypes.includes(e.event_type)) {
        callsByType[e.event_type].push(e.event_id);
      }
      const eid = e.event_id || "";
      if (/^memget-/.test(eid)) memoHits++;
      else if (/^memput-/.test(eid)) memoPuts++;
      else if (/^mem(save|load)-/.test(eid)) archiveOps++;
    });

    const governedTotal = governedTypes
      .reduce((n, t) => n + callsByType[t].length, 0);
    return [
      `nodes: ${nodeCount}` +
        (workerCount != null ? `  ${workerLabel}: ${workerCount}` : ""),
      `tokens: ${totalTokens}  cost_units: ${totalCost}`,
      `governed tool/memory/vector calls: ${governedTotal}`,
      `content layer: memo hits=${memoHits} puts=${memoPuts} ` +
        `archive ops=${archiveOps}`,
      ...governedTypes.map((t) =>
        `  ${t}: ${callsByType[t].length}` +
        (callsByType[t].length
          ? `\n    ${callsByType[t].join("\n    ")}`
          : "")),
    ].join("\n");
  }

  function buildColumn(runId, d) {
    const col = document.createElement("div");
    col.className = "compare-col";

    const summary = document.createElement("pre");
    summary.textContent = summarize(d);

    const tree = document.createElement("pre");
    tree.textContent = renderTree(
      Array.isArray(d.tree) ? d.tree : []);

    const gens = document.createElement("pre");
    gens.textContent = (Array.isArray(d.gens) ? d.gens : [])
      .map((s) => `${s.task_id} ${s.execution_id}:${s.status}`)

      .join("\n");

    const val = document.createElement("pre");
    val.textContent = d.validate && d.validate.summary
      ? `${d.validate.ok ? "PASS" : "FAIL"} — ${d.validate.summary}`
      : "no bundle";

    col.innerHTML = `<h3>${runId}</h3>`;
    col.appendChild(summary);
    col.insertAdjacentHTML("beforeend", "<h3>lineage</h3>");
    col.appendChild(tree);
    col.insertAdjacentHTML("beforeend", "<h3>generations</h3>");
    col.appendChild(gens);
    col.insertAdjacentHTML("beforeend", "<h3>run_rules</h3>");
    col.appendChild(val);
    return col;
  }

  return { open, close };
}