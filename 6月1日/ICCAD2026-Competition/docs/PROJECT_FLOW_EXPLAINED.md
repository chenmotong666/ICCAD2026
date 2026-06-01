# 项目执行流程与 EDA 后端说明

本文档面向第一次接触本项目的人，解释当前系统是如何读取比赛测试、理解自然语言请求、调用 EDA 工具、修改网表、输出结果并验证正确性的。

重点先说清楚：

- 本项目不是简单让大模型“编答案”。
- 自然语言请求会先被本地规则路由器识别。
- 路由器只是把一句话翻译成后端函数调用。
- 真正做分析和优化的是 `eda/` 下的网表图、变换器、Yosys 封装和 Verilog 输出器。
- 如果某个请求本地规则无法识别，才会尝试走 LLM API。
- 当前 100 个 release 测试集都被本地规则覆盖，所以 token 用量显示为 0。

## 1. 项目整体结构

核心文件如下：

```text
main.py
  程序入口，读取 config，创建 EDA 后端和 agent，逐行处理比赛 prompt。

agent/react_agent.py
  agent 主控逻辑。每收到一句自然语言请求，先尝试本地 rule_router。
  如果本地路由命中，直接返回后端结果；如果没命中，再调用 LLM。

agent/rule_router.py
  本地规则路由器。用正则和关键词识别比赛 prompt 模板，
  把自然语言请求映射成 backend.xxx() 函数调用。

eda/backend.py
  EDA 功能总入口。对外提供 read_design、write_design、gate_count、
  remove_dangling、replace_xor_with_nand、optimize_cone 等高级操作。

eda/netlist_graph.py
  内部网表图结构。把 Verilog/Yosys JSON 转成节点和边。
  gate、wire、PI、PO、DFF 都在这里组织成可查询的图。

eda/transformer.py
  真正修改网表结构的地方。例如删除 dangling gate、插 buffer、
  常量传播、XOR 转 NAND、OR/AND 技术映射等。

eda/writer.py
  把修改后的 NetlistGraph 重新写成 gate-level Verilog。

eda/yosys_backend.py
  Yosys 的 Python 封装。负责把 Verilog 转成 JSON、做等价验证、
  调用 Yosys pass、检查输出网表能否被重新读入。

scripts/run_release_tests.ps1
  批量跑 100 个 release testcase 的脚本，并生成 summary.csv。
```

可以把整个项目理解成下面这条流水线：

```text
比赛 prompt
  -> main.py 逐句读取
  -> ReactAgent.run()
  -> rule_router.py 判断是否能本地处理
  -> backend.py 执行 EDA 操作
  -> transformer.py / netlist_graph.py 分析或修改网表
  -> writer.py 写出 Verilog
  -> run_release_tests.ps1 汇总结果
  -> Yosys 验证输出网表结构
```

## 2. 一次测试是怎么跑起来的

比赛测试集一般是一个 `prompt.txt`，里面按顺序写着多条自然语言命令，例如：

```text
This is the beginning of a new testcase. The case name is test67.
Please load the design from the file test67.v located in the directory testcase/test67/.
Please count all the gates in this design ...
Apply safe local simplifications ...
Please write the current design to the output file test67_out.v.
```

运行时，程序不是一次性处理整个 prompt，而是逐行读入。每一行都会产生一个 `#RESPONSE n`。

大致过程是：

1. `main.py` 从标准输入读一行请求。
2. 如果这一行是新 testcase 开始，agent 会 reset 历史状态。
3. 对普通请求，调用 `ReactAgent.run(user_request)`。
4. `ReactAgent.run()` 先问 `rule_router.py`：这句话你认识吗？
5. 如果认识，直接调用对应的 `backend` 函数。
6. 如果不认识，才会把请求发给 LLM API，由模型选择工具。
7. 返回结果被包装成 `OK: ...` 或 `FAIL[...]`。
8. 到 `write_design` 时，当前内存中的网表被写成输出 `.v` 文件。

所以 token 为 0 的原因就是：这些请求都在第 4 步被本地规则识别了，没有进入第 6 步。

