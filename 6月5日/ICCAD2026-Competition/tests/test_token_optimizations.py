from agent.react_agent import ReactAgent
from agent.tool_schema import get_tools_for_request


def _openai_tool_names(tools):
    return [tool["function"]["name"] for tool in tools]


def test_read_and_write_requests_use_single_tool_buckets():
    read_names = _openai_tool_names(
        get_tools_for_request("Please load the design from testcase/test01/test01.v.", "openai")
    )
    write_names = _openai_tool_names(
        get_tools_for_request("Write the current design to the output file out.v.", "openai")
    )

    assert read_names == ["read_design"]
    assert write_names == ["write_design"]


def test_release_read_write_variants_use_single_tool_buckets():
    read_variants = [
        "Bring the small circuit for this case into memory from testcase/test65/test65.v before answering anything about it.",
        "Start this session by opening the Verilog netlist stored at testcase/test66/test66.v.",
        "Please work on the netlist located at testcase/test68/test68.v.",
        "Load testcase/test69/test69.v and treat it as the current state.",
        "For the final synthetic case, read testcase/test70/test70.v into the system.",
    ]
    write_variants = [
        "Save the currently loaded design as test65_out.v.",
        "Write the updated gate-level Verilog to test66_out.v.",
        "Write the edited netlist as test68_out.v.",
        "Store the final design in test69_out.v.",
        "Export the cleaned design to test70_out.v.",
    ]

    for request in read_variants:
        assert _openai_tool_names(get_tools_for_request(request, "openai")) == ["read_design"]
    for request in write_variants:
        assert _openai_tool_names(get_tools_for_request(request, "openai")) == ["write_design"]


def test_depth_request_uses_small_depth_bucket():
    names = _openai_tool_names(
        get_tools_for_request(
            "What is the maximum logic depth from input a to output y?",
            "openai",
        )
    )

    assert "get_max_depth" in names
    assert "buffer_high_fanout" not in names
    assert len(names) <= 7


def test_summary_request_uses_small_summary_bucket():
    names = _openai_tool_names(
        get_tools_for_request("Please summarize the current design.", "openai")
    )

    assert "design_summary" in names
    assert "buffer_high_fanout" not in names
    assert len(names) <= 6


def test_release_analysis_variants_use_narrow_buckets():
    const_names = _openai_tool_names(
        get_tools_for_request(
            "Report all AND gates with constant 0 input.",
            "openai",
        )
    )
    summary_names = _openai_tool_names(
        get_tools_for_request(
            "Give me a compact inventory of the netlist: total primitive instances, the mix of primitive kinds, and the visible input/output interface.",
            "openai",
        )
    )
    cone_names = _openai_tool_names(
        get_tools_for_request(
            "Which output is supported by the largest amount of fanin logic, and how large is that cone?",
            "openai",
        )
    )
    pi_po_names = _openai_tool_names(
        get_tools_for_request(
            "Find whether any direct primary-input to primary-output passthroughs exist in this design.",
            "openai",
        )
    )

    assert const_names == ["read_design", "count_gate_type", "report_constant_input_gates"]
    assert "design_summary" in summary_names
    assert len(summary_names) <= 6
    assert "largest_output_cone" in cone_names
    assert len(cone_names) <= 5
    assert "direct_pi_po_connections" in pi_po_names
    assert len(pi_po_names) <= 3


def test_io_and_cone_subtypes_use_minimal_buckets():
    io_count = _openai_tool_names(
        get_tools_for_request("How many primary inputs and primary outputs does this design have?", "openai")
    )
    pi_widths = _openai_tool_names(
        get_tools_for_request("Please list all the primary inputs of this design with their bit widths.", "openai")
    )
    po_widths = _openai_tool_names(
        get_tools_for_request("List all primary outputs of this design with their bit widths.", "openai")
    )
    cone_count = _openai_tool_names(
        get_tools_for_request("How many gates are in the fanin cone of primary output n15?", "openai")
    )
    cone_large = _openai_tool_names(
        get_tools_for_request("Report all primary outputs whose fanin cone contains more than 5 gates.", "openai")
    )

    assert io_count == ["read_design", "primary_io_counts"]
    assert pi_widths == ["read_design", "list_primary_inputs_with_widths"]
    assert po_widths == ["read_design", "list_primary_outputs_with_widths"]
    assert "report_cone_size" in cone_count
    assert "transitive_fanin" not in cone_count
    assert len(cone_count) <= 3
    assert "report_large_cones" in cone_large
    assert len(cone_large) <= 3


