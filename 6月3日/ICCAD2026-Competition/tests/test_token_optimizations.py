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

    def __init__(self):
        self.seen_messages = None

    def chat(self, messages, tools, system=""):
        self.seen_messages = [dict(msg) for msg in messages]
        return None, [{"id": "call_1", "name": "design_summary", "arguments": {}}]

    def usage_summary(self):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class _FakeBackend:
    def design_summary(self):
        return "Module: top. Cells: 42. PI:2 PO:1."


def test_agent_history_keeps_compact_final_answer_only():
    llm = _FakeLLM()
    agent = ReactAgent(llm, _FakeBackend())
    request = (
        "Please summarize the current design. "
        "Ensure the design functionality does not change."
    )

    answer = agent.run(request)

    assert answer == "Module: top. Cells: 42. PI:2 PO:1."
    assert llm.seen_messages[0]["content"] == request
    assert [msg["role"] for msg in agent.history] == ["user", "assistant"]
    assert "functionality does not change" not in agent.history[0]["content"].lower()
    assert "tool_calls" not in agent.history[1]
