# ICCAD 2026 Problem A — 项目架构与工作流程

## 项目概览

本项目是 **ICCAD 2026 Contest Problem A: LLM-Assisted Netlist Exploration and Transformation** 的参赛提交。系统由一个 ReAct 风格的 LLM Agent 驱动自研 EDA 后端引擎，接收自然语言请求，对门级 Verilog 网表执行分析、变换和验证操作。

**团队编号**: 0606 | **阶段**: alpha | **可执行文件**: `cada0606_alpha`

---

## 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│                     contest evaluator                        │
│              stdin (NL requests)  ──►  stdout (#RESPONSE)   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                       main.py                                │
│  入口点: 解析 -config → 初始化各子系统 → 主循环              │
│  · 从 stdin 逐行读取请求                                      │
│  · 输出 #RESPONSE <id> / #END <id> 块                        │
│  · 镜像到 <case_name>.log                                    │
└──────────────────────────────────────────────────────────────┘
           │                    │                    │
           ▼                    ▼                    ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   config.py      │  │  agent/          │  │  eda/            │
│   YAML 配置解析   │  │  LLM Agent 层    │  │  EDA 后端引擎    │
│   · provider选择  │  │                 │  │                 │
│   · API Key管理   │  │  llm_client.py  │  │  backend.py     │
│   · 环境变量回退   │  │  react_agent.py │  │  netlist_graph  │
└──────────────────┘  │  tool_schema.py │  │  yosys_backend  │
                       └──────────────────┘  │  transformer    │
                                              │  optimizer      │
                                              │  writer         │
                                              └──────────────────┘
```

---

## 详细工作流程

### 阶段一：启动与初始化

```
./cada0606_alpha -config config.yaml
```

1. **`main.py`** 解析 `-config` 参数，调用 `config.py:load_config()` 加载 YAML 配置
2. `load_config()` 读取 `provider`、对应 LLM 的 `api_key`/`model`/`base_url`、`generation` 参数
   - API Key 缺失时自动回退到环境变量 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
   - 占位符 `<YOUR_xxx_API_KEY>` 会被识别并触发回退逻辑
3. `build_system()` 初始化三个核心子系统：
   - **YosysBackend** → 定位并验证 Yosys 二进制
   - **LLMClient** → 根据 `provider` 构建 OpenAI 或 Anthropic SDK 客户端
   - **EDABackend** → 创建 YosysBackend + VerilogWriter + ConeOptimizer
   - **ReactAgent** → 绑定 LLMClient + EDABackend
4. 注册 `SIGTERM` / `SIGINT` 信号处理器，确保退出时刷新日志

### 阶段二：主循环 — 逐请求处理

```
for each line in stdin:
    request = line.strip()
    response_id += 1
```

#### 2.1 首个请求 — Testcase 初始化

系统从自然语言文本中检测 testcase 名称和日志文件名：

```
输入: "This is the beginning of testcase case28. Please output a copy of the log into case28.log."
检测: case_name=case28, log_file=case28.log
```

实现位于 `main.py:extract_case_info()`，支持三种正则模式匹配：
- `case name is '<name>'`
- `casename: <name>`
- `beginning of testcase <name>`

系统随后：
1. 调用 `agent.reset()` 清空对话历史
2. 创建 `<case_name>.log` 日志文件
3. 输出 `#RESPONSE 1` 确认消息（**不经过 LLM**，直接生成）
4. 跳过 LLM 调用，等待下一个请求

#### 2.2 后续请求 — ReAct Agent 循环

每个后续请求进入 `ReactAgent.run()` 方法：

```
user_request
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. 请求预处理                                               │
│    · _compact_user_request(): 去除功能性等价性保证等冗余短语 │
│    · 限制长度 ≤ 560 字符                                    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. 工具筛选 (get_tools_for_request)                         │
│    根据请求内容匹配最佳的紧凑工具子集                         │
│    · ~40 个语义分类器，按优先级从具体到通用匹配               │
│    · 如: "how many AND gates" → _GATE_TYPE_COUNT_TOOLS (2个)  │
│    · 如: "optimize the cone" → _DEPTH_CONE_OPT_TOOLS (5个)   │
│    · 未匹配 → BASIC 子集 (37 个工具)                         │
│    · 如果已加载设计，自动过滤 read_design                     │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. LLM 调用 (llm_client.chat)                               │
│    构造消息历史: [system_prompt, state_context, history...]  │
│    发送给 GPT-4o-mini 或 Claude Haiku 4.5                    │
│    · 支持 Native Function Calling / Tool Use                │
│    · 最多 2 次重试，指数退避                                 │
│    · SDK 不可用时自动回退到 curl (含 Windows 兼容)           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. 响应处理                                                 │
│    ┌─ 纯文本响应 → 标准化后直接返回                          │
│    └─ 工具调用 → 执行每个工具，汇总结果返回                   │
│       · _canonical_tool_name(): 处理PascalCase/camelCase变体 │
│       · _dispatch(): 映射工具名→EDABackend方法               │
│       · 更新状态摘要 (state_summary / last_action_summary)   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. 历史管理                                                 │
│    · 仅将紧凑摘要存入对话历史 (非完整工具结果)                │
│    · 滑动窗口: 保留最近 3 个用户轮次                          │
│    · 每个工具有独立截断限制 (如 transform 600字符, verify 200) │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
  #RESPONSE <id> + answer + #END <id>
```

### 阶段三：EDA 后端数据流

```
read_design("testcase/test.v")
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ YosysBackend.verilog_to_json()                               │
│   · 预处理: 修复越界向量引用、规范化位置参数 DFF              │
│   · 自动检测顶层模块名                                        │
│   · 为无 module 定义的 dff 实例提供 blackbox 模块             │
│   · Yosys: read_verilog → hierarchy → proc → flatten          │
│   · 输出: Yosys JSON (write_json)                            │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ NetlistGraph.from_yosys_json()                               │
│   构建 Cell-Only 有向图:                                      │
│   · PI 节点 → 主输入端口的每一位                              │
│   · CONST_0 / CONST_1 节点                                    │
│   · Cell 节点 → 门实例 (type + output_wire + input_ports)     │
│   · 边: driver → reader (wire 属性)                          │
│   · O(1) 查找缓存: wire_driver, wire_readers                  │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 所有后续操作直接在内存中的 NetworkX DiGraph 上执行             │
│ · 不需要重新解析 Verilog                                     │
│ · 变换在 NetlistTransformer 中原地修改图                       │
│ · 变换后 wire_driver / wire_readers 缓存保持一致              │
└──────────────────────────────────────────────────────────────┘
```

---

## 工具体系 — 67 个 EDA 工具

工具定义在 `agent/tool_schema.py` 的 `TOOL_SPECS` 中，分为 13 个类别：

| 类别 | 工具数 | 示例 |
|------|--------|------|
| **IO** | 2 | `read_design`, `write_design` |
| **SUMMARY** | 7 | `design_summary`, `gate_count_breakdown`, `primary_io_counts` |
| **DEPTH** | 7 | `get_max_depth`, `max_design_depth`, `max_fanin_depth` |
| **PATH** | 4 | `find_path`, `list_paths`, `all_paths_through` |
| **CONE** | 7 | `report_cone_size`, `transitive_fanin`, `shared_fanin_cones` |
| **GATE** | 3 | `gate_info`, `list_gates_by_type`, `report_constant_input_gates` |
| **STRUCTURAL** | 8 | `boolean_expression`, `internal_signals_equiv`, `articulation_points_between` |
| **RENAME** | 1 | `rename` (自动检测门/线) |
| **DFF_CLOCK** | 3 | `list_flipflops_by_clock`, `same_clock_domain` |
| **TRANSFORM** | 18 | `buffer_high_fanout`, `replace_in_cone`, `structural_duplicate_merge` 等 |
| **OPTIMIZE** | 2 | `optimize_design_depth`, `optimize_cone` |
| **VERIFY** | 5 | `check_equiv`, `verify_assertion`, `is_signal_constant` |
| **MISC** | 3 | `report_floating_signals`, `report_dff_enable_hold`, `check_signal_symmetry` |

### 智能工具筛选

系统不会将全部 67 个工具发送给 LLM（浪费 token），而是根据请求内容语义选择精确的工具子集。核心逻辑在 `tool_schema.py:get_tools_for_request()`：

```
请求: "How many AND gates are in the design?"
  → 匹配 _GATE_TYPE_COUNT_TOOLS: {read_design, count_gate_type}  (2 tools)

请求: "Optimize the cone of output n15 to depth ≤ 5"
  → 匹配 _DEPTH_CONE_OPT_TOOLS: {read_design, optimize_cone, max_fanin_depth,
                                  gate_count_breakdown, check_original_equiv}  (5 tools)

请求: "Remove dangling gates and report the result"
  → 匹配 _DANGLING_CLEANUP_TOOLS: {read_design, remove_dangling, last_operation_count,
                                    gate_count_breakdown, check_original_equiv}  (5 tools)
```

---

## 关键技术决策

### 1. Cell-Only 图模型

传统的 Yosys JSON → netlist 图通常包含独立的 wire 节点。本项目采用 **Cell-Only** 模型：

```
传统:  PI → wire → gate → wire → gate → wire → PO
本项目: PI ────────────→ gate ────────────→ gate ────────────→ PO
                        (边携带 wire 名)
```

**优势**: 节点数减半，`out_degree(node) == fanout` 恒成立，所有图算法更简洁高效。

### 2. 确定性变换

所有 `NetlistTransformer` 操作是确定性的：
- 新门名/线名由前缀+计数器生成
- 相同输入 → 相同输出
- 不依赖随机数或时间戳

### 3. 令牌优化

对话历史使用激进压缩策略：
- 非变换工具的结果不存入历史
- 变换结果截断到 60-600 字符（取决于工具类别）
- 滑动窗口只保留最近 3 个用户轮次
- 样板短语（如 "ensure functionality does not change"）自动去除

### 4. LLM 调用容错

- SDK 不可用时自动回退到 curl (含 Windows `--ssl-no-revoke` 兼容)
- 2 次重试 + 指数退避
- 敏感信息自动脱敏（API Key、URL）

---

## 工具间交互示意

```
用户: "Optimize the cone of n15 to depth ≤ 5, then verify equivalence"

Agent 第1轮 → LLM 调用
  LLM 返回 tool_calls: [optimize_cone("n15", max_depth=5, objective="min_gates")]
  │
  ├─ ConeOptimizer.optimize():
  │   1. extract_cone("n15") → 获取锥内所有门
  │   2. _build_cone_module() → 构建独立锥模块 Verilog
  │   3. Yosys ABC 优化 (带深度约束)
  │   4. check_equiv(gold, opt) → Yosys SAT 等价性验证
  │   5. _verify_depth() → 验证深度 ≤ 5
  │   6. _splice() → 将优化后门拼回主图
  │
  └─ 返回: "Opt: 42->35 gates (-7). Equiv OK. Depth OK"

Agent 第2轮 → LLM 调用
  LLM 返回 tool_calls: [check_original_equiv()]
  │
  └─ 返回: "EQUIV: current == original (1234 cells, structurally preserved)"

Agent 最终响应: "Opt: 42->35 gates (-7). Equiv OK. Depth OK\nEQUIV: current == original (1234 cells, structurally preserved)"
```

---

## 文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `main.py` | 247 | 入口点、I/O 协议、日志管理 |
| `config.py` | 188 | YAML 配置解析、环境变量回退 |
| `agent/llm_client.py` | 527 | OpenAI/Anthropic 统一客户端、curl 回退 |
| `agent/react_agent.py` | 514 | ReAct 循环、对话历史管理 |
| `agent/tool_schema.py` | 1430 | 67 工具定义、40+ 请求分类器、系统提示词 |
| `eda/backend.py` | 1966 | EDA API 实现（67 个公开方法） |
| `eda/netlist_graph.py` | 572 | Cell-Only 图模型、Yosys JSON 解析 |
| `eda/yosys_backend.py` | 539 | Yosys 子进程封装、SAT 等价性检查 |
| `eda/transformer.py` | 902 | 门级变换引擎（缓冲、替换、合并、化简等） |
| `eda/writer.py` | 349 | 门级 Verilog 输出（拓扑排序、DFF blackbox） |
| `eda/optimizer.py` | 507 | ABC 锥优化管道（提取→优化→验证→拼接） |
| `eda/constants.py` | 110 | 原语定义、门类型常量 |
| `eda/decorators.py` | 59 | `@requires_design` / `@catch_keyerror` 装饰器 |
| `eda/tool_metadata.py` | 245 | `@tool` 装饰器、元数据自动收集 |

---

## 运行环境

| 组件 | 版本/来源 |
|------|-----------|
| Python | 3.11+ |
| Yosys | ≥0.30 (apt 安装或手动编译) |
| LLM | GPT-4o-mini / Claude Haiku 4.5 (云端 API) |
| 容器 | Docker (Debian bookworm-slim) |
| 关键依赖 | networkx≥3.2, openai≥1.30, anthropic≥0.28, pyyaml≥6.0 |