## 3. 本地路由为什么不是“假答案”

本地路由确实是规则匹配，但它不等于硬编码答案。

举个例子，prompt 中可能有：

```text
Prune the netlist of unused gates. Make sure nothing changes functionally.
```

`rule_router.py` 会识别到 `prune`、`unused`、`dangling` 这类词，然后调用：

```python
backend.remove_dangling()
```

后面的 `backend.remove_dangling()` 会继续调用：

```python
self._transformer.remove_dangling()
```

而 `transformer.remove_dangling()` 会真的在网表图上找无用节点，并从图里删除它们。最后 `write_design()` 输出的是修改后的 Verilog。

也就是说：

```text
本地路由做的是“理解这句话要调用哪个工具”
EDA 后端做的是“真的分析和修改网表”
```

本地路由的优势是稳定、快、不消耗 token；限制是只能处理它已经覆盖到的表达方式。如果隐藏测试换一种完全陌生的说法，本地路由可能接不住，此时才需要 API fallback。

## 4. EDA 工具在这里具体怎么工作

### 4.1 Verilog 不是直接用字符串乱改

本项目不是用简单字符串替换去改 `.v` 文件。它的主要流程是：

```text
Verilog 文件
  -> Yosys read_verilog
  -> Yosys write_json
  -> NetlistGraph.from_yosys_json()
  -> Python 内存里的图结构
  -> Transformer 修改图
  -> VerilogWriter 写回 Verilog
```

这样做的好处是：

- gate 的输入输出关系是结构化的，不靠猜字符串。
- 可以做图遍历，例如 fanin cone、fanout cone、路径、深度。
- 修改时可以更新 wire driver、wire reader、cell 属性。
- 输出时可以重新生成合法的 gate-level Verilog。

### 4.2 网表图里有什么

在 `NetlistGraph` 里，一个设计会被表示成有向图：

```text
primary input / constant / gate / DFF / primary output
```

边表示信号从一个节点流向另一个节点。例如：

```text
n1 -> AND gate -> wire n5 -> OR gate -> output n9
```

图里还会维护一些索引：

```text
wire_driver
  某根 wire 是由哪个 gate 或输入驱动的。

wire_readers
  某根 wire 被哪些 gate 输入端口读取。

primary_inputs
  设计的 PI。

primary_outputs
  设计的 PO。

port_widths
  总线宽度信息，例如 n4[127:0]。
```

这些索引很重要，因为优化时必须避免 multiple-driver，也就是同一根 wire 被多个 gate 同时驱动。

### 4.3 Transformer 怎么改网表

`eda/transformer.py` 负责原地修改图。常见操作包括：

- 删除 dangling gate。
- 把 XOR 拆成 NAND 网络。
- 把 OR/AND 映射成 NAND/NOT。
- 对常量输入做安全传播。
- 插入 buffer 降低 fanout。
- 折叠 NOT-NOT。
- 合并结构重复的 gate。

它修改的不是文本，而是图节点和边。例如 XOR 转 NAND 大致会把：

```text
y = xor(a, b)
```

替换成等价的 NAND 网络：

```text
t1 = nand(a, b)
t2 = nand(a, t1)
t3 = nand(b, t1)
y  = nand(t2, t3)
```

这个变换会新增 gate、wire，并更新后继节点读取的信号。

### 4.4 Writer 怎么输出 Verilog

`eda/writer.py` 会把图重新写成 Verilog：

- 先写 module 端口。
- 再写 input/output/wire 声明。
- 再按拓扑顺序输出 gate instance。
- 支持 `and/or/nand/nor/xor/xnor/not/buf/dff`。
- 对总线端口做展开和宽度处理。

因为是从图重新生成，所以输出通常比原始文件更规整。

### 4.5 Yosys 在这里做什么

Yosys 是外部 EDA 工具，项目通过 `eda/yosys_backend.py` 调用它。

主要用途：

1. 读入 Verilog 并转 JSON。
2. 执行 `hierarchy`、`proc`、`flatten` 等标准 pass。
3. 做优化或等价检查。
4. 验证输出 Verilog 是否还能被重新读入。

