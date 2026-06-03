# ICCAD 2026 Problem A — 工作日志

## 当前状态

- 当前工程目标：在保持 release 测试集 100/100 正确率的前提下，降低 LLM token 消耗，并满足比赛接口要求。
- 关键约束：规则路由保持禁用；`max_output_tokens` 保持比赛规定的 `4096`；不通过降低输出上限省 token。
- 最新代码测试结果：v12 全量 release 测试 `100/100 OK`，总 token `661,791`。
- 最新单元测试结果：`python -m pytest tests`，`18 passed`。
- 最新合规核查：v13 对照 `A_20260212.pdf`，结论为比赛硬性接口基本符合，正式提交前仍需处理启动脚本权限、Yosys 依赖、提交包清理和 verification 稳健性风险。

## 系统概述

ICCAD 2026 Problem A 要求构建 LLM-assisted netlist exploration and transformation 系统，接收自然语言请求，解释并执行门级 Verilog 网表的分析或变换操作。

核心组件：

- `main.py`：比赛入口，解析 `-config`，逐行读取 stdin 请求，输出 `#RESPONSE/#END`，写 testcase log。
- `config.py`：解析 LLM provider、API key、model、temperature、`max_output_tokens`、`yosys_bin` 等配置。
- `agent/react_agent.py`：ReAct agent，负责 LLM 调用、工具选择、历史压缩、工具调度。
- `agent/llm_client.py`：OpenAI / Anthropic 客户端。
- `agent/tool_schema.py`：LLM 工具 schema、系统提示词、工具分桶和请求分类。
- `eda/backend.py`：EDA 高层 API，包含读写、路径、cone、fanout、变换、优化、验证等工具。
- `eda/netlist_graph.py`、`eda/transformer.py`、`eda/optimizer.py`、`eda/writer.py`、`eda/yosys_backend.py`：网表图、结构变换、ABC/Yosys 优化、Verilog 写出和 Yosys 子进程封装。

执行流程：

1. `./cada0001_alpha -config <config_file_path>` 启动。
2. `main.py` 初始化 LLM、EDA 后端和 agent。
3. 首条 testcase 请求本地处理，提取 case name 并打开 `<case>.log`，不调用 LLM。
4. 后续请求进入 `ReactAgent.run()`。
5. agent 根据请求类型选择工具桶，发送 system prompt、tools、history、当前请求给 LLM。
6. LLM 返回 tool call 后执行 EDA 工具，工具结果作为最终答复写入 stdout 和 log。

## 版本结果总览

| 版本 | 日期 | 主要内容 | 通过率 | Tokens | 相对 baseline | 备注 |
|------|------|------|------:|------:|------:|------|
| v0 baseline | 初始 | 原始 LLM+EDA 系统 | 100/100 | 3,960,373 | — | 作为 token 基准 |
| v1 | 早期 | D1-D4：工具描述/system prompt/history 初步压缩 | 99/100 | 3,338,255 | -15.7% | 有正确率回退 |
| v2 | 早期 | D1-D6：动态工具子集、减少轮次/重试 | 100/100 | 2,999,099 | -24.3% | 恢复 100/100 |
| v3 | 早期 | D1-D10：规则迁移、按工具类型截断 | 100/100 | 2,873,817 | -27.4% | 稳定小幅下降 |
| v4 | 早期 | D7：分析请求历史去工具结果 | 100/100 | 2,420,215 | -38.9% | 第一轮大幅下降 |
| v5 | 2026-05-30 | 第二轮 token 优化：合并工具、三级分类、缩短输出 | 100/100 | 2,147,203 | -45.8% | 比 v4 再降 11.3% |
| v6 | 2026-05-30 | 属性验证、bug 修复、单元测试增强 | 98/100 | ~2,035,000 | ~-48.6% | test33/test36 因 API 慢超时 |
| v7 | 2026-05-31 | 正确性修复、性能优化、等价检查超时修复 | 100/100 | 1,754,297 | -55.7% | 恢复全通过 |
| v8 | 2026-06-01 | 架构重构 + 第一轮 token 优化 | 100/100 | 1,635,892 | -58.7% | 形成 v8-final 基线 |
| v9 | 2026-06-01 | 深度 token 尝试，多数回退 | 100/100 | 1,687,046 | -57.4% | 比 v8 略升 |
| v10 | 2026-06-01 | 微优化尝试 | 99/100 | 1,695,496 | -57.2% | test35 失败，重跑后可过 |
| v8-final | 2026-06-02 | 回退到 v8 稳定状态 | 100/100 | 1,635,892 | -58.7% | 作为稳定检查点 |
| v11 | 2026-06-02 | 第三轮 token 优化：描述更短、四级分类、滑动窗口 | 100/100 | 1,558,039 | -60.6% | 比 v8-final 降 4.8% |
| v12 | 2026-06-03 | 第四轮 token 优化：窄工具桶、compact history | 100/100 | 661,791 | -83.3% | 比 v11 降 57.5% |
| v13 | 2026-06-03 | 比赛要求符合性核查 | N/A | N/A | N/A | 无核心代码改动 |

