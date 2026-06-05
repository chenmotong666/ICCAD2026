"""
Unit tests for graph transformations (no Yosys/LLM required).

Each test builds a small NetlistGraph manually, runs a transformer,
and verifies the graph structure is correct. Tests match current
transformer behavior to catch regressions.
"""
from eda.netlist_graph import NetlistGraph, CONST_0, CONST_1
from eda.transformer import NetlistTransformer


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_toy_graph() -> NetlistGraph:
    """a → AND → n1 → NOT → y   (plus dangling: c → d_buf → unused)"""
    g = NetlistGraph()
    g.module_name = "toy"
    g.primary_inputs = {"a": "PI:a", "c": "PI:c"}
    g.primary_outputs = {"y": "u_not"}

    g.G.add_node("PI:a", ntype="pi", output_wire="a", is_po=False)
    g.G.add_node("PI:c", ntype="pi", output_wire="c", is_po=False)
    g.G.add_node(CONST_0, ntype="const", output_wire="1'b0", is_po=False)
    g.G.add_node(CONST_1, ntype="const", output_wire="1'b1", is_po=False)

    # functional path
    g.G.add_node("u_and", ntype="cell", gate_type="$and",
                 output_wire="n1", input_wires=["a", "1'b1"], is_po=False)
    g.G.add_node("u_not", ntype="cell", gate_type="$not",
                 output_wire="y", input_wires=["n1"], is_po=True)

    # dangling gate
    g.G.add_node("u_buf", ntype="cell", gate_type="$buf",
                 output_wire="unused", input_wires=["c"], is_po=False)

    g.G.add_edge("PI:a", "u_and", wire="a")
    g.G.add_edge(CONST_1, "u_and", wire="1'b1")
    g.G.add_edge("u_and", "u_not", wire="n1")
    g.G.add_edge("PI:c", "u_buf", wire="c")

    g.wire_driver = {"a": "PI:a", "c": "PI:c", "1'b0": CONST_0, "1'b1": CONST_1,
                     "n1": "u_and", "y": "u_not", "unused": "u_buf"}
    g.wire_readers = {"a": ["u_and"], "c": ["u_buf"], "1'b1": ["u_and"],
                      "n1": ["u_not"], "y": [], "unused": []}
    return g


# ── tests ───────────────────────────────────────────────────────────────────

class TestRemoveDangling:
    def test_removes_dangling_gate(self):
        g = _make_toy_graph()
        t = NetlistTransformer(g)
        assert "u_buf" in g.G.nodes
        n = t.remove_dangling()
        assert n == 1
        assert "u_buf" not in g.G.nodes
        assert "unused" not in g.wire_driver
        assert "u_and" in g.G.nodes
        assert "u_not" in g.G.nodes

    def test_second_remove_does_nothing(self):
        g = _make_toy_graph()
        t = NetlistTransformer(g)
        t.remove_dangling()
        n = t.remove_dangling()
        assert n == 0


class TestConstantPropagation:
    def test_and_with_const_0_replaced_by_zero(self):
        """AND(a, 0) → gate removed, successors rewire to CONST_0"""
        g = NetlistGraph()
        g.module_name = "test"
        g.primary_inputs = {"a": "PI:a"}
        g.primary_outputs = {"y": "u_and"}

        g.G.add_node("PI:a", ntype="pi", output_wire="a")
        g.G.add_node(CONST_0, ntype="const", output_wire="1'b0")
        g.G.add_node("u_and", ntype="cell", gate_type="$and",
                     output_wire="y", input_wires=["a", "1'b0"], is_po=True)
        g.G.add_edge("PI:a", "u_and", wire="a")
        g.G.add_edge(CONST_0, "u_and", wire="1'b0")
        g.wire_driver = {"a": "PI:a", "1'b0": CONST_0, "y": "u_and"}
        g.wire_readers = {"a": ["u_and"], "1'b0": ["u_and"], "y": []}

        t = NetlistTransformer(g)
        n = t.simplify_constant_gates()
        assert n >= 1
        assert "u_and" not in g.G.nodes
        assert g.primary_outputs.get("y") == CONST_0

    def test_and_with_const_1_passes_through(self):
        """AND(a, 1) → gate removed, output driven directly by 'a'"""
        g = NetlistGraph()
        g.module_name = "test"
        g.primary_inputs = {"a": "PI:a"}
        g.primary_outputs = {"y": "u_and"}

        g.G.add_node("PI:a", ntype="pi", output_wire="a")
        g.G.add_node(CONST_1, ntype="const", output_wire="1'b1")
        g.G.add_node("u_and", ntype="cell", gate_type="$and",
                     output_wire="y", input_wires=["a", "1'b1"], is_po=True)
        g.G.add_edge("PI:a", "u_and", wire="a")
        g.G.add_edge(CONST_1, "u_and", wire="1'b1")
        g.wire_driver = {"a": "PI:a", "1'b1": CONST_1, "y": "u_and"}
        g.wire_readers = {"a": ["u_and"], "1'b1": ["u_and"], "y": []}

        t = NetlistTransformer(g)
        n = t.simplify_constant_gates()
        assert n >= 1
        assert "u_and" not in g.G.nodes
        assert g.primary_outputs.get("y") == "PI:a"

    def test_xor_with_const_1_rewrites_to_not(self):
        """XOR(a, 1) → gate type changes to NOT"""
        g = NetlistGraph()
        g.module_name = "test"
        g.primary_inputs = {"a": "PI:a"}
        g.primary_outputs = {"y": "u_xor"}

        g.G.add_node("PI:a", ntype="pi", output_wire="a")
        g.G.add_node(CONST_1, ntype="const", output_wire="1'b1")
        g.G.add_node("u_xor", ntype="cell", gate_type="$xor",
                     output_wire="y", input_wires=["a", "1'b1"], is_po=True)
        g.G.add_edge("PI:a", "u_xor", wire="a")
        g.G.add_edge(CONST_1, "u_xor", wire="1'b1")
        g.wire_driver = {"a": "PI:a", "1'b1": CONST_1, "y": "u_xor"}
        g.wire_readers = {"a": ["u_xor"], "1'b1": ["u_xor"], "y": []}

        t = NetlistTransformer(g)
        n = t.simplify_constant_gates()
        assert n >= 1
        assert g.G.nodes["u_xor"].get("gate_type") == "$not"


