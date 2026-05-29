# ICCAD 2026 Problem A — 工作日志

## 系统概述

### 比赛背景

ICCAD 2026 Problem A（LLM-Assisted Netlist Exploration and Transformation）要求参赛队伍构建一个系统，接收自然语言请求，解释并执行门级 Verilog 网表的分析或变换操作。系统包含两个核心组件：

1. **EDA 后端**：门级网表引擎，提供图遍历、结构分析、逻辑优化等操作
2. **AI Agent 前端**：基于 LLM 的 ReAct 代理，将自然语言翻译为 EDA 操作序列

### 系统架构

```
main.py                           # 竞赛入口：读取 stdin 请求，管理测试用例生命周期
├── config.py                     # YAML 配置解析（LLM 端点、模型、温度等）
├── agent/react_agent.py          # ReAct 代理：LLM 交互循环、工具调度
│   ├── agent/llm_client.py       # OpenAI / Anthropic LLM 客户端
│   ├── agent/tool_schema.py      # 工具定义（TOOL_SPECS）+ 系统提示词 + 分类逻辑
│   └── agent/rule_router.py      # 确定性规则路由（当前禁用）
└── eda/backend.py                # EDA 高层 API：68 个工具方法
    ├── eda/netlist_graph.py      # 网表图数据结构
    ├── eda/transformer.py        # 结构变换（插入/删除/替换门）
    ├── eda/optimizer.py          # ABC 逻辑优化（锥形优化）
    ├── eda/writer.py             # Verilog 网表写出
    └── eda/yosys_backend.py      # Yosys 子进程封装
```

### 执行流程

1. `cada0001_alpha -config config.yaml` 启动
2. `main.py` 初始化 LLMClient、EDABackend、ReactAgent
3. 测试用例开始：从 stdin 读取请求，逐行处理
4. **首个请求**（"This is the beginning of testcase..."）→ 本地处理，跳过 LLM
5. **后续请求** → ReactAgent.run()：
   - 分类请求（分析/变换）→ 选择工具子集
   - 发送 [system_prompt + tools + history + request] 给 LLM
   - LLM 返回 tool_calls → 执行工具 → 返回结果
   - 如果 LLM 返回文本 → 作为最终答案
6. 输出写入 `#RESPONSE N ... #END N` 格式的 stdout 和 `.log` 文件

### 关键指标

- **评估标准**：每个测试用例 1 分，优化题按 cost_best / cost 排名
- **本次优化目标**：**将 cost 视为 token 消耗**，在保持 100% 正确率的前提下最小化 token 使用
- **Token 构成**：99.4% prompt tokens（system + tools + history） + 0.6% completion tokens
- **测试规模**：100 个测试用例（test01-100），涵盖基本操作、分析查询、变换优化

---

## 优化方向总览

| 方向 | 名称 | 位置 | 预估收益 |
|------|------|------|---------|
| D1 | 精简工具定义 | `tool_schema.py` | 中 |
| D2 | 首个请求本地处理 | `main.py`（已有） | 低 |
| D3 | 压缩 system prompt | `tool_schema.py` | 中 |
| D4 | 对话历史截断 | `react_agent.py` | 中 |
| D5 | 动态工具子集 | `tool_schema.py` + `react_agent.py` | 中高 |
| D6 | 减少 LLM 轮次/重试 | `react_agent.py` | 低中 |
| D7 | 历史去工具结果 | `react_agent.py` | 高 |
| D9 | system prompt 规则迁移 | `tool_schema.py` | 中 |
| D10 | 按工具类型分级截断 | `react_agent.py` | 中 |

---

## 优化详情

### D1 — 精简工具定义

**文件**：`agent/tool_schema.py`

**改动**：将 68 个工具的 description 从多句描述压缩为单句。自解释参数（from_signal、to_signal 等）删除参数级 description，仅为 4 个非直觉参数（through、key、style、objective、const_value）保留描述。

**原理**：工具定义与 system prompt 一起发送，每个 LLM 调用都携带全部工具定义。减少描述长度直接降低每调用的 token 开销。

---

### D2 — 首个请求本地处理

**文件**：`main.py`（已有逻辑，确认有效）

**改动**：每个测试用例的第一个请求（"This is the beginning of testcase..."）被 main.py 本地处理，直接返回确认消息，不调用 LLM。

**原理**：首个请求纯属格式性（设置测试用例名称、初始化日志），无需 LLM 参与。

---

### D3 — 压缩 system prompt

**文件**：`agent/tool_schema.py`

**改动**：
- System prompt 从 648 tokens 压缩至约 315 tokens（-51%）
- 删除冗余描述、合并相似句子
- 保留：角色定义、门类型、状态持久化说明
- 后续在 D9 中进一步压缩

**原理**：System prompt 每条 LLM 调用都带，即使减少一行也有累积效果。

---

### D4 — 对话历史截断

**文件**：`agent/react_agent.py`

**改动**：
- `HISTORY_CONTENT_LIMIT` 从 1600 降至 1200 chars
- `_compact_for_history()` 函数简化（去除复杂的行/单词保护逻辑）