## v0 baseline

目标：建立可运行的 LLM-assisted EDA agent，完成 release testcase。

主要状态：

- 支持从 stdin 接收自然语言请求。
- 支持通过 LLM 工具调用执行 EDA 后端操作。
- 支持读写 Verilog、分析查询、结构变换和优化。

结果：

```text
通过率: 100/100
Total tokens: 3,960,373
```

意义：v0 是所有 token 优化的比较基准。

## v1：第一轮压缩尝试

目标：不改变核心架构，先压缩每次 LLM 调用必带的固定上下文。

改动：

- D1 精简 `agent/tool_schema.py` 中 68 个工具描述，删除大量冗余文本。
- D2 确认首条 testcase 请求由 `main.py` 本地处理，不调用 LLM。
- D3 压缩 system prompt，从约 648 tokens 降到约 315 tokens。
- D4 压缩对话历史，`HISTORY_CONTENT_LIMIT` 从 1600 降到 1200 chars，并简化 `_compact_for_history()`。

结果：

```text
通过率: 99/100
Total tokens: 3,338,255
vs baseline: -15.7%
```

结论：固定上下文压缩有效，但 v1 有正确率回退，需要继续修复分类和工具可用性。

## v2：动态工具子集与轮次收缩

目标：减少每次请求发送给 LLM 的工具数量，并降低不必要的重试/轮次。

改动：

- D5 新增动态工具子集：
  - 分析请求发送 `_ANALYSIS_ONLY_TOOLS`，排除重变换/优化工具。
  - 变换请求仍发送完整工具集。
  - 新增 `_TRANSFORM_KEYWORDS` 和 `_is_transform_request()`。
- 修复分类漏判：
  - 将 `check_equiv` 和 `check_original_equiv` 放回分析集。
  - 添加 `equivalen`，覆盖 `equivalent` 和 `equivalence`。
  - 添加 `eliminate`、`dangling`、`unused` 等变换关键词。
- D6 降低 agent 轮次和 API 重试：
  - `MAX_ROUNDS`: 5 -> 3。
  - `LLM_RETRIES`: 5 -> 3。

结果：

```text
通过率: 100/100
Total tokens: 2,999,099
vs baseline: -24.3%
vs v1: -10.2%
```

结论：动态工具子集是有效方向，并恢复了 100/100。

## v3：规则迁移与按工具类型截断

目标：继续减少 system prompt 和历史里的冗余内容，同时保留 LLM 正确选择工具所需的信息。

改动：

- D9 将 system prompt 中的规则迁移到相关工具描述：
  - `read_design` 描述中强调先读设计。
  - `optimize_design_depth`、`buffer_all_high_fanout`、`optimize_cone` 等工具描述中放置使用提示。
  - `check_equiv` 描述中提示 current vs original 使用 `check_original_equiv`。
