# ICCAD 2026 Problem A — System Design Report

## 1. Project Structure

```
contest/
├── main.py                   Entry point — stdin loop, #RESPONSE/#END formatter, log writer
├── config.py                 YAML config file parser (provider, API key, model, Yosys path)
├── config.example.yaml       Annotated example config
├── requirements.txt          Python dependencies
│
├── eda/                      EDA backend package
│   ├── __init__.py
│   ├── netlist_graph.py      Core data structure — cell-only directed graph
│   ├── yosys_backend.py      Yosys subprocess wrapper (read/write/opt/verify)
│   ├── transformer.py        Structural mutations (insert, replace, remove, buffer)
│   ├── writer.py             Gate-level Verilog emitter
│   ├── optimizer.py          Cone extract → ABC optimize → equiv check → splice
│   └── backend.py            High-level tool API exposed to the LLM agent
│
└── agent/                    LLM agent package
    ├── __init__.py
    ├── llm_client.py         Unified OpenAI + Anthropic client with native tool-use
    ├── tool_schema.py        Tool definitions (both provider formats) + system prompt
    └── react_agent.py        ReAct loop — plan, call tools, observe, reply
```

---

## 2. Architecture Overview

```
stdin  ──►  main.py (request loop)
                │
                ▼
         ReactAgent.run(request)
                │
         ┌──── ReAct loop (up to 10 rounds) ────┐
         │                                       │
         │   LLMClient.chat(history, tools)      │
         │        ↓ tool_calls                   │
         │   _dispatch(tool_name, args)          │
         │        ↓ result string                │
         │   append to history                   │
         │        ↓ final text                   │
         └───────────────────────────────────────┘
                │
                ▼
         EDABackend  (one method per tool)
                │
         ┌──────┴────────────────────────┐
         │                               │
   NetlistGraph              YosysBackend (subprocess)
   NetlistTransformer        ├── verilog_to_json
   VerilogWriter             ├── abc_optimize_verilog
   ConeOptimizer             └── check_equiv
```

---

## 3. Module Design Details

### 3.1 `eda/netlist_graph.py` — NetlistGraph

**Core design choice: cell-only nodes (no wire nodes).**

Every node in the graph is a *driver* — something that drives exactly one
output wire. There are three node types:

| Type | Node ID | Represents |
|---|---|---|
| `pi` | `PI:a`, `PI:data[3]` | Primary input port bit |
| `const` | `CONST_0`, `CONST_1` | Constant 0 or 1 |
| `cell` | instance name, e.g. `U42` | Gate or DFF |

Directed edges go `driver → reader`, labelled with the wire name.

**Why no wire nodes?**
The contest gate set has exactly one output per primitive. Wire name = cell
output, so a dedicated wire node carries zero extra information. Removing
wire nodes halves node count, makes `out_degree(node) == fanout` a trivial
identity, and simplifies every traversal algorithm.

**O(1) lookup caches (always consistent):**
- `wire_driver[wire_name]` → node_id driving that wire
- `wire_readers[wire_name]` → list of node_ids reading that wire

**Key algorithms:**

| Method | Algorithm | Complexity |
|---|---|---|
| `get_max_depth` | DP on topological sort, increment on cell-entry | O(V+E) |
| `find_path` | BFS/shortest path on subgraph (with optional exclusion/waypoint) | O(V+E) |
| `all_paths_pass_through` | Remove 'through' node; check if bypass path exists | O(V+E) |
| `extract_cone` | Backward BFS, stops at PI/const/DFF | O(V+E) |
| `get_fanout` | `G.out_degree(node)` | O(1) |

**DFF treatment:** DFF outputs act as pseudo-PIs in all combinational
analyses. Backward BFS stops when it reaches a DFF node; depth DP does not
cross DFF output edges.

---

### 3.2 `eda/yosys_backend.py` — YosysBackend

Thin subprocess wrapper. All Yosys interaction passes through a single
`run(script)` method that executes `yosys -Q -T -p "<script>"` and returns
stdout as a string.

**Key methods:**

| Method | Yosys passes used |
|---|---|
| `verilog_to_json` | `read_verilog; hierarchy; proc; flatten; write_json` |
| `json_to_verilog` | `read_json; write_verilog -noattr -noexpr` |
| `abc_optimize_verilog` | `read_verilog; hierarchy; proc; flatten; abc -g ...; write_verilog` |
| `check_equiv` | `equiv_make; equiv_simple; equiv_induct; equiv_status` |
| `optimize_full` | `opt_expr; opt_merge; opt_clean; opt` (global) |

**Depth constraint:** passed as `-D <n>` to ABC's `-g` gate mapping step.

**Swap-out path:** `_check_available()` is the only place that touches the
binary path. Replacing with `libyosys` Python bindings requires implementing
the same interface in a subclass.

---

### 3.3 `eda/transformer.py` — NetlistTransformer