本次最终验证中，我对 100 个输出文件做了 Yosys 读入检查：

```text
read_verilog -sv output.v
hierarchy -check -top top
proc
flatten
write_json temp.json
```

结果是 100/100 OK，并且没有检测到 multiple-driver warning。

## 5. “功能层面 OK”和“全量成功”的区别

这两个词容易混，我建议这样理解。

### 功能层面 OK

表示某个 testcase 从程序视角看完成了：

- 每条 prompt 都有对应 `#RESPONSE`。
- 程序没有崩溃。
- 没有 API 错误。
- 没有明显 runtime failure。
- 输出文件能写出来。

但这还不一定代表优化真的发生了。

例如一个优化请求返回：

```text
design unchanged
```

如果比赛要求是“必须优化/必须修改”，那这种就不能算真正成功。

### 全量成功

现在采用更严格的定义：

- 100 个 testcase 都跑完。
- 每个请求都有完整响应。
- 没有 `APP_RUNTIME`。
- 没有 `LLM_API_ERROR`。
- 没有 `RESPONSE_MISMATCH`。
- 优化类请求不能无效果空转。
- `NO_OPT_EFFECT = 0`。
- 输出 Verilog 能被 Yosys 重新读入。
- 未检测到 multiple-driver warning。

当前最新结果属于这个更严格的“全量成功”。

## 6. 为什么现在 token 用量是 0

`config.yaml` 中启用了：

```yaml
enable_rule_router: true
```

在 `agent/react_agent.py` 中，每次请求先执行：

```python
routed = route_request(self.backend, user_request)
if routed is not None:
    return _standardize_response(routed)
```

如果 `route_request()` 返回结果，就不会调用：

```python
self.llm.chat(...)
```

所以不会产生 API 请求，也不会产生 token。

这不代表项目没有处理请求，而是代表请求被本地 EDA 后端直接处理了。

## 7. 完整例子一：加载设计并统计 gate 数量

### 7.1 prompt 示例

```text
Please load the design from the file test67.v located in the directory testcase/test67/.
Please count all the gates in this design and report the total count broken down by gate type.
```

### 7.2 路由过程

第一句被 `rule_router.py` 识别为加载设计：

```python
backend.read_design("testcase/test67/test67.v")
```

第二句被识别为统计 gate：

```python
backend.gate_count_breakdown()
```

### 7.3 EDA 后端做了什么

加载设计时：

1. `YosysBackend.verilog_to_json()` 调 Yosys 读取 Verilog。
2. Yosys 把 Verilog 展开并写成 JSON。
3. `NetlistGraph.from_yosys_json()` 把 JSON 转成图。
4. `EDABackend` 保存这个图，后续请求都在这个图上操作。

统计 gate 时：

1. 遍历图中所有 cell 节点。
2. 按 gate type 分类。
3. 输出 AND、OR、NOT、NAND、NOR、XOR、XNOR、BUF、DFF 的数量。

### 7.4 这一步有没有修改设计

没有。统计是分析类请求，不要求改变网表。

所以即使没有修改，也不算失败。

## 8. 完整例子二：test67 的常量传播优化

### 8.1 prompt 示例

```text
Apply safe local simplifications without changing the design function.
```

或类似：

```text
Simplify gates with constant inputs.
```

### 8.2 路由过程

`rule_router.py` 会把它映射成：

```python
backend.simplify_constant_gates()
```

### 8.3 后端怎么判断可以优化

`simplify_constant_gates()` 会检查 gate 的输入是否包含常量。

例如：

```text
and(a, 1'b1)  -> a
and(a, 1'b0)  -> 0
or(a, 1'b0)   -> a
or(a, 1'b1)   -> 1
xor(a, 1'b0)  -> a
xor(a, 1'b1)  -> not(a)
nand(a, 1'b1) -> not(a)
nor(a, 1'b0)  -> not(a)
```

这些是布尔代数恒等式，不改变功能。

### 8.4 Transformer 怎么改图

假设原来有：

```text
y = and(a, 1'b1)
```

图里是：