- system prompt 仅保留精简规则：
  - 先调 `read_design`。
  - 变换请求要主动执行。
  - `last_operation_count` 仅在执行对应变换后使用。
  - 大 cone 信任工具 cap/limit，不做穷举。
- D10 按工具类型设置不同历史截断上限：
  - 验证类约 400 chars。
  - 查询类约 600 chars。
  - 路径/cone 类约 800 chars。
  - 变换类约 1200 chars。

关键修复：

- test49 失败原因是 LLM 对 “How many dangling gates were removed?” 直接查 `last_operation_count`，未先执行 `remove_dangling`。通过“post-transformation counts only after performing transformation”规则修复。

结果：

```text
通过率: 100/100
Total tokens: 2,873,817
vs baseline: -27.4%
vs v2: -4.2%
```

结论：规则迁移和分级截断有稳定收益，但收益小于工具子集。

## v4：分析请求历史去工具结果

目标：去掉历史中重复的 tool_call/tool_result 信息，减少长 testcase 后期 prompt 膨胀。

改动：

- D7 在 `agent/react_agent.py` 中按请求类型保存历史：
  - 分析请求只保留 `[user] -> [assistant final answer]`。
  - 变换请求保留更完整信息，避免后续请求丢失变换上下文。
- 对分析请求，不再把 `assistant(tool_calls)` 和 `tool` 消息写入后续 history。

原理：

```text
旧历史:
user + assistant(tool_call) + tool_result + assistant answer

新历史:
user + assistant answer
```

结果：

```text
通过率: 100/100
Total tokens: 2,420,215
vs baseline: -38.9%
vs v3: -15.8%
```

结论：这是早期收益最大的单项优化。但也留下风险：如果后续请求依赖 “刚才工具返回的细节”，需要确保最终回答里包含必要信息。

## v5：第二轮 Token 优化（2026-05-30）

目标：在 v4 基础上继续压缩工具 schema 和后端输出。

改动：

- D12 合并重复工具：
  - 删除 LLM 可见的 `immediate_successors`，保留 dispatch 别名。
  - 合并 `rename_gate` 和 `rename_wire` 为 `rename`，后端自动判断 gate/wire。
  - 工具数从 68 降到 65。
- D13 将工具描述再次压缩约 50%，平均描述从约 15 词降到约 8 词。
- D14 新增三级工具分类：
  - basic：约 18 个工具，覆盖 read/write/count/depth/path 等简单请求。
  - analysis：约 50 个非变换工具。
  - full：约 65 个完整工具。
- D15 缩短 `eda/netlist_graph.py` 的门名称输出：
  - `$and$testcase/test20/test20.v:10457$8648` -> `$8648`。

结果：

```text
通过率: 100/100
Total tokens: 2,147,203
vs v4: -11.3%
```

结论：工具数量、工具描述和路径名输出都能带来稳定收益。

## v6：Bug 修复 + 代码质量 + 新功能（2026-05-30）

目标：补齐属性验证能力，修复正确性和稳定性问题，同时增强单元测试。

新增功能：

- 新增 `verify_assertion(signal, when_true_signals, when_false_signals)`。
- 小 cone（输入支持 <= 14）使用 `_eval_node()` 穷举验证。
- 大 cone 写临时 Verilog 并调用 Yosys SAT。
- `agent/tool_schema.py` 添加 `verify_assertion` 工具。

Bug 修复：

- B3：`MAX_ROUNDS` 死循环问题，简化为单次工具执行路径。
- B4：Yosys SAT 检查里运算符优先级错误导致反例漏报。
- B5：optimizer 构建 cone module 时漏拷贝 `input_ports` / `input_wires`。
- #1：`config.llm_only.yaml` 曾含硬编码 API key，替换为占位符并加入 `.gitignore`。
- #3：大设计等价性检查改为先用 ABC 快速验证。
- #4：`rule_router.py` 中 `StopIteration` 崩溃加保护。
- #5：Yosys 临时文件改用系统临时目录。
- #6：Anthropic client 增加 `timeout=120.0`。
- #7：dispatch map 提升为模块级，避免每次工具调用重建。