Structural mutations on a `NetlistGraph` in-place. Every mutation:
1. Updates the `G` graph (add/remove nodes and edges).
2. Keeps `wire_driver` and `wire_readers` caches consistent.
3. Does **not** call Yosys — all mutations are pure Python graph operations.

**Methods:**

| Method | Description |
|---|---|
| `remove_dangling()` | Iterative backward reachability from PO nodes |
| `insert_gate_before_pattern(pattern, gate, extra_in)` | Add gate between first input driver and matched cells |
| `replace_gate_type(cell, new_prim)` | Change gate type, preserve connectivity |
| `replace_all_in_cone(output, old, new)` | Type replacement scoped to fanin cone |
| `replace_all_globally(old, new)` | Type replacement across entire design |
| `buffer_high_fanout(net, max_fo)` | Chunk successor lists; insert buf per chunk |
| `add_balance_buffers(from, to_list)` | Insert buf chains to equalise depth to each sink |
| `fuse_not_buf_pairs()` | Remove redundant buf after not; re-wire successors |

**Name generation:** `_fresh_name(prefix)` and `_fresh_wire(hint)` use
per-prefix counters, so repeated runs are deterministic.

---

### 3.4 `eda/writer.py` — VerilogWriter

Emits gate-level Verilog from a `NetlistGraph` using topological sort for
cell ordering. Handles:
- Scalar and bus port declarations
- Internal wire declarations (excludes PI and PO wires)
- Named-port DFF instantiation
- Positional primitive instantiation for all combinational gates

---

### 3.5 `eda/optimizer.py` — ConeOptimizer

Five-step pipeline for cone-level optimization:

```
1. extract_cone(output)           → set of cell node_ids
2. _build_cone_module(...)        → standalone NetlistGraph + Verilog file
3. yosys.abc_optimize_verilog()   → optimized Verilog
4. yosys.check_equiv(gold, opt)   → equivalence proof or counterexample
5. _splice(graph, old, opt_graph) → replace old cells with optimized cells
```

**Depth constraint:** if `max_depth` is set, passed to ABC as `-D <n>`.
After optimization, `_verify_depth()` re-measures the actual depth from
every PI to confirm the constraint was met.

**Splice strategy:** Optimized cells get a `_opt_{output_name}_` prefix to
avoid name collisions. PI nodes of the cone module are reverse-mapped to
their original drivers in the main graph using `wire_driver` lookups.

---

### 3.6 `eda/backend.py` — EDABackend

High-level API: one method per tool, returning a human-readable string.
This is the only module the agent needs to import from the `eda` package.

All methods follow the same pattern:
1. Call `_need_design()` to raise early if no design is loaded.
2. Delegate to the appropriate `NetlistGraph` / `NetlistTransformer` /
   `ConeOptimizer` method.
3. Return a formatted string (the agent forwards this directly as the
   `#RESPONSE` text).

---

### 3.7 `agent/llm_client.py` — LLMClient

Unified interface over OpenAI and Anthropic APIs with **native tool-use**.

```python
text, tool_calls = client.chat(messages, tools, system)
# tool_calls: [{"id": ..., "name": ..., "arguments": {...}}, ...]
```

Both providers are supported:
- **OpenAI:** `tools` array + `tool_choice="auto"` → `message.tool_calls`
- **Anthropic:** `tools` array → `content` blocks of type `tool_use`

Helper methods `make_tool_result_message()` and
`make_assistant_tool_call_message()` produce the correct provider-specific
history entries so the conversation is self-consistent.

---

### 3.8 `agent/tool_schema.py` — Tool Schema

Canonical tool definitions in `TOOL_SPECS` (a list of dicts). Two format
functions derive provider-specific lists:
- `openai_tools()` → `[{"type": "function", "function": {...}}]`
- `anthropic_tools()` → `[{"name": ..., "input_schema": {...}}]`

**19 tools are defined**, covering all task types in the contest spec:

| Group | Tools |
|---|---|
| I/O | `read_design`, `write_design`, `design_summary` |
| Analysis | `get_max_depth`, `find_path`, `all_paths_through`, `report_cone_size`, `get_fanout`, `report_large_cones`, `same_clock_domain` |
| Transformation | `insert_gate_before`, `buffer_high_fanout`, `replace_in_cone`, `replace_globally`, `remove_dangling`, `fuse_not_buf`, `add_balance_buffers` |
| Optimization | `optimize_cone` |
| Verification | `check_equiv` |

The system prompt instructs the model to:
- Call `read_design` before any analysis
- Preserve state across requests
- Give direct, factual answers
- State assumptions when the request is ambiguous

---

### 3.9 `agent/react_agent.py` — ReactAgent

ReAct loop per request (max 10 rounds):

```
while rounds < MAX_ROUNDS:
    text, tool_calls = llm.chat(history, tools, system)
    if not tool_calls:
        return text          # final answer
    for tc in tool_calls:
        result = _dispatch(backend, tc.name, tc.arguments)
        history.append(tool_result_message(tc.id, result))
```

