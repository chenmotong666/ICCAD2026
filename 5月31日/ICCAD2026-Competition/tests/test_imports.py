def test_core_imports():
    from agent.react_agent import ReactAgent
    from agent.tool_schema import TOOL_SPECS
    from eda.backend import EDABackend
    from eda.netlist_graph import NetlistGraph

    assert ReactAgent is not None
    assert EDABackend is not None
    assert NetlistGraph is not None
    assert len(TOOL_SPECS) >= 30