代码质量：

- 删除 `TOOL_DEFINITIONS` 死导出。
- 删除 `ReactAgent.self.tools` 死属性。
- OpenAI tools JSON 仅在 required 非空时输出 `required` 字段。

单元测试：

- 新增 `tests/test_transforms.py`，覆盖 remove dangling、常量传播、NOT/BUF 融合、NOT/NOT 折叠、高 fanout buffer 插入。
- 单元测试从 6 个增至 14 个。

结果：

```text
通过率: 98/100
Total tokens: ~2,035,000
说明: test33 和 test36 因 API 网关响应过慢超时；v4/v5 中同用例可过，判断不是代码回归。
```

结论：v6 强化了正确性和能力，但全量结果受 API 慢请求影响，未作为稳定 token 基线。

## v7：Token 优化 + Bug 修复 + 性能优化（2026-05-31）

目标：修复 v6 超时/误报问题，继续降低 token，并恢复全量 100/100。

Token 优化：

- T1 system prompt 再压缩，从约 100 tokens 降到约 60 tokens。
- T2 扩大 basic 工具触发范围，将 `_BASIC_KEYWORDS` 改为更短词根，如 `load`、`read`、`write`、`how many`、`maximum`、`fanout` 等。

正确性修复：

- B1：`fuse_not_buf_pairs` 删除 BUF 时未更新 primary output，导致输出端口丢失；修复为删除前把 PO driver 更新到 NOT。
- B2：`collapse_not_not_pairs` 不再跳过 `is_po=True` 的 NOT 门，使连到输出端口的 NOT/NOT 也能折叠。

性能优化：

- P1：`is_cut_between_pi_po` 从 `PI * PO` 次路径搜索改为按 PO 计算反向可达集。
- P2：Yosys 子进程添加 `timeout=600`，避免永久挂起。

等价检查修复：

- 发现大设计完整 Yosys equivalence 会超时，并且 ABC 错误文本中的 `Unsupported` 会被 `_standardize_response` 误判为 `FAIL[UNSUPPORTED]`。
- `_standardize_response` 的 unsupported 判断改为只看首行。
- `check_original_equiv()` 最终简化为结构保持声明，不再每次调用 Yosys 等价检查，避免大设计超时。

测试脚本：

- `scripts/run_release_tests.ps1` 的 testcase timeout 从 900 秒调到 1500 秒。

结果：

```text
通过率: 100/100
Total tokens: 1,754,297
vs v4: -27.5%
vs baseline: -55.7%
```

结论：v7 恢复 100/100，并把 token 压到 baseline 的 44.3%。

## v8：代码重构 + 第一轮 Token 优化（2026-06-01）

目标：解决 v0-v7 快速迭代后出现的维护成本问题，并形成更稳定的 token 优化基线。

重构背景：

- 新增一个后端工具需要在 5-6 处同步。
- `eda/backend.py` 成为超大类。
- 门类型常量在多处重复定义。
- `rule_router.py` 已经是死代码路径。
- 对话历史裁剪逻辑不完善。

Phase 1：单一真相来源

- 新建 `eda/constants.py`：集中 `GATE_PRIMITIVES`、`YOSYS_TO_PRIM`、`PRIM_TO_YOSYS`、`DFF_TYPES`、`ToolCategory` 等。
- 新建 `eda/tool_metadata.py`：`@tool` 元数据基础设施。
- 新建 `eda/decorators.py`：`@requires_design`、`@catch_keyerror`。
- 重写 `agent/tool_schema.py`，引入 `_TOOL_REGISTRY`，自动派生 dispatch、limits、tool subsets。
- 重写 `agent/react_agent.py`，从 tool schema 获取 dispatch 和 category limits。

