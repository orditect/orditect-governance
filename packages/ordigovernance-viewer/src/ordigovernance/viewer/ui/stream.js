/* Publish stream panel: stage-aware SSE rendering.

Thinking stages render dim/italic, the report stage renders green.

Image discipline:
  The report text's ![img] tokens are consumed by the runner inside
  the stream (the final content carries no token), so anchors cannot
  be derived from text. The enrich.placeholder event IS the anchor:
  it arrives when the stream reaches the image's position. Reserving
  a container at the current stream tail at that moment pins the
  image to its semantic position. enrich.resolved fills the reserved
  container in place — position never moves, content swaps at most
  once (loading -> resolved).
*/

export function initStream({ dom, log }) {
  const stream = dom.get("publishStream");
  let currentStage = "";
  const byPhId = new Map();        // placeholder_id -> anchor container

  const clear = () => {
    stream.innerHTML = "";
    currentStage = "";
    byPhId.clear();
  };

  const appendText = (stage, text) => {
    const cls = stage === "thinking" ? "thinking" : "report";
    let el = stream.lastElementChild;
    if (!el || !el.classList.contains(cls)
        || el.classList.contains("img-anchor")) {
      el = document.createElement("div");
      el.className = cls;
      stream.appendChild(el);
    }
    el.textContent += text;
    stream.scrollTop = 1e9;
  };

    const reserveAnchor = (phId) => {
    const anchor = document.createElement("div");
    anchor.className = "img-anchor";
    stream.appendChild(anchor);
    if (phId) byPhId.set(phId, anchor);
    stream.scrollTop = 1e9;
  };

  const resolveImage = (phId, url) => {
    if (!url) return;
    const anchor = byPhId.get(phId);
    const img = document.createElement("img");
    img.src = url;
    img.alt = "enriched image";
    if (anchor) {
      anchor.innerHTML = "";          // swap loading -> final in place
      anchor.appendChild(img);
    } else {
      stream.appendChild(img);        // no placeholder seen: tail
    }
    stream.scrollTop = 1e9;
  };

    const handleEvent = (ev) => {
    const d = ev.data || {};
    if (ev.event === "stream.start") {
      currentStage = "";
    } else if (ev.event === "stage.end") {
      currentStage = "";
      log(`[stream] stage.end ${d.name}`);
        } else if (ev.event === "stream.delta") {
      // thinking deltas render dim/italic; content renders as report.
      const kind = d.kind || "content";
      const stage = kind === "thinking"
        ? "thinking"
        : (d.stage || currentStage || "report");
      appendText(stage, d.text || "");
    } else if (ev.event === "enrich.placeholder") {
      reserveAnchor(d.placeholder_id);
      log(`[stream] enrich.placeholder ${d.placeholder_id}`);
    } else if (ev.event === "enrich.resolved") {
      resolveImage(d.placeholder_id, d.url);
      log(`[stream] enrich.resolved ${d.placeholder_id}`);
    } else if (ev.event === "enrich.expired") {
      const anchor = byPhId.get(d.placeholder_id);
      if (anchor) {
        anchor.classList.add("img-expired");
        anchor.textContent = "[image not produced]";
      }
      log(`[stream] enrich.expired ${d.placeholder_id}`);
    } else if (ev.event !== "stream.delta") {
      log(`[stream] ${ev.event} #${ev.seq}`);
    }
  };

  return { clear, handleEvent };
}