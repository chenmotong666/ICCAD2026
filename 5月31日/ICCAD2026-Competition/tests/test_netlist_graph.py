from eda.netlist_graph import NetlistGraph


def test_manual_graph_depth_and_fanout():
    graph = NetlistGraph()
    graph.module_name = "toy"
    graph.primary_inputs = {"a": "PI:a", "b": "PI:b"}
    graph.primary_outputs = {"y": "u2"}

    graph.G.add_node("PI:a", ntype="pi", output_wire="a", is_po=False)
    graph.G.add_node("PI:b", ntype="pi", output_wire="b", is_po=False)
    graph.G.add_node(
        "u1",
        ntype="cell",
        gate_type="$and",
        output_wire="n1",
        input_wires=["a", "b"],
        is_po=False,
    )
    graph.G.add_node(
        "u2",
        ntype="cell",
        gate_type="$not",
        output_wire="y",
        input_wires=["n1"],
        is_po=True,
    )

    graph.G.add_edge("PI:a", "u1", wire="a")
    graph.G.add_edge("PI:b", "u1", wire="b")
    graph.G.add_edge("u1", "u2", wire="n1")

    graph.wire_driver = {"a": "PI:a", "b": "PI:b", "n1": "u1", "y": "u2"}
    graph.wire_readers = {"a": ["u1"], "b": ["u1"], "n1": ["u2"], "y": []}

    depth, path = graph.get_max_depth("a", "y")

    assert depth == 2
    assert path[0] == "a"
    assert graph.get_fanout("n1") == 1