def test_equivalence_requests_avoid_transform_full_bucket():
    design_equiv_names = _openai_tool_names(
        get_tools_for_request(
            "Prove that the transformed design is equivalent to the pre-transformation netlist.",
            "openai",
        )
    )
    signal_equiv_names = _openai_tool_names(
        get_tools_for_request(
            "I suspect n109 and n110 implement the same local function. Check whether the two internal signals are structurally or functionally identical according to your available analysis.",
            "openai",
        )
    )

    assert design_equiv_names == ["read_design", "check_equiv", "check_original_equiv"]
    assert "internal_signals_equiv" in signal_equiv_names
    assert "optimize_cone" not in signal_equiv_names
    assert len(signal_equiv_names) <= 4


def test_structural_variants_use_narrow_buckets():
    clock_names = _openai_tool_names(
        get_tools_for_request("Does dff g999 and dff g998 under the same clock domain?", "openai")
    )
    articulation_names = _openai_tool_names(
        get_tools_for_request(
            "Find all articulation points in the combinational graph between n2 and n14.",
            "openai",
        )
    )

    assert "same_clock_domain" in clock_names
    assert len(clock_names) <= 3
    assert "articulation_points_between" in articulation_names
    assert len(articulation_names) <= 3


def test_gate_count_request_uses_count_bucket():
    names = _openai_tool_names(
        get_tools_for_request("How many NAND gates are now in the current design?", "openai")
    )

    assert "count_gate_type" in names
    assert "list_gates_by_type" not in names
    assert len(names) <= 4


def test_gate_count_subtypes_use_minimal_buckets():
    breakdown_names = _openai_tool_names(
        get_tools_for_request(
            "Please count all the gates in this design and report the total count broken down by gate type.",
            "openai",
        )
    )
    type_names = _openai_tool_names(
        get_tools_for_request("How many NOT gates are currently in the design?", "openai")
    )
    last_names = _openai_tool_names(
        get_tools_for_request(
            "How many BUF gates were added by the buffer insertion just performed?",
            "openai",
        )
    )

    assert breakdown_names == ["read_design", "gate_count_breakdown"]
    assert type_names == ["read_design", "count_gate_type"]
    assert "last_operation_count" in last_names
    assert "buffer_all_high_fanout" not in last_names
    assert len(last_names) <= 3


def test_path_depth_and_fanout_subtypes_use_minimal_buckets():
    path_exists = _openai_tool_names(
        get_tools_for_request(
            "Verify whether a path connecting input n0 to output n6 exists while avoiding n94.",
            "openai",
        )
    )
    path_list = _openai_tool_names(
        get_tools_for_request(
            "Provide a complete enumeration of paths between n2 and n117[0].",
            "openai",
        )
    )
    depth_between = _openai_tool_names(
        get_tools_for_request(
            "Calculate the critical path depth between n4 and n5[0].",
            "openai",
        )
    )
    dff_clock = _openai_tool_names(
        get_tools_for_request("List all flip-flops driven by clock n0.", "openai")
    )
    direct_loads = _openai_tool_names(
        get_tools_for_request("Enumerate the immediate successors of gate g0.", "openai")
    )
    fanout_loads = _openai_tool_names(
        get_tools_for_request(
            "What is the fanout of primary input n5? List all gates that n5 drives directly.",
            "openai",
        )
    )

    assert path_exists == ["read_design", "find_path"]
    assert path_list == ["read_design", "list_paths"]
    assert depth_between == ["read_design", "get_max_depth"]
    assert dff_clock == ["read_design", "list_flipflops_by_clock"]
    assert "list_direct_loads" in direct_loads
    assert len(direct_loads) <= 3
    assert "list_direct_loads" in fanout_loads
    assert "transitive_fanin" not in fanout_loads
    assert len(fanout_loads) <= 3