`_dispatch()` maps tool names to `EDABackend` methods via a dict of lambdas.
All exceptions are caught and returned as error strings so the agent can
report them gracefully.

**Conversation history** is a flat list of `{role, content}` dicts, reset
at the start of each testcase via `agent.reset()`.

---

### 3.10 `main.py` — Entry Point

```
./cada0001_alpha -config config.yaml
```

1. Parses `-config` via `argparse`.
2. Loads `SystemConfig` from YAML.
3. Initialises `EDABackend` + `LLMClient` + `ReactAgent`.
4. Reads `stdin` line-by-line.
5. **Response 1** (testcase initialisation): extracts case name from the
   request using `_CASE_NAME_RE`, opens the log file, emits a direct
   acknowledgement, and inserts the exchange into history.
6. **Subsequent responses**: delegates to `agent.run()`, emits
   `#RESPONSE N / text / #END N` to stdout and mirrors to log.
7. Flushes stdout after every `#END` so the evaluator receives it immediately.

---

## 4. Data Flow: End-to-End Example

Request: *"Insert an AND gate before all buffers whose name includes `_gc__`
and connect the other input to `_gc_ctrl`."*

```
stdin → main.py
  → agent.run("Insert an AND gate ...")
  → llm.chat(history, tools)
      model → tool_call: insert_gate_before(
                  name_pattern="_gc__",
                  gate_type="and",
                  extra_input="_gc_ctrl")
  → _dispatch → backend.insert_gate_before("_gc__","and","_gc_ctrl")
      → transformer.insert_gate_before_pattern("_gc__","and","_gc_ctrl")
          → find_cells_by_pattern("_gc__")       # [U_gc__buf0, ...]
          → for each: remove old edge, add AND gate node, rewire
      → return "Inserted 3 and gate(s) before cells matching '_gc__': ..."
  → history.append(tool_result)
  → llm.chat(history, tools)
      model → text: "Replaced the matched buffers with 2-input AND gates ..."
  → return text
→ emit_response(id, text, log)
stdout: #RESPONSE N
        Replaced the matched buffers ...
        #END N
```

---

## 5. Runtime Limits and Strategies

| Request type | Limit | Strategy |
|---|---|---|
| Basic ops (read/write) | 60 s | Direct Yosys I/O; no LLM needed |
| Analysis | 300 s | Graph traversal in Python; instant for typical netlists |
| Transformation | 300 s | Pure graph mutation; no subprocess |
| Optimization | 300 s | ABC via Yosys + equiv check; cone-scoped to limit scope |
| Verification | 300 s | Yosys equiv passes; ABC cec as faster alternative |

---

## 6. Scoring Awareness

- **Analysis:** Return the exact correct value. The graph algorithms are
  deterministic and correct by construction.
- **Transformation (hard constraints):** `buffer_high_fanout` and
  `add_balance_buffers` enforce structural bounds by construction. The
  optimizer verifies the depth constraint before committing a splice.
- **Optimization (ranked by cost ratio):** The `optimize_cone` pipeline calls
  ABC which finds a locally optimal solution. For competitive ranking,
  consider running ABC multiple times with different strategies and taking
  the best gate count.

---

## 7. Known Limitations and Future Work

| Limitation | Impact | Mitigation |
|---|---|---|
| `add_balance_buffers` uses a heuristic to find the "driver of sink" | May not always find the right edge to extend | Improve using cone-aware depth measurement |
| `ConeOptimizer._splice` uses string prefix to avoid name collisions | Cosmetic (long cell names) | Add a post-splice rename pass |
| Property verification (`verify done = req & ~busy`) not yet implemented | Missing one analysis task type | Encode as miter circuit and call `smtbmc` or `pysat` |
| Clock domain analysis uses wire name heuristics | May miss CDC if clock wire has non-standard name | Parse DFF CLK port connections from Yosys JSON |
| No multi-output support | Correct per contest spec (1 output per primitive) | N/A for this contest |

---

## 8. Setup and Running

```bash
# 1. Install dependencies
pip install -r requirements.txt
apt install yosys     # or: pip install yowasp-yosys

# 2. Copy and fill in config
cp config.example.yaml config.yaml
# Edit: add API key for openai or anthropic, choose provider

# 3. Run the system
python main.py -config config.yaml < testcase_stdin.txt

# 4. Enable debug output (tool call trace)
# Set verbose: true in config.yaml

# 5. Run tests (no Yosys needed)
python -m pytest tests/  # if tests/ directory is added
```

---

## 9. Dependency Summary

| Package | Purpose | Required |
|---|---|---|
| `networkx` | Graph algorithms (topological sort, BFS, ancestors) | Yes |
| `openai` | OpenAI API client | One of the two |
| `anthropic` | Anthropic API client | One of the two |
| `pyyaml` | Config file parsing | Recommended (fallback parser included) |
| `yosys` binary | Netlist I/O, ABC optimization, equivalence check | Yes |