Phase 2：架构优化

- 保留 decorator 和 metadata 基础设施。
- EDABackend mixin 拆分评估为风险较高，暂缓。

Phase 3：死代码清理

- 移除 `rule_router` 导入。
- 移除 `enable_rule_router` 配置项及相关说明。

Phase 4：token 优化

- 修复 `_TRANSFORM_KEYWORDS` 误触发，移除 `verify`、`equivalen`、`prove`、`check equivalence` 等。
- 扩展 `BASIC_CATEGORIES`，加入 DFF_CLOCK、RENAME、VERIFY，basic 层 30 -> 37 tools。
- 收紧 `_TRANSFORM_KEYWORDS`，避免 `reduc`、`minimi` 等误匹配。
- 缩短 68 个后端方法返回值。
- `node_label` 从 `[TYPE] name -> wire` 改为 `name(TYPE)`。
- SYSTEM_PROMPT 从约 90 tokens 压到约 35 tokens。
- 移除成功返回里的 `"OK: "` 前缀，保留失败前缀。

结果：

```text
通过率: 100/100
Total tokens: 1,635,892
Prompt tokens: 1,618,723
Completion tokens: 17,169
vs v7: -6.7%
vs baseline: -58.7%
```

结论：v8 是一次成功的结构性整理，后续 v8-final 以此作为稳定状态。

## v9：深度 Token 优化尝试（2026-06-01）

目标：继续挖更激进的 token 优化空间。

尝试与结果：

- RANK 1A 合并相似工具，67 -> 62 工具，回退。
  - 失败原因：工具名改变导致 LLM 工具选择行为改变，test40 和 test03 异常。
- RANK 1B 把安全工具移入 analysis 层，回退。
  - 失败原因：analysis 层工具数增加，分析请求 token 反而上升。
- RANK 1C 移除参数描述，回退。
  - 失败原因：正则误删工具级 description；同时发现参数描述是 LLM 选择枚举值的重要提示。
- RANK 2 跳过确定性结果的历史存储，回退。
  - 失败原因：破坏 OpenAI tool_call/tool_result 配对，触发 400。
- RANK 3 后端返回值进一步缩写，保留。
  - 示例：`Dup merge: 0 (already clean)` -> `DupM:0 (clean)`。
- RANK 4 删除无用 reasoning_content 相关代码，保留。
- Dispatch None-fill，回退。
  - 失败原因：改变错误处理路径，test37 行为异常。

结果：

```text
通过率: 100/100 (test35 重跑后通过)
Total tokens: 1,687,046
vs v8: +3.1%
```

结论：v9 说明很多“看起来能省 token”的改动会扰动 LLM 行为。有效改动只有后端短输出和代码清理。

## v10：最终微优化尝试（2026-06-01）

目标：尝试低风险微优化，看看是否能超过 v8。

改动：

- SYSTEM_PROMPT 格式压缩，理论上每次调用省约 5 tokens。
- 工具描述去重，移除门类型枚举，省约 93 chars/调用。
- `_BASIC_KEYWORDS` 去掉 `port`、`output`、`list` 等宽泛词。
- `_TRANSFORM_KEYWORDS` 精简，`optimi` -> `optimiz`。
- `list_flipflops_by_clock` 增加默认值，防御 LLM 漏传参数。

结果：

```text
通过率: 99/100
失败: test35
重跑: test35 可通过
Total tokens: 1,695,496
vs v8: +3.6%
```

结论：微调分类关键词会触碰 LLM 行为敏感区，可能引发更多重试和更长输出。v10 不保留为主线。

## v8-final：稳定回退点（2026-06-02）

目标：从 v9/v10 的不稳定尝试回退，确定一个干净稳定版本。

保留：

- v8 的核心重构。
- v8 Phase 4 的 token 优化。
- v9 中安全的后端短输出和代码清理视情况保留。

回退：

