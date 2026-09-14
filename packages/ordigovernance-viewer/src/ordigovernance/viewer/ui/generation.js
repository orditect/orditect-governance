/* Generation panel: time-travel view over every execution generation.

Each node line lists its generations as clickable chips; clicking one
opens a content panel with the archived result + lineage pins. The
panel content is cached so the 2s poll can restore it without
re-fetching or flickering. Replay buttons are injected per node type
via the callbacks map (the component does not know what "replay" does).

Pin discipline: when the caller injects onPinClick(taskId, eid), pins
render as clickable chips and the click is delegated to the caller
(the component does not know what "walking the lineage" means); without
the callback pins degrade to plain text (backwards compatible).
*/

export function initGeneration({ api, dom, getRunId, onReplay, onPinClick = null }) {
  const el = dom.get("generations");

  const _cache = {};
  let _lastSig = "";

  const _renderPins = (container, pins) => {
    if (!pins || typeof pins !== "object") return;
    const wrap = document.createElement("div");
    wrap.textContent = "pins: ";
    Object.entries(pins).forEach(([taskId, eid]) => {
      const short = `...${String(eid).slice(-4)}`;
      if (onPinClick) {
        const chip = document.createElement("a");
        chip.textContent = `${taskId}@${short}`;
        chip.style.cssText =
          "color:#8ab4f8;cursor:pointer;margin-left:6px;";
        chip.onclick = () => onPinClick(taskId, eid);
        wrap.appendChild(chip);
      } else {
        const text = document.createElement("span");
        text.textContent = `${taskId}@${short}`;
        text.style.marginLeft = "6px";
        wrap.appendChild(text);
      }
    });
    container.appendChild(wrap);
  };

  const _renderBody = (panel, taskId, eid) => {
    const key = `${taskId}|${eid}`;
    panel.textContent = _cache[key] || "(content unchanged)";
    const pins = _cache[`${key}|pins`];
    if (pins) _renderPins(panel, pins);
  };

  const _restorePanel = (taskId, eid, afterEl) => {
    const panel = document.createElement("pre");
    panel.className = "gen-content";
    panel.dataset.taskId = taskId;
    panel.dataset.eid = eid;
    panel.style.cssText =
      "background:#0d0f13;margin:4px 0 4px 16px;max-height:200px;";
    _renderBody(panel, taskId, eid);
    afterEl.after(panel);
  };

  const showContent = async (taskId, eid, afterEl) => {
    if (afterEl.nextElementSibling?.classList.contains("gen-content")) {
      afterEl.nextElementSibling.remove();
      return;
    }
    const key = `${taskId}|${eid}`;
    const panel = document.createElement("pre");
    panel.className = "gen-content";
    panel.dataset.taskId = taskId;
    panel.dataset.eid = eid;
    panel.style.cssText =
      "background:#0d0f13;margin:4px 0 4px 16px;max-height:200px;";
    panel.textContent = "loading...";
    afterEl.after(panel);
    try {
      const d = await api.getGenerationContent(getRunId(), taskId, eid);
      _cache[key] = JSON.stringify(d.result ?? d, null, 2).slice(0, 4000);
      if (d.input_pins && Object.keys(d.input_pins).length) {
        _cache[`${key}|pins`] = d.input_pins;
      } else {
        delete _cache[`${key}|pins`];
      }
      _renderBody(panel, taskId, eid);
    } catch (err) {
      panel.textContent = `no archive for this generation (${err})`;
    }
  };

  const render = (snaps) => {
    const byTask = {};
    (snaps || []).forEach((s) => (byTask[s.task_id] ??= []).push(s));

    const sig = JSON.stringify(
      Object.keys(byTask).sort().map((t) => [
        t, byTask[t].map((s) => `${s.execution_id}:${s.status}`),
      ])
    );
    if (sig === _lastSig) return;
    _lastSig = sig;

    const openPanel = el.querySelector(".gen-content");
    const openKey = openPanel
      ? `${openPanel.dataset.taskId}|${openPanel.dataset.eid}`
      : null;

    el.innerHTML = "";
    Object.keys(byTask).sort().forEach((t) => {
      const line = document.createElement("div");
      line.textContent = t;
      byTask[t].forEach((s) => {
        const chip = document.createElement("a");
        chip.textContent = ` ${s.execution_id.slice(0, 12)}:${s.status}`;
        chip.style.cssText =
          "color:#8ab4f8;cursor:pointer;margin-left:8px;";
        chip.onclick = () => showContent(t, s.execution_id, line);
        line.appendChild(chip);

        if (s.status === "succeeded" && onReplay) {
          const btn = onReplay(t, s.execution_id);
          if (btn) line.appendChild(btn);
        }
      });
      el.appendChild(line);
      if (openKey && openKey.startsWith(`${t}|`)) {
        const eid = openKey.split("|")[1];
        _restorePanel(t, eid, line);
      }
    });
  };

  return { render };
}