**原理**：对话历史在测试用例内累积——20 请求的测试用例到后期可达 10000+ tokens。截断单条消息减少历史膨胀。分析显示 74% 的回复 <200 字符，1200 足够保留关键信息。

---

### D5 — 动态工具子集分类

**文件**：`agent/tool_schema.py` + `agent/react_agent.py`

**改动**：
- 新增 `_ANALYSIS_ONLY_TOOLS`（52 个工具）：排除 16 个重变换/优化工具
- 新增 `_TRANSFORM_KEYWORDS`：保守关键词列表用于分类
- 新增 `_is_transform_request()`：检测请求是否为变换类
- 新增 `get_tools_for_request()`：按请求类型返回对应工具子集
- `ReactAgent.run()` 每次请求动态调用 `get_tools_for_request()` 而非使用静态 `self.tools`

**分类逻辑**：
```
分析请求（无变换关键词）→ 52 工具子集（约节省 1500 tokens/调用）
变换请求（含变换关键词）→ 完整 68 工具集
```

**关键词列表**（持续扩充）：
```
transform, replace, convert, insert, buffer, remove, prune, merge, 
collapse, fuse, simplify, simplif, propagat, remap, restructure, 
optimi, reduc, minimi, write the current design, write out,
verify, check equivalence, check the current netlist, prove, 
reconnect, equivalen, eliminate, dangling, unused
```

**修复记录**：
- 将 `check_equiv` 和 `check_original_equiv` 加入分析集（它们是验证工具，非变换工具）
- 添加 `equivalent` → `equivalen`（修正：`equivalent` 不是 `equivalence` 的子串）
- 添加 `eliminate`、`dangling`、`unused` 以覆盖更多请求措辞

---

### D6 — 减少 LLM 轮次/重试

**文件**：`agent/react_agent.py`

**改动**：
- `MAX_ROUNDS`：5 → 3（每个请求最大工具调用迭代次数）
- `LLM_RETRIES`：5 → 3（API 调用失败重试次数）

**原理**：实际的 ReAct 循环在首次工具执行后即返回结果（不进入第二轮），且分析显示 API 调用极少失败。5 次重试和 5 轮迭代过大。

---

### D7 — 历史去工具结果

**文件**：`agent/react_agent.py`

**改动**：
- 导入 `_is_transform_request` 用于请求分类
- `run()` 方法中新增 `is_transform` 标记
- **分析请求**：历史上仅保留 `[user] → [assistant]` 两条消息。跳过工具调用（`make_assistant_tool_call_message`）和工具结果（`make_tool_result_message`），因为 assistant 回复已包含完整答案。
- **变换请求**：历史上保留完整 `[user] → [assistant(tool_calls)] → [tool] → [assistant]` 四条消息。后续请求可能需要引用工具调用细节和变换效果。

**原理**：
```
分析请求历史（v3 之前）：
  [user]: "What is the max depth?"
  [assistant]: (tool_call: get_max_depth, args: {...})     ← 约 120 chars
  [tool]:     "Max depth from in0 to out3 is 7..."         ← 约 600+ chars
  [assistant]: "OK: Max depth from in0 to out3 is 7..."   ← 约 600+ chars
  总计：~1320+ chars

分析请求历史（v4）：
  [user]: "What is the max depth?"
  [assistant]: "OK: Max depth from in0 to out3 is 7..."   ← 约 600+ chars
  总计：~600 chars (节省 55%)
```

**效益**：最大的单次 token 节省方向（-15.8% vs v3）。

---

### D9 — system prompt 规则迁移到工具描述

**文件**：`agent/tool_schema.py`

**改动**：
- 原始 system prompt 的 9 条 Rules 部分（~150 tokens）完全删除
- 关键规则嵌入到对应工具的 description 中：
  - `read_design` → "Call this first before any analysis or transformation."
  - `optimize_design_depth` → "Use this for design-wide depth optimization."
  - `buffer_all_high_fanout` → "Use this for design-wide fanout limits."
  - `optimize_cone` → "Verify constraint is met after optimization."
  - `simplify_constant_gates` → "Use report_constant_input_gates first; check result with last_operation_count."
  - `check_equiv` → "For current vs original loaded design use check_original_equiv instead."
- 在 system prompt 中添加 4 条精简规则（~60 tokens）：
  1. 先调 read_design
  2. 主动执行变换操作（非被动查询）
  3. last_operation_count 必须在变换后使用
  4. 信任工具的 cap/limit，不做穷举搜索

**关键修复**：test49 持续失败，因为 LLM 将 "How many dangling gates were removed?" 被动查询 `last_operation_count`（返回 0）而非主动调用 `remove_dangling`。添加规则 "For post-transformation counts use last_operation_count, but only after performing the corresponding transformation" 后修复。

---

### D10 — 按工具类型分级截断

**文件**：`agent/react_agent.py`

**改动**：
- 新增 `_TOOL_CATEGORY_LIMITS` 字典，为每个工具定义截断上限
- 新增 `_limit_for_tool()` 函数
- 修改 `_compact_for_history()` 接受可选 `tool_name` 参数

**分级策略**：