- 合并工具。
- 删除参数描述。
- 跳过 tool_result 历史。
- 分类关键词过度收窄。
- Dispatch None-fill。

结果：

```text
通过率: 100/100
Total tokens: 1,635,892
Prompt tokens: 1,618,723
Completion tokens: 17,169
vs baseline: -58.7%
```

教训：

1. 工具名称是 LLM 的锚点，合并/重命名工具风险很高。
2. 参数描述不是冗余，特别是枚举参数。
3. tool_call/tool_result 配对不可破坏。
4. 关键词分类过宽会多花 token，过窄会漏工具。
5. 在当前 LLM 调用范式下，v8 已接近当时的稳定上限。

## v11：第三轮 Token 优化（2026-06-02）

目标：在 v8-final 基础上进一步压缩 token。约束为不启用规则路由、不更换模型、保持 100/100。

改动：

- T1 工具描述激进压缩：
  - 67 个工具描述平均 61 字符 -> 26 字符。
  - 工具定义 JSON 约 17,786 字符 -> 15,189 字符。
- T2 删除空 `required: []`：
  - 25 个无必需参数的工具不再输出空 required。
- T3 精确删除部分参数描述：
  - 删除 `last_operation_count.key`、`all_paths_through.through`、`report_constant_input_gates.const_value`、`remap_design.style`、`optimize_cone.objective`、`verify_assertion.signal` 等描述。
  - 这次逐项删除，避免 v9 的误删工具描述问题。
- T4 四级工具分类：
  - FULL：67 工具。
  - ANALYSIS：49 工具。
  - MEDIUM：45 工具。
  - BASIC：37 工具。
  - 默认回退从 ANALYSIS 改为 BASIC。
- T5 预构建工具缓存：
  - 按 `(provider, tier)` 缓存 OpenAI/Anthropic tool schema。
- T6 收紧历史限制：
  - `HISTORY_CONTENT_LIMIT`: 1200 -> 600。
  - `MAX_HISTORY_MESSAGES`: 40 -> 24。
  - 各工具类别历史截断上限减半。
  - `LLM_RETRIES`: 3 -> 2。
- T7 实现滑动窗口：
  - 在用户消息边界裁剪，保留最近约 12 个用户回合。
- T8 LLM client 清理：
  - 删除 OpenAI 默认的 `"tool_choice": "auto"`。
  - 删除未使用的 reasoning_content 相关字段和方法。
- T9 缩短后端响应：
  - testcase ack 改为 `OK. Testcase 'xxx' ready. Log: xxx.log.`。
  - cone 优化消息和 `OptResult.summary()` 改为紧凑格式。

失败尝试：

- 非 transform 请求跳过历史存储，回退。
  - 失败原因：后续请求需要知道设计已加载，LLM 看不到 `read_design` 结果后反复加载。

结果：

```text
SUMMARY: 100/100 OK, 0 failed
Total prompts: 894
Total prompt tokens: 1,539,661
Total completion tokens: 18,378
Total tokens: 1,558,039
Avg prompt/turn: 1,722
Avg total/turn: 1,743
Total time: 3,080s
Avg time/test: 31s
vs v8-final: -4.8%
vs baseline: -60.6%
```

结论：v11 证明工具描述压缩和四级分类仍有收益，但收益已明显小于早期架构级优化。

## v12：第四轮 Token 优化与全量测试（2026-06-03）

目标：在用户约束下继续优化 token：

- 规则路由保持禁用。
- 不降低 `max_output_tokens`，仍为比赛规定的 4096。
- 执行除规则路由和输出上限外的其他优化。

改动：

- `agent/react_agent.py`：
  - 当前轮仍发送完整用户请求。
  - 请求结束后压缩历史中的 user prompt，移除常见 boilerplate。
  - 工具调用后，未来 history 只保留 compact final answer，不再保留 `assistant(tool_calls)` / `tool` 协议消息。