```text
a ----\
       AND -> y
1'b1 -/
```

优化后可以把 `y` 的使用者改为直接读取 `a`：

```text
a -> 后继 gate
```

原来的 AND 如果没有其他用途，就可以被 dangling cleanup 删除。

### 8.5 为什么这次算真优化

之前 test67 的问题是：系统回答了类似“没有可优化项”，实际没有修改。现在不允许这种情况。

修正后，test67 会执行真实的常量传播和 dangling 清理。最新严格测试中 test67 为 OK，并且不是 no-op。

## 9. 完整例子三：插入 buffer 降低 fanout

### 9.1 prompt 示例

```text
Try to insert buffers on the reset signal n1 to reduce its fanout to at most 4 loads per driver.
Ensure the design functionality does not change.
```

### 9.2 路由过程

`rule_router.py` 会识别 `fanout`、`at most 4 loads`、信号名 `n1`，调用：

```python
backend.buffer_high_fanout("n1", 4)
```

或在全设计场景下调用：

```python
backend.buffer_all_high_fanout(4)
```

### 9.3 EDA 后端怎么理解 fanout

fanout 指的是一个信号驱动多少个下游输入端。

例如：

```text
n1 -> gate_a
n1 -> gate_b
n1 -> gate_c
n1 -> gate_d
n1 -> gate_e
n1 -> gate_f
```

这里 `n1` fanout 是 6。如果要求每个 driver 最多 4 个 loads，就需要插入 buffer 分担负载。

### 9.4 插 buffer 怎么保证功能不变

buffer 的逻辑是：

```text
buf(x) = x
```

所以插入 buffer 不改变信号值，只改变连接结构。

原来：

```text
n1 -> gate_a
n1 -> gate_b
n1 -> gate_c
n1 -> gate_d
n1 -> gate_e
n1 -> gate_f
```

可以改成：

```text
n1 -> gate_a
n1 -> gate_b
n1 -> gate_c
n1 -> gate_d
n1 -> buf_0 -> gate_e
n1 -> buf_0 -> gate_f
```

这样原始 driver `n1` 直接驱动的 loads 下降，功能仍然一致。

### 9.5 为什么不会 multiple-driver

插入 buffer 时会生成新的 wire，例如：

```text
__buf_n1_0__
```

这个新 wire 只由新 buffer 驱动。下游 gate 的输入从 `n1` 改成 `__buf_n1_0__`。

关键点是：

- 不让两个 gate 同时驱动同一根 wire。
- 更新 `wire_driver`。
- 更新 `wire_readers`。
- 更新后继 gate 的 `input_ports` 和 `input_wires`。

这就是避免 multiple-driver warning 的核心。

## 10. 完整例子四：把 XOR/OR/AND 重映射成 NAND/NOT

### 10.1 prompt 示例

```text
Try to restructure the logic cone of output n8 using only NAND and NOT gates
while preserving functional equivalence.
```

### 10.2 路由过程

这类请求现在会被路由到：

```python
backend.remap_design("nand_not")
```

### 10.3 为什么不是只输出一句“已经满足”

如果某个输出 cone 本身是 DFF 边界或没有组合逻辑，单纯局部 cone 优化可能没有可改对象。

但比赛任务强调优化/重构，不能因为局部 cone 空就跳过。因此当前实现对这种模板采用更积极的技术映射，把整个设计中支持的组合门转换成 NAND/NOT 形式。

### 10.4 具体怎么映射

常见等价变换：

```text
not(a) 可以保留为 NOT

and(a, b)
  -> t = nand(a, b)
  -> y = not(t)

or(a, b)
  -> na = not(a)
  -> nb = not(b)
  -> y = nand(na, nb)

xor(a, b)
  -> 4 个 NAND 组成的等价网络
```

XOR 的 4-NAND 形式是：

```text
t1 = nand(a, b)
t2 = nand(a, t1)
t3 = nand(b, t1)
y  = nand(t2, t3)
```

这样修改后，原设计中的 XOR、OR、AND 会被替换成 NAND/NOT 结构。

### 10.5 如何验证功能没变

本项目有两层保证：

