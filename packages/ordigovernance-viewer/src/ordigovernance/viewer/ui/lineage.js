/* Lineage tree panel: latest-generation task tree, nested by parent.

Pure render from snapshot data; the tree structure is derived here
(byParent grouping + recursive walk), not pre-computed server-side.
*/

export function initLineage({ dom }) {
  const el = dom.get("tree");

  const renderTree = (snaps) => {
    const byParent = {};
    snaps.forEach((s) => {
      const key = s.parent_task_id ?? "null";
      (byParent[key] ??= []).push(s);
    });
    const lines = [];
    const walk = (pid, depth) =>
      (byParent[pid] || [])
        .sort((a, b) => a.task_id.localeCompare(b.task_id))
        .forEach((s) => {
          lines.push(
            `${"  ".repeat(depth)}${depth ? "+- " : ""}${s.task_id} ` +
            `[${s.status}]`
          );
          walk(s.task_id, depth + 1);
        });
    walk("null", 0);
    return lines.join("\n") || "<empty>";
  };

  const render = (snaps) => {
    el.textContent = renderTree(snaps);
  };

  return { render, renderTree };
}