| 类别 | 工具举例 | 截断上限 |
|------|---------|---------|
| 验证类 | check_equiv, is_cut_between_pi_po, same_clock_domain | 400 chars |
| 查询类 | gate_info, design_summary, primary_io_counts, get_fanout | 600 chars |
| 路径/锥形类 | find_path, report_cone_size, transitive_fanin | 800 chars |
| 变换类 | optimize_cone, buffer_high_fanout, remove_dangling | 1200 chars |
| 默认 | 未分类工具 + assistant 回复 | 800 chars |

**原理**：不同工具的输出长度差异很大。验证类只需 yes/no（400 chars 足够），而变换类需要保留操作细节（1200 chars）。比统一截断更精准地减少历史 token。

---

## 测试结果演进

| 版本 | 方向 | 通过率 | Token | vs Baseline | vs 上一版 |
|------|------|--------|-------|-------------|----------|
| v0 (baseline) | — | 100/100 | 3,960,373 | — | — |
| v1 | D1-D4 | 99/100 | 3,338,255 | -15.7% | — |
| v2 | D1-D6 | 100/100 | 2,999,099 | -24.3% | -10.2% |
| v3 | D1-D10 | 100/100 | 2,873,817 | -27.4% | -4.2% |
| **v4** | **D1-D10+D7** | **100/100** | **2,420,215** | **-38.9%** | **-15.8%** |

最终结果：**Token 消耗降低 38.9%，100/100 测试通过**。

---

## 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `agent/tool_schema.py` | TOOL_SPECS 精简、system prompt 压缩/重写、分类逻辑（`_ANALYSIS_ONLY_TOOLS`、`_TRANSFORM_KEYWORDS`、`_is_transform_request`、`get_tools_for_request`、`_build_tools`）、工具描述增强 |
| `agent/react_agent.py` | 历史截断重构、`get_tools_for_request` 集成、MAX_ROUNDS/LLM_RETRIES 调整、`_TOOL_CATEGORY_LIMITS` 分级截断、历史去工具结果逻辑、`_is_transform_request` 导入 |
| `config.yaml` | `enable_rule_router: false` |

---

## 已知问题与经验教训

### LLM 非确定性问题

test33 在多次运行中偶尔以 `all_paths_through` 参数缺失错误失败，重试后通过。这是 LLM 非确定性导致的——无法通过代码修复，但可通过将 `through` 参数保留描述来降低概率。

### 关键词分类精度

`_is_transform_request` 基于子串匹配的保守分类。`equivalent` vs `equivalence` 的失败案例说明：`"equivalent" in "equivalence"` 返回 `False`（因为 't' ≠ 'c'）。修正为 `"equivalen"` 即可同时匹配两个词。这是保守子串匹配的固有风险——需持续根据实际测试结果扩充关键词。

### 历史裁剪的风险

D7 是收益最大但也最敏感的方向。分析请求不保留工具细节的假设（"assistant 回复包含完整答案"）目前成立，但如果后续测试用例中有跨请求的上下文依赖（如 "使用刚才找到的信号"），可能需要调整分类逻辑。

---

## 文件清单与清理建议

### 可删除的文件

| 文件/目录 | 大小 | 原因 |
|----------|------|------|
| `config.llm_only.yaml` | 0.5 KB | **含硬编码 API Key**，安全风险。当前使用 `config.yaml` |
| `FINAL_RESULTS_20260526/` | 144 MB | 旧测试结果存档 |
| `FINAL_RESULTS_20260527_API_RETEST/` | 144 MB | 旧测试结果存档 |
| `run_outputs_llm_only_full4/` | 3.3 MB | 旧测试输出 |
| `run_outputs_token_opt_v1/` | 2.8 MB | 旧测试输出 |
| `run_outputs_token_opt_v1_retest33/` | 13 KB | 旧测试输出 |
| `run_outputs_token_opt_v2/` | 2.8 MB | 旧测试输出 |
| `run_outputs_token_opt_v3/` | 2.9 MB | 旧测试输出 |
| `__pycache__/` (所有) | <1 MB | 自动生成，会重新生成 |
| `.pytest_cache/` | <1 MB | 测试缓存，会重新生成 |

**总计可释放：约 300 MB**

### 参考文件（可保留或删除）

| 文件 | 说明 |
|------|------|
| `REPORT.md` | 项目报告（个人备忘） |
| `TECHNICAL_REPORT.md` | 技术报告（个人备忘） |
| `docs/PROJECT_FLOW_EXPLAINED.md` | 系统流程说明（可能过时） |
| `A_20260212.pdf` | 竞赛题目 PDF |
| `A_20260212_extracted.txt` | 竞赛题目文本提取 |

### `agent/rule_router.py` 说明

该文件始终被 `react_agent.py` 导入，但运行路径被 `enable_rule_router: false` 配置关断。当前状态为 **导入但不执行**。如需彻底删除此文件，需同时修改 `react_agent.py` 删除导入和相关调用点。

### 保留的运行输出

`run_outputs_token_opt_v4/`（2.8 MB）是唯一有参考价值的运行输出——包含最终 100/100 通过的测试结果。

---