class TestFuseNotBuf:
    def test_fuses_not_buf_removes_buf(self):
        """internal NOT→BUF→load: BUF removed, NOT drives load directly"""
        g = NetlistGraph()
        g.module_name = "test"
        g.primary_inputs = {"a": "PI:a"}
        g.primary_outputs = {"y": "u_load"}

        g.G.add_node("PI:a", ntype="pi", output_wire="a")
        g.G.add_node("u_not", ntype="cell", gate_type="$not",
                     output_wire="n1", input_wires=["a"], is_po=False)
        g.G.add_node("u_buf", ntype="cell", gate_type="$buf",
                     output_wire="n2", input_wires=["n1"], is_po=False)
        g.G.add_node("u_load", ntype="cell", gate_type="$and",
                     output_wire="y", input_wires=["n2", "a"], is_po=True)
        g.G.add_edge("PI:a", "u_not", wire="a")
        g.G.add_edge("u_not", "u_buf", wire="n1")
        g.G.add_edge("u_buf", "u_load", wire="n2")
        g.G.add_edge("PI:a", "u_load", wire="a")
        g.wire_driver = {"a": "PI:a", "n1": "u_not", "n2": "u_buf", "y": "u_load"}
        g.wire_readers = {"a": ["u_not", "u_load"], "n1": ["u_buf"], "n2": ["u_load"], "y": []}

        t = NetlistTransformer(g)
        n = t.fuse_not_buf_pairs()
        assert n == 1
        assert "u_buf" not in g.G.nodes
        # u_load should now read from u_not (via wire n1)
        assert g.G.has_edge("u_not", "u_load")


class TestCollapseNotNot:
    def test_collapses_back_to_back_inverters(self):
        """internal NOT→NOT→load: NOTs collapsed, input drives load directly"""
        g = NetlistGraph()
        g.module_name = "test"
        g.primary_inputs = {"a": "PI:a"}
        g.primary_outputs = {"y": "u_load"}

        g.G.add_node("PI:a", ntype="pi", output_wire="a")
        g.G.add_node("u_not1", ntype="cell", gate_type="$not",
                     output_wire="n1", input_wires=["a"], is_po=False)
        g.G.add_node("u_not2", ntype="cell", gate_type="$not",
                     output_wire="n2", input_wires=["n1"], is_po=False)
        g.G.add_node("u_load", ntype="cell", gate_type="$buf",
                     output_wire="y", input_wires=["n2"], is_po=True)
        g.G.add_edge("PI:a", "u_not1", wire="a")
        g.G.add_edge("u_not1", "u_not2", wire="n1")
        g.G.add_edge("u_not2", "u_load", wire="n2")
        g.wire_driver = {"a": "PI:a", "n1": "u_not1", "n2": "u_not2", "y": "u_load"}
        g.wire_readers = {"a": ["u_not1"], "n1": ["u_not2"], "n2": ["u_load"], "y": []}

        t = NetlistTransformer(g)
        n = t.collapse_not_not_pairs()
        assert n >= 1
        # u_not2 and u_not1 should be gone; u_load now reads from PI:a
        assert "u_not2" not in g.G.nodes
        assert g.G.has_edge("PI:a", "u_load")


class TestBufferHighFanout:
    def test_inserts_buffers_for_high_fanout(self):
        """Signal driving 4 loads with max_fanout=2 → buffers inserted"""
        g = NetlistGraph()
        g.module_name = "test"
        g.primary_inputs = {"a": "PI:a"}
        g.primary_outputs = {}

        g.G.add_node("PI:a", ntype="pi", output_wire="a")
        drivers = []
        for i in range(4):
            nid = f"u_load{i}"
            g.G.add_node(nid, ntype="cell", gate_type="$buf",
                         output_wire=f"load{i}", input_wires=["a"], is_po=False)
            g.G.add_edge("PI:a", nid, wire="a")
            drivers.append(nid)

        g.wire_driver = {"a": "PI:a"}
        g.wire_readers = {"a": drivers}
        for i, nid in enumerate(drivers):
            g.wire_driver[f"load{i}"] = nid
            g.wire_readers[f"load{i}"] = []

        t = NetlistTransformer(g)
        n = t.buffer_high_fanout("a", max_fanout=2)
        assert n >= 2
        fo = g.G.out_degree("PI:a")
        assert fo <= 2