def test_verify_signal_and_assertion_subtypes_use_minimal_buckets():
    signal_names = _openai_tool_names(
        get_tools_for_request(
            "Determine whether signals n2122 and n2116 are functionally equivalent.",
            "openai",
        )
    )
    assertion_names = _openai_tool_names(
        get_tools_for_request(
            "Is output n16 always 0 regardless of all inputs? Report yes or no.",
            "openai",
        )
    )

    assert "internal_signals_equiv" in signal_names
    assert "boolean_expression" not in signal_names
    assert "check_original_equiv" not in signal_names
    assert "check_signal_symmetry" not in signal_names
    assert len(signal_names) <= 2
    assert assertion_names == ["read_design", "verify_assertion"]


def test_boolean_expression_request_beats_io_bucket():
    names = _openai_tool_names(
        get_tools_for_request(
            "Derive the Boolean equation for output n16 in terms of its primary inputs.",
            "openai",
        )
    )

    assert names == ["read_design", "boolean_expression"]


def test_post_transform_reports_stay_query_only():
    const_count = _openai_tool_names(
        get_tools_for_request(
            "How many gates were eliminated by constant propagation?",
            "openai",
        )
    )
    cleanup_report = _openai_tool_names(
        get_tools_for_request(
            "After the cleanup, report the current design-wide maximum combinational depth and the current total gate breakdown.",
            "openai",
        )
    )

    assert "last_operation_count" in const_count
    assert "simplify_constant_gates" not in const_count
    assert "gate_count_breakdown" in cleanup_report
    assert "max_design_depth" in cleanup_report
    assert "remove_dangling" not in cleanup_report
    assert "collapse_not_not" not in cleanup_report


def test_transform_subtypes_use_smaller_buckets():
    xor_names = _openai_tool_names(
        get_tools_for_request(
            "Convert every XOR gate in this design to an equivalent 4-NAND circuit.",
            "openai",
        )
    )
    dangling_names = _openai_tool_names(
        get_tools_for_request(
            "Remove all dangling gates that do not contribute to any primary output.",
            "openai",
        )
    )
    notnot_names = _openai_tool_names(
        get_tools_for_request(
            "Find all back-to-back inverter pairs and collapse them into a wire.",
            "openai",
        )
    )
    buffer_each = _openai_tool_names(
        get_tools_for_request(
            "Please insert a BUF gate on signal n2 so that each load of n2 is driven through a dedicated buffer.",
            "openai",
        )
    )
    delete_names = _openai_tool_names(
        get_tools_for_request(
            "Delete all gates that do not contribute to any primary output.",
            "openai",
        )
    )
    reconstruct_names = _openai_tool_names(
        get_tools_for_request(
            "Reconstruct the entire netlist using only AND and NOT gates while preserving functional equivalence.",
            "openai",
        )
    )
    decompose_names = _openai_tool_names(
        get_tools_for_request(
            "Decompose all XOR gates in the fanin cone of n15 into AND, OR, and NOT gates without changing functionality.",
            "openai",
        )
    )
    buffer_net = _openai_tool_names(
        get_tools_for_request(
            "Try to insert buffers on the reset signal n1 to reduce its fanout to at most 4 loads per driver.",
            "openai",
        )
    )
    depth_reduce = _openai_tool_names(
        get_tools_for_request(
            "Try to optimize n15 to at most 4 levels deep.",
            "openai",
        )
    )

    assert "replace_xor_with_nand" in xor_names
    assert "replace_globally" not in xor_names
    assert len(xor_names) <= 5
    assert "remove_dangling" in dangling_names
    assert "structural_duplicate_merge" not in dangling_names
    assert len(dangling_names) <= 5
    assert "collapse_not_not" in notnot_names
    assert "remove_dangling" not in notnot_names
    assert len(notnot_names) <= 5
    assert "buffer_each_load" in buffer_each
    assert "buffer_all_high_fanout" not in buffer_each
    assert len(buffer_each) <= 5
    assert "remove_dangling" in delete_names
    assert "primary_io_counts" not in delete_names
    assert len(delete_names) <= 5
    assert "remap_design" in reconstruct_names
    assert "report_constant_input_gates" not in reconstruct_names
    assert len(reconstruct_names) <= 4
    assert "replace_in_cone" in decompose_names
    assert "transitive_fanin" not in decompose_names
    assert len(decompose_names) <= 13
    assert "buffer_high_fanout" in buffer_net
    assert "buffer_all_high_fanout" not in buffer_net
    assert len(buffer_net) <= 6
    assert "optimize_cone" in depth_reduce
    assert "optimize_design_depth" not in depth_reduce
    assert len(depth_reduce) <= 5


