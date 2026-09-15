/* Dependency graph panel (mermaid DAG).

Renders task dependency structure with status coloring and token
rollup per node. Clicking a node selects it for HITL actions (via the
injected callback). Token attribution uses purpose-prefix rules on
call ids — the same discipline the Python side enforces.
*/


/* Token attribution rules are injected via tokenCallers: the component
   carries no business call_id vocabulary. */

function tokenByTask(audit, tokenCallers) {
  const map = {};
  (audit || []).forEach((e) => {
    const u = (e.payload && e.payload.usage) || {};
    if (!u.total_tokens) return;
    const id = e.event_id || "";
    for (const [pattern, group] of tokenCallers) {
      const m = pattern.exec(id);
      if (m) {
        const owner = typeof group === "string" ? group : m[group];
        map[owner] = (map[owner] || 0) + u.total_tokens;
        break;
      }
    }
  });
  return map;
}

const STATUS_CLASS = {
  running: "stRunning", succeeded: "stSucceeded", failed: "stFailed",
  cancelled: "stCancelled", pending: "stPending",
};

const EDGE_STYLE = {
  succeeded: { arrow: "-->", cls: "edgeOk" },
  running:   { arrow: "-.->", cls: "edgeWait" },
  pending:   { arrow: "-.->", cls: "edgeWait" },
  failed:    { arrow: "-.->", cls: "edgeBad" },
  cancelled: { arrow: "-.->", cls: "edgeBad" },
};

export function initDependency({ dom, mermaid, onSelectNode, tokenCallers = [], formatNodeLabel = null }) {
  const el = dom.get("graph");
  if (!mermaid) {
    el.textContent =
      "Graph renderer unavailable (mermaid failed to load). " +
      "Status data continues to stream; refresh to retry.";
    return { render: async () => {} };
  }

  let renderInFlight = false;
  let renderSeq = 0;
  let pendingDef = null;

  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    flowchart: { curve: "basis" },
    securityLevel: "loose",
  });

  const render = async (graph, snaps, audit, options = {}) => {
    const status = {};
    (snaps || []).forEach((s) => { status[s.task_id] = s.status; });
    const tokens = tokenByTask(audit, tokenCallers);
    // Every node in the graph is a real executor-managed task with
    // snapshots; render the graph exactly as declared.
    const nodeIds = graph.task_ids || [];
    const edges = graph.edges || [];
    // Caller-injected highlight set (e.g. an impact closure): the
    // component applies a class, never interprets what the set means.
    const highlight = new Set(options.highlight || []);

    const safeToId = {};
    nodeIds.forEach((id) => {
      safeToId[id.replace(/[^a-zA-Z0-9_-]/g, "_")] = id;
    });
    const idsByLength = [...nodeIds].sort((a, b) => b.length - a.length);

    const lines = ["flowchart TD"];
    nodeIds.forEach((id) => {
      const safe = id.replace(/[^a-zA-Z0-9_-]/g, "_");
      const st = status[id] || "no snapshot";
      const tok = tokens[id];
      const display = formatNodeLabel ? formatNodeLabel(id) : id;
      // Quotes inside a label break the mermaid node syntax; escape.
      const label = (tok != null
        ? `${display}<br/>${st} · ${tok} tok`
        : `${display}<br/>${st}`).replace(/"/g, "#quot;");
      lines.push(`  ${safe}["${label}"]`);
    });

    const edgeClasses = [];
    edges.forEach((e, idx) => {
      const c = String(e.child_id).replace(/[^a-zA-Z0-9_-]/g, "_");
      const p = String(e.parent_id).replace(/[^a-zA-Z0-9_-]/g, "_");
      const parentStatus = status[e.parent_id] || "pending";
      const style = EDGE_STYLE[parentStatus] || EDGE_STYLE.pending;
      lines.push(`  ${p} ${style.arrow} ${c}`);
      edgeClasses.push({ idx, cls: style.cls });
    });

    edgeClasses.forEach(({ idx, cls }) => {
      const color = cls === "edgeOk" ? "#81c995"
        : cls === "edgeBad" ? "#f28b82" : "#9aa0a6";
      lines.push(`  linkStyle ${idx} stroke:${color},stroke-width:2px`);
    });

    Object.entries(STATUS_CLASS).forEach(([st, cls]) => {
      const ids = nodeIds
        .filter((id) => status[id] === st)
        .map((id) => id.replace(/[^a-zA-Z0-9_-]/g, "_"));
      if (ids.length) lines.push(`  class ${ids.join(",")} ${cls}`);
    });
    lines.push("  classDef stRunning fill:#3b6ea5,stroke:#8ab4f8,color:#fff");
    lines.push("  classDef stSucceeded fill:#1e4620,stroke:#81c995,color:#fff");
    lines.push("  classDef stFailed fill:#5f2120,stroke:#f28b82,color:#fff");
    lines.push("  classDef stCancelled fill:#4e3a1e,stroke:#fdd663,color:#fff");
    lines.push("  classDef stPending fill:#2a2d34,stroke:#9aa0a6,color:#d7dce2");
    if (highlight.size) {
      const ids = nodeIds
        .filter((id) => highlight.has(id))
        .map((id) => id.replace(/[^a-zA-Z0-9_-]/g, "_"));
      if (ids.length) lines.push(`  class ${ids.join(",")} stHighlight`);
      lines.push(
        "  classDef stHighlight fill:#4a3a6a,stroke:#c4a5f8," +
        "stroke-width:3px,color:#fff");
    }

    const resolveNodeId = (nodeEl) => {
      const dataId = nodeEl.getAttribute("data-id");
      if (dataId && safeToId[dataId] !== undefined) return safeToId[dataId];
      let candidate = (nodeEl.id || "").replace(/^.*flowchart-/, "");
      for (let i = 0; i < 3 && candidate; i++) {
        if (safeToId[candidate] !== undefined) return safeToId[candidate];
        const m = /^(.*)-\d+$/.exec(candidate);
        if (!m) break;
        candidate = m[1];
      }
      const text = nodeEl.textContent || "";
      for (const id of idsByLength) {
        if (text.startsWith(id)) return id;
      }
      return null;
    };

    const paint = async (def) => {
      try {
        const { svg } = await mermaid.render(`graphSvg-${renderSeq++}`, def);
        el.innerHTML = svg;
        el.querySelectorAll(".node").forEach((nodeEl) => {
          const id = resolveNodeId(nodeEl);
          if (!id) return;
          nodeEl.style.cursor = "pointer";
          nodeEl.addEventListener("click", () => onSelectNode(id));
        });
      } catch (err) {
        el.textContent = def;
      }
    };

    const def = lines.join("\n");
    if (renderInFlight) {
      // Coalesce to the LATEST request: intermediate frames are
      // superseded and never need to render, but the newest state
      // must not be dropped (a dropped frame leaves the DAG one poll
      // behind until the next signature change).
      pendingDef = def;
      return;
    }
    renderInFlight = true;
    try {
      let current = def;
      while (current !== null) {
        await paint(current);
        current = pendingDef;
        pendingDef = null;
      }
    } finally {
      renderInFlight = false;
    }
  };
  return { render };
}