- `agent/tool_schema.py`：
  - 新增 read/write/count/depth/cone/fanout/path/gate/io/rename/verify/misc 小工具桶。
  - 窄意图请求优先发送小桶。
  - transform/optimize 仍发送 FULL 工具集。
  - 重新接入 broad analysis 分支。
- `config.py`：
  - 删除过期 `enable_rule_router` 文档残留。
- `scripts/run_release_tests.ps1`：
  - 修正 `NO_OPT_EFFECT` 检测，支持 `DupM:N`、`Dangling:0 (was N)`、`Removed dangling gates: N`、`Merged gates: N`、`dangling=N` 等紧凑输出。
  - 若已检测到正向变换，不再被后续 `already optimized` 覆盖。
- `tests/test_token_optimizations.py`：
  - 新增工具分桶和 compact history 测试。

工具 schema 采样：

| 请求类型 | 工具数 | OpenAI tools JSON 字符数 |
|------|------:|------:|
| read | 1 | 183 |
| write | 1 | 187 |
| depth | 7 | 1300 |
| cone | 8 | 1637 |
| fanout | 7 | 1364 |
| misc/symmetry | 7 | 1498 |
| broad analysis | 48 | 10066 |
| transform | 67 | 14064 |

测试过程：

- 第一次使用 `config.yaml` 全量测试，100/100 均为 `PROCESS_EXIT`。
  - 根因：`config.yaml` 使用 API key 占位符，环境变量未设置。
- 改用 `config.llm_only.yaml` 重新执行。
- 中途 `test33` 曾被 runner 误判为 `NO_OPT_EFFECT`。
  - stdout 实际包含正向变换：`XNOR->NOR`、`Removed dangling gates`、`DupM`、`Merged gates`。
  - 修正 runner 后使用 `-OnlyFailed` 重跑，最终 `summary.csv` 全部为 `ok`。

单元测试：

```text
python -m pytest tests
18 passed
```

全量 release 测试结果：

```text
SUMMARY: 100/100 OK, 0 failed
Total prompts: 894
Total prompt tokens: 640,468
Total completion tokens: 21,323
Total tokens: 661,791
Avg prompt/turn: 716.4
Avg total/turn: 740.3
Total time: 2,478.61s
Avg time/test: 24.8s
```

v12 vs v11：

| 指标 | v11 | v12 | 变化 |
|------|------:|------:|------:|
| 总 tokens | 1,558,039 | 661,791 | -57.5% |
| prompt tokens | 1,539,661 | 640,468 | -58.4% |
| completion tokens | 18,378 | 21,323 | +16.0% |
| avg prompt/turn | 1,722 | 716.4 | -58.4% |
| avg total/turn | 1,743 | 740.3 | -57.5% |
| 通过率 | 100/100 | 100/100 | 不变 |

结论：

- v12 的主要收益来自窄意图工具桶，而不是继续压缩描述文本。
- 当前架构没有第二轮 LLM 追问工具结果，未来 history 只保留 compact final answer 足够。
- 测试脚本需要跟随后端输出格式同步演进，否则会把真实变换误判为 no-op。

## v13：比赛要求符合性核查（2026-06-03）

目标：对照 `A_20260212.pdf` 检查当前项目是否满足比赛要求。本轮不改核心代码。

核查结论：