def test_transform_requests_use_narrow_intent_buckets():
    const_names = _openai_tool_names(
        get_tools_for_request(
            "Simplify all gates with constant inputs without changing the design function.",
            "openai",
        )
    )
    buffer_names = _openai_tool_names(
        get_tools_for_request(
            "Try to insert buffers on reset signal n1 to reduce fanout to at most 4 loads.",
            "openai",
        )
    )
    replace_names = _openai_tool_names(
        get_tools_for_request("Convert all XOR gates to NAND gates.", "openai")
    )
    depth_names = _openai_tool_names(
        get_tools_for_request(
            "Optimize the design to reduce critical path depth.",
            "openai",
        )
    )

    assert "simplify_constant_gates" in const_names
    assert "buffer_all_high_fanout" not in const_names
    assert len(const_names) <= 8
    assert "buffer_high_fanout" in buffer_names
    assert "replace_xor_with_nand" not in buffer_names
    assert len(buffer_names) <= 11
    assert "replace_xor_with_nand" in replace_names
    assert "buffer_high_fanout" not in replace_names
    assert len(replace_names) <= 13
    assert "optimize_design_depth" in depth_names
    assert "replace_xor_with_nand" not in depth_names
    assert len(depth_names) <= 10


def test_misc_and_clock_requests_keep_required_tools():
    symmetry_names = _openai_tool_names(
        get_tools_for_request(
            "Check whether the function at y is symmetric with respect to inputs a and b.",
            "openai",
        )
    )
    clock_names = _openai_tool_names(
        get_tools_for_request("List flip-flops driven by clock clk.", "openai")
    )

    assert "check_signal_symmetry" in symmetry_names
    assert "optimize_cone" not in symmetry_names
    assert len(symmetry_names) <= 7
    assert "list_flipflops_by_clock" in clock_names


class _FakeLLM:
    provider = "openai"

    def __init__(self, call_name="design_summary"):
        self.call_name = call_name
        self.seen_messages = None
        self.seen_tools = None

    def chat(self, messages, tools, system=""):
        self.seen_messages = [dict(msg) for msg in messages]
        self.seen_tools = list(tools)
        return None, [{"id": "call_1", "name": self.call_name, "arguments": {}}]

    def usage_summary(self):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class _FakeBackend:
    def design_summary(self):
        return "Module: top. Cells: 42. PI:2 PO:1."

    def gate_count_breakdown(self):
        return "Total: 42 AND:10 OR:8 NOT:24 NAND:0 NOR:0 XOR:0 XNOR:0 BUF:0 DFF:0"


def test_agent_history_keeps_compact_final_answer_only():
    llm = _FakeLLM()
    agent = ReactAgent(llm, _FakeBackend())
    request = (
        "Please summarize the current design. "
        "Ensure the design functionality does not change."
    )

    answer = agent.run(request)

    assert answer == "Module: top. Cells: 42. PI:2 PO:1."
    assert llm.seen_messages[0]["content"] == "Please summarize the current design."
    assert [msg["role"] for msg in agent.history] == ["user", "assistant"]
    assert "functionality does not change" not in agent.history[0]["content"].lower()
    assert "tool_calls" not in agent.history[1]


def test_agent_drops_read_tool_after_design_loaded():
    llm = _FakeLLM("gate_count_breakdown")
    agent = ReactAgent(llm, _FakeBackend())
    agent._state_summary = "Loaded 'top': 42 cells, PI:2 PO:1"

    agent.run("Please count all the gates in this design and report the total count broken down by gate type.")

    assert _openai_tool_names(llm.seen_tools) == ["gate_count_breakdown"]
