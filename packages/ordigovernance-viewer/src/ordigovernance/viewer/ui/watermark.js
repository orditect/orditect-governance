/* Watermark panel: semaphore usage plus budget balance (SSE-driven).

Hard discipline: usage figures are non-atomic approximations — display
only, never alert on them.
*/

export function initWatermark({ sse, dom }) {
  const el = dom.get("water");

  sse.onMessage((d) => {
    if (d.error) {
      el.textContent = "error: " + d.error;
      return;
    }
    const cells = Object.entries(d.semaphores || {})
      .map(([name, s]) => `${name}: ${s.usage}/${s.limit} (${s.utilization})`)
      .join("  ");
    el.textContent = `${cells}\nbudget: ${d.budget ?? "n/a"}`;
  });

  sse.onError(() => {
    el.textContent = "connection lost (retrying...)";
  });

  return {};
}