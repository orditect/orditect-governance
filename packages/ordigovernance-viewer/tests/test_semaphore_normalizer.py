"""Semaphore status normalization (acceptance)."""

from ordigovernance.viewer.semaphore_normalizer import (
    format_water_line,
    normalize_sems,
)


def test_normalize_mapping_shape():
    raw = {"llm": {"limit": 2, "usage": 1, "utilization": "50.0%"}}
    out = normalize_sems(raw)
    assert out == [{"name": "llm", "limit": 2, "usage": 1,
                    "utilization": "50.0%"}]


def test_normalize_list_shape_and_drop_garbage():
    raw = [{"name": "llm", "limit": 2}, "junk", {"no_name": True}]
    out = normalize_sems(raw)
    assert out == [{"name": "llm", "limit": 2}, {"no_name": True}]


def test_format_line_defensive_fields():
    line = format_water_line(
        [{"name": "llm", "limit": 2, "usage": 1, "utilization": "50.0%"},
         {"name": "db", "limit": 4, "available": 3},
         {"name": "mystery"}],
        budget_balance=42,
    )
    assert "llm: 1/2 (50.0%)" in line
    assert "db: 1/4 (25.0%)" in line
    assert "mystery: ?/? (?)" in line
    assert line.endswith("budget: 42")