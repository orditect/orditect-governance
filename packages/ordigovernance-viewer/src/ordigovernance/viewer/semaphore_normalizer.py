"""Semaphore status normalization for the watermark display.

Migrated from the reference application's water display module. The
registry's return shape is not pinned by the public docs: some
implementations return a list of status dicts, others a {name: status}
mapping whose values may omit the "name" field. Accept both; drop
anything that is not a status dict.

Hard discipline: usage figures are non-atomic approximations — display
only, never alert on them.
"""

from __future__ import annotations


def normalize_sems(raw) -> list[dict]:
    """Normalize get_all_semaphore_status() output to a list of dicts."""
    if isinstance(raw, dict):
        out: list[dict] = []
        for name, status in raw.items():
            if isinstance(status, dict):
                entry = dict(status)
                entry.setdefault("name", name)
                out.append(entry)
        return out
    return [s for s in raw if isinstance(s, dict)]


def format_water_line(sems: list[dict], budget_balance) -> str:
    """One-line snapshot of all semaphore levels plus the budget level.

    Field access is defensive: the documented five-field format is
    name / limit / usage / available / utilization; a missing field
    degrades to "?" instead of crashing the display.
    """
    cells: list[str] = []
    for s in sems:
        name = s.get("name", "?")
        limit = s.get("limit", "?")
        usage = s.get("usage", s.get("in_use"))
        if usage is None:
            available = s.get("available")
            if isinstance(limit, int) and isinstance(available, int):
                usage = limit - available
            else:
                usage = "?"
        util = s.get("utilization")
        if (util is None and isinstance(limit, int) and limit
                and isinstance(usage, int)):
            util = f"{usage / limit:.1%}"
        cells.append(f"{name}: {usage}/{limit} ({util or '?'})")
    return f"[water] {'  '.join(cells)}  |  budget: {budget_balance}"