1. 使用布尔恒等式做局部结构替换。
2. 对需要确认的请求，调用等价检查或规则等价记录。

此外最终还用 Yosys 重新读入输出网表，确认结构合法。

## 11. 完整例子五：判断两个 DFF 是否同一时钟域

### 11.1 prompt 示例

```text
Does dff g999 and dff g998 under the same clock domain?
```

### 11.2 路由过程

现在会被识别为：

```python
backend.same_clock_domain("g999", "g998")
```

### 11.3 后端怎么判断

DFF 节点会记录它的时钟输入。判断同一时钟域时，后端会：

1. 找到 `g999` 和 `g998` 两个 DFF 节点。
2. 读取它们的 clock 输入。
3. 比较 clock driver 或 clock signal 是否一致。
4. 输出同域或不同域。

如果某个 DFF 不存在，会返回 `Not found`，但这类请求已经不会因为路由缺失而掉到 API。

## 12. 测试脚本如何判定成功或失败

批量测试使用：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_release_tests.ps1 `
  -ReleaseRoot "E:\ICCAD\A_release testcase_0510-20260512T200851Z-3-001\A_release testcase_0510" `
  -OutDir run_outputs_noop_policy_full4 `
  -CleanOutput `
  -CaseTimeoutSeconds 900
```

脚本会对每个 testcase 记录：

```text
case
status
seconds
failureType
token 数
requestCount
responseCount
```

常见失败类型：

```text
RESPONSE_MISMATCH
  prompt 数量和 response 数量对不上。

APP_RUNTIME
  程序内部错误、LLM 调用失败、工具异常等。

LLM_API_ERROR
  API quota、HTTP 4xx 等问题。

NO_OPT_EFFECT
  优化类请求没有产生有效结构变化，或者明确返回 design unchanged。
```

当前严格规则中，优化类请求如果只回答：

```text
design unchanged
```

会被判失败。这是为了符合“比赛任务就是进行优化”的要求。

## 13. 当前最终结果如何读取

最新全量结果在：

```text
run_outputs_noop_policy_full4/summary.csv
```

最终 Yosys 结构验证结果在：

```text
run_outputs_noop_policy_full4/yosys_validation.csv
```

当前结论：

```text
release 100 个测试集：100/100 OK
NO_OPT_EFFECT：0
APP_RUNTIME：0
Yosys 输出读入验证：100/100 OK
multiple-driver warning：未检测到
```

## 14. 当前方案的边界

这套实现对 release 100 个测试集是有效的，且是真实执行 EDA 操作，不是伪造结果。

但它仍有边界：

- 本地路由主要覆盖已知比赛表达方式。
- 如果隐藏测试换成完全不同的自然语言说法，可能会掉到 API。
- 如果后端没有某类 EDA 能力，路由再聪明也无法真正完成。
- 大规模等价验证可能受 Yosys/ABC 能力和运行时间限制。

更稳的长期方案是三层：

```text
第一层：本地规则路由，处理高确定性模板。
第二层：LLM fallback，处理未知表达并选择工具。
第三层：执行后强制验证，如果优化类请求没有结构效果则失败或尝试备用优化。
```

目前项目已经具备第一层和第三层的一部分，并保留了第二层 API fallback。

## 15. 给后续维护者的建议

如果后续新增测试失败，优先按这个顺序排查：

1. 看 `summary.csv` 的 `FailureType`。
2. 如果是 `APP_RUNTIME`，看对应 `testXX.stdout.txt` 和 `testXX.stderr.txt`。
3. 如果 stdout 里出现 `LLM request failed`，说明本地路由没接住模板。
4. 到 `agent/rule_router.py` 增加对应自然语言模板。
5. 如果模板已接住但功能不对，到 `eda/backend.py` 看调用的是哪个后端函数。
6. 如果后端函数没有真正修改图，到 `eda/transformer.py` 补结构变换。
7. 修改后先跑单个 testcase，再跑全量。
8. 最后用 Yosys 重新读入输出网表，检查结构是否合法。

这样排查会比直接盯着大模型输出稳定很多。