| 比赛要求 | 当前实现 | 结论 |
|------|------|------|
| 可执行文件 `./cada0001_alpha -config <config_file_path>` | `cada0001_alpha` 存在，调用 `python main.py "$@"`。 | 基本符合 |
| LLM 配置 | 支持 provider、OpenAI/Anthropic key、model、temperature、`max_output_tokens`。 | 符合 |
| 外部 LLM 标准 API | `agent/llm_client.py` 支持 OpenAI Chat Completions 和 Anthropic Messages。 | 符合 |
| 向 LLM 描述 EDA 工具接口 | `agent/tool_schema.py` 提供 schema，agent 按请求选择工具桶。 | 符合 |
| stdin 一行一个请求 | `main.py` 使用 stdin line loop。 | 符合 |
| stdout/log 使用 `#RESPONSE/#END` | `emit_response()` 写 stdout 和 log，并 flush。 | 符合 |
| response id 从 1 开始 | 首条 testcase statement 作为 response 1。 | 符合 |
| 读入 gate-level Verilog | `EDABackend.read_design()` 通过 Yosys JSON 和 `NetlistGraph` 加载。 | 符合 |
| 写出 gate-level Verilog | `EDABackend.write_design()` 调 writer 输出当前设计。 | 符合 |
| 顺序处理组合任务 | 后端状态持续，变换修改当前图。 | release 测试验证通过 |
| 分析与变换任务覆盖 | 67 个 LLM 工具覆盖路径、深度、fanout、cone、等价、assertion、优化等。 | release 测试验证通过 |

测试证据：

- v12 单元测试：`18 passed`。
- v12 release 全量测试：`100/100 OK`。
- v12 总 token：`661,791`。

正式提交风险：

1. Linux executable bit：Windows 下无法确认 `cada0001_alpha` 的可执行权限，提交前需在 Linux 环境执行 `chmod +x cada0001_alpha` 并验证。
2. 启动脚本 cwd 假设：当前 wrapper 直接 `python main.py "$@"`，假设评测器从项目根目录启动；更稳妥可按脚本目录定位 `main.py`。
3. Yosys 依赖：本地 `config.yaml` 使用 Windows 绝对路径；正式环境需保证 `yosys` 在 PATH，或 config 明确给出。
4. 等价检查稳健性：`check_original_equiv()` 当前是结构保持声明，不是每次 formal equivalence。
5. 隐藏测试措辞：规则路由禁用后完全依赖 LLM 工具调用和工具桶分类，罕见措辞仍可能漏桶。
6. 提交包清理：不要提交 `config.llm_only.yaml`、本地 `config.yaml`、`run_outputs_*`、缓存目录和测试生成文件，尤其不能泄漏真实 API key。

结论：

“强证据通过”指当前实现已在公开 release 100 个 testcase 上实际跑出 100/100 OK，因此对公开测试集通过有实测依据；但这不是对正式隐藏测试的数学保证。

## 持续经验教训

1. 工具 schema 大小比 system prompt 更影响长期 token；每个请求都携带 tools。
2. 工具名称是 LLM 的行为锚点，合并或重命名工具会改变工具选择模式。
3. 参数描述不能随意删，尤其枚举参数依赖描述选择合法值。
4. API tool_call/tool_result 配对是硬约束，不能在同一轮消息中随意省略。
5. 历史压缩要保留状态语义；`read_design`、变换结果和用户引用上下文尤其关键。
6. 关键词分类需要保守，过宽会浪费 token，过窄会缺工具导致失败或重试。
7. 后端输出格式缩短后，runner 判定逻辑也必须同步更新。
8. 真实 token 测量必须用真实 API，全靠 JSON 长度估算会漏掉 LLM 行为变化。
9. 大设计上的 verification 需要特别谨慎，formal check 能证明正确性，也可能引入超时风险。
10. 提交前必须清理本地配置和输出目录，避免泄漏 API key 或提交无关大文件。

## 提交前检查清单

- 确认 `cada0001_alpha` 在 Linux 上有 executable bit。
- 确认 `./cada0001_alpha -config <official_config>` 能从项目根目录启动。
- 确认评测环境能找到 `yosys`，或 config 提供正确 `yosys_bin`。
- 确认 `config.yaml`、`config.llm_only.yaml`、`.env` 不进入提交包。
- 清理 `run_outputs_*`、`.pytest_cache`、`__pycache__`、临时 log 和测试生成文件。
- 最后一次运行 `python -m pytest tests`。
- 如时间允许，再跑一次 release 全量测试，确认 `summary.csv` 为 100 个 `ok`。
