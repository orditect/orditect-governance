/* Audit panel: governed-call event log with usage/cost/elapsed.

Renders the append-only audit stream; token totals are aggregated for
the header line.
*/

export function initAudit({ dom }) {
  const el = dom.get("audit");

  const render = (events) => {
    if (!Array.isArray(events) || !events.length) {
      el.textContent = "<empty>";
      return;
    }
    const lines = events.map((e) => {
      const u = (e.payload && e.payload.usage) || {};
      const p = e.payload || {};
      const extra = u.total_tokens
        ? `tokens=${u.total_tokens} elapsed=${p.elapsed_ms}ms ` +
          `cost=${p.cost_units}`
        : "";
      return `[${e.event_type}] id=${e.event_id} ${extra}`;
    });
    el.textContent = lines.join("\n");
  };

  return { render };
}