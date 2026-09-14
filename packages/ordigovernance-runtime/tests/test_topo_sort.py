import pytest

from ordigovernance.runtime.replay.topo_sort import subgraph_between, topo_order


def edge(child, parent):
    return {"child_id": child, "parent_id": parent, "is_primary": True}


LINEAR = [edge("b", "a"), edge("c", "b"), edge("d", "c")]
DIAMOND = [edge("b", "a"), edge("c", "a"), edge("d", "b"), edge("d", "c")]


def test_linear_interval_order():
    assert topo_order("a", "d", LINEAR) == ["a", "b", "c", "d"]


def test_diamond_interval_order():
    order = topo_order("a", "d", DIAMOND)
    assert order[0] == "a"
    assert order[-1] == "d"
    assert set(order[1:3]) == {"b", "c"}


def test_external_dependencies_are_excluded():
    edges = LINEAR + [edge("c", "external"), edge("external", "elsewhere")]
    included = subgraph_between("a", "d", edges)
    assert "external" not in included
    assert "elsewhere" not in included
    assert topo_order("a", "d", edges) == ["a", "b", "c", "d"]


def test_downstream_of_end_is_excluded():
    edges = LINEAR + [edge("e", "d")]
    assert topo_order("a", "d", edges) == ["a", "b", "c", "d"]


def test_single_node_interval():
    assert topo_order("a", "a", LINEAR) == ["a"]


def test_disconnected_interval_raises():
    edges = [edge("x", "a"), edge("y", "b")]
    with pytest.raises(ValueError):
        topo_order("a", "y", edges)