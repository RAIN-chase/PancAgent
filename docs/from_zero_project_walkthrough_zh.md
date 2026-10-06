# 从空目录到当前科研 Agent 项目

核对日期：2026-10-05。本教程按当前工作区代码讲解，并非重建历史的准确时间线。PA 是 Protocol Analysis，DA 是 Data Analysis，PO 是 Protocol Optimizing。示例是虚构教学数据，不构成实验方案或生物学验证。

## 先跑一个真实的小流程

在仓库根目录，用项目已有环境运行：

```powershell
cd E:\workspace\Pancreas\Agents_test
$env:PYTHONPATH = "backend"
& "D:\anaconda3\envs\protocol_agent\python.exe" examples/tutorial/run_protocol_walkthrough.py
```

也可在自己的环境中先运行 `python -m pip install -e ".[dev]"`，再运行 `python examples/tutorial/run_protocol_walkthrough.py`。这条安装命令会访问包源；本次没有重新安装依赖。现有环境的绝对路径是本机路径，换机器要替换。

示例不需要 API key。它复用正式 loader、PA 内部 LangGraph 和 query engine，默认使用 OfflineLLMClient。每次创建新运行归档，最后打印 OUTPUT_DIR；后续查询必须使用这个实际目录。

你应该看到：两份 protocol、两个试剂事件、A=0.1 与 B=0.05 ug/ml、比较成功、原始证据和 release_ready=true。源数据是 100 与 50 ng/ml；矩阵自动统一为 ug/ml。这个单位变化正好说明计算层的职责。

依次打开：

1. `examples/tutorial/protocols.jsonl`：两行，每行是一份完整 protocol。
2. `examples/tutorial/run_protocol_walkthrough.py`：从输入到产物的调用顺序。
3. OUTPUT_DIR 中的 `protocol_feature_matrix.csv`：计算结果。
4. `protocol_feature_provenance.json`：结果的来源。
5. `matrix_release_gate_report.json`：是否允许发布该矩阵。

文件里的 high confidence 和 comparison_ready=true 只用于已知的虚构测试输入。处理真实数据时必须有审核依据，不要通过提高标签绕过质量门。

## 一、组成项目的基本概念

| 名称 | 含义 | 本项目位置 |
| --- | --- | --- |
| Python 环境 | 某套 Python 及其安装的库 | 本机 protocol_agent 环境 |
| 包 | 可以被 import 的代码集合 | backend/protocol_optimization_agent |
| 模块 | 一个 Python 文件 | runtime/config.py、analysis/protocol/input_loader.py |
| 函数 | 接收输入并完成一项工作 | load_protocol_analysis_inputs |
| 类 | 把配置和相关方法放在一起 | ProtocolAnalysisAgent |
| 数据契约 / schema | 规定字段、类型和有效值 | schemas.py、science/models.py |
| 工具 | Agent 可以调用的函数接口 | interfaces/langgraph_server.py 中的 @tool 函数 |
| 节点 | 图里的一步计算 | PA 的 _node_* 方法 |
| State | 当前图执行中的工作数据 | ProtocolAnalysisState、OrchestratorState |
| 产物 / artifact | 写到文件里的结果及其身份 | CSV、JSON、报告 |
| API | 程序之间请求服务的接口 | interfaces/api.py；LangGraph Server 也提供接口 |
| 前端 | 浏览器中的展示与交互 | frontend |

节点、工具和 Agent 不同：节点是执行步骤；工具是可调用接口；Agent/工作流负责组织一组操作。一个节点完全可以只运行 Python，不调用 LLM。项目里许多带 Agent 名称的领域组件实际上是受控分析流程。

## 二、如果从空目录开始，先搭什么

学习顺序是：普通计算 → 数据契约 → 可执行工作流 → 查询工具 → LLM 调用 → 多领域调度 → 服务与界面 → 持久科学记录 → 评测。每一阶段留下可以运行和核对的结果。

### 阶段 1：最小 Python 项目

空目录先建这些内容即可：

```text
project/
  pyproject.toml
  backend/
    protocol_optimization_agent/
      __init__.py
      io.py
      input_loader.py
      schemas.py
  backend/tests/
  examples/
```

pyproject.toml 记录项目名、Python 版本、依赖、包所在目录和命令行入口。当前项目设置 package-dir 指向 backend，因此 `from protocol_optimization_agent...` 能导入里面的代码。`pip install -e .` 是可编辑安装：导入指向工作区，修改源代码后通常不需要重新安装；更改入口和依赖则需要更新安装。

先理解 `Path`、`dict/list`、函数、类、JSON、异常和 import，再读图编排。遇到 import 失败先确认 Python 环境与包安装，不要修改业务代码来解决环境问题。

验收：在正确环境中可以导入包，读入一份小 JSONL，输出 protocol_id。

### 阶段 2：规定输入和输出

当前唯一正式 protocol 输入是目录里的 `protocols.jsonl`。JSONL 每行一个 JSON 对象；stage 和 reagent 嵌套在 protocol 中。h5ad manifest 是另一类输入，仅运行 PA 时不需要 h5ad。

analysis/protocol/input_loader.py 把正式记录投影为内部 protocol_records、factor_events、source_identity_rows。投影是字段和身份的确定性转换；不从证据句子猜剂量，也不自动把阶段时间当作试剂暴露时间。

schemas.py 检查结构，quality gate 检查数据能否进入分析。结构正确不代表证据充分。例如 concentration 可解析，但 unit 缺失时，不能声称浓度可比较。

从头设计时先写两个样例，再规定字段、缺失语义、来源和校验规则。详见 [正式输入契约](protocol_input_contract.md)。旧 JSONL、外部 factor CSV、SQLite 的输入描述不能替代当前契约。

验收：重复 ID、错误嵌套、非布尔 readiness 被拒绝；零值保留；缺失数值不由 LLM 补写。

### 阶段 3：先写普通计算

输入 → 检查 → 标准化 → 特征计算 → 比较 → 导出，每一步先能作为普通函数工作。当前计算位于 analysis/protocol/，例如 normalization.py、graph_builder.py、feature_compiler.py、validation.py、comparison.py；IO 位于 runtime/io.py。PA 节点负责组织这些函数，不再内嵌全部科研计算。

比如 Activin A 剂量统一为 ug/ml，再计算最大浓度；记录原值、原单位、标准值、标准单位和证据。`presence`、`count`、`duration` 是不同特征，不能仅凭试剂出现就推断使用时长。

最终表是 Protocol × Feature：每行一份 protocol，每列一个计算定义。FeatureSpec 是列的规则说明；LLM 可以建议新规则，但列值由代码计算。先确定最小几个特征，再增加规则注册与审计。

验收：虚构输入的数值与独立预期一致，结果可以解释到原事件。

### 阶段 4：把步骤组织成 PA 图

阅读 `agents/protocol_analysis.py` 时按以下顺序：ProtocolAnalysisState → __init__ → run → build_langgraph → 一个 _node_* 实现。

run 初始化 state 并调用已编译图。build_langgraph 注册节点与边；节点接收 state，返回更新字段。compile 生成可执行对象，invoke 启动一次执行。

```text
质量门 → schema 校验 → 时间线 → 因子标准化 → 语义解释
  → 特征矩阵 → 特征发现 → 关系与差异 → 审计 → 导出
```

State 是本次流程的工作台，不等于长期记忆。一般字段按更新替换；消息列表在对话图中通过 add_messages 合并，按消息 ID 处理更新。TypedDict 提供类型说明，不等于运行时的严格数据验证；Pydantic 负责适用边界的验证。

不要只画图：每个节点必须确实完成工作。空的 workflow.py 骨架已删除；实际固定 pipeline 位于 agents/pipeline.py。旧 orchestrator.py 导入兼容层已删除，不能把图或文件存在当作端到端科学计算的证明。

验收：每个步骤有结果与日志；失败/排除的输入能在报告中找到。

### 阶段 5：先构建查询层，再接 LLM

analysis/protocol/query_engine.py 读取 PA 已生成产物，提供 find_protocols、compare_protocols、get_evidence_for_feature 等确定性方法。查询不等于重新读论文或重跑分析。

interfaces/langgraph_server.py 用 @tool 将这些能力暴露给聊天模型。模型看到工具名称、说明和参数 schema，返回工具调用请求；ToolNode 执行函数，把结果作为工具消息回传。`bind_tools` 只是让模型知道能用哪些工具，实际执行由程序完成。

```text
configure → agent → 有 tool_calls？
                       ├─ 有 → tools → agent
                       └─ 无 → finalize → END
```

InjectedState 将输出目录等运行上下文从 state 注入工具，减少模型猜路径的机会。Prompt 负责行为说明；文件授权和质量门仍由代码执行。

CLI/config 的 llm 配置与聊天环境变量是两条配置路径。当前 default_config.json 明确选择在线 provider，并非离线默认配置；教学脚本不读取该文件，使用 OfflineLLMClient。聊天配置详见现有 .env.example；不要把真实 key 写入数据或提交记录。

验收：先直接调用 query engine 得到正确结果，再验证模型选择正确工具并忠实总结。

### 阶段 6：接入 DA 与 PO

DA 是另一个可独立运行的领域图。analysis/phenotype/h5ad_tools.py 放实际数据计算，agents/data_analysis.py 放执行顺序、汇总、验证和输出。这样同一个计算工具能用于图、API 与测试；旧 tools/ 兼容转发已删除，调用方使用规范计算模块路径。

manifest 用 protocol_id、dataset_id、sample_id、condition_id 和配置列名绑定实际数据。多个 manifest 行可以选择同一个 h5ad 的不同细胞；必须验证筛选确实选到预期细胞，以及 X、raw、layer 的基因索引与表达尺度。

已有 inspection、composition、expression、marker、轻量 DE 等实现。成熟度与参考相似性仍有 placeholder；GRN、轨迹等部分能力依赖外部工具配置。CSV 文件存在不代表里面有可靠数值。

PO 读取 PA 特征与 DA 表型，通过输入门、表型目标、连接、关联、证据排序、候选修改、experiment_design、审计和导出生成下一轮实验候选。函数名称里的 counterfactual 不代表已实现因果反事实估计。

验收：DA 的 technical_qc 通过且优化终点对应的 task_validity 有效，才能宣称满足该任务的执行要求；po_ready 已删除。仍需要科学可比性检查，不能因文件齐全而混合不可比的研究。

### 阶段 7：增加外层调度

当前有三个不同入口：

| 入口 | 运行方式 | 用途 |
| --- | --- | --- |
| agents/pipeline.py | 按固定顺序调用 PA、DA、PO | 批处理 pipeline |
| interfaces/langgraph_server.py 的 protocol_chat | LLM ↔ 协议查询工具 | 协议问答 |
| agents/orchestrator.py 的 orchestrator | LLM ↔ SubAgent/注册科学操作工具 | 跨领域对话调度 |

agents/orchestrator.py 的图是 configure → orchestrator → subagents → orchestrator，并在没有工具调用时 finalize。它由模型选择工具；“subagents 节点”并不自动表示三个领域 Agent 在并行讨论。

analysis/optimization/readiness.py 检查上游准备情况。agents/orchestrator.py 校验缓存，runtime/context.py 处理运行上下文、输出位置与证据响应封装。每个工具应返回可消费的结构，包括使用的工具、特征、证据、限制及产物身份。

验收：查协议调用 PA；查表达调用 DA；优化先确认上游满足要求；上游失败能清楚返回原因。

### 阶段 8：服务和网页

langgraph.json 注册两张可对外使用的图。LangGraph Server 运行图并流式传输状态，frontend 使用 SDK 接收消息与工具调用。interfaces/api.py 是额外的 FastAPI 服务入口；聊天前端连接 LangGraph Server 时并不强制经过该 API。

在已配置模型、CLI 和前端依赖后，可以用现有脚本本机启动：

```powershell
.\scripts\start_langgraph_server.ps1 -HostAddress 127.0.0.1 -Port 2024 -OutputDir "替换为教学脚本打印的实际OUTPUT_DIR"
```

另一个终端：

```powershell
.\scripts\start_frontend.ps1 -HostAddress 127.0.0.1 -Port 5173 -LangGraphApiUrl "http://127.0.0.1:2024" -AssistantId "protocol_chat"
```

先使用 protocol_chat 验证单一协议问答，再将 AssistantId 改为 orchestrator 学习跨领域调度。前端脚本会写 frontend/.env.local；此处给出操作方法，本次没有启动服务或改写环境文件。认证部署另读 authenticated_deployment.md。

验收：用户能看见工具调用，访问正确输出快照，错误能显示，用户之间的数据受代码授权约束。

### 阶段 9：区分三种“记忆”

| 存储 | 记录什么 | 生命周期 |
| --- | --- | --- |
| 图 State / 消息 | 本次工作进度、工具回应 | 一次执行；跨轮恢复需要相应保存机制 |
| RunArchive | 输入快照、模型/工具调用、结果、错误 | 每次 run 独立 |
| ScientificWorldModel | 协议、表型、证据、假设、实验与版本 | 同一研究 project 跨 run |

runtime/archive.py 和 runtime/context.py 保存每次计算的来路。science/models.py 定义科学实体，science/state.py 用 SQLite 保留记录与历史，science/ingestion.py 从经过检查的产物接入这些记录。显式审批、执行记录、反馈与准入在 extensions/experimental_feedback/lifecycle.py；核心分析不会自动执行实验扩展。

project_id 表示同一研究，run_id 表示一次运行，trace_id 用于追踪过程。把这三个身份分开，才能多次研究同一问题，同时复查每次计算。

实验候选有审核、实施、观察与评估状态；实际湿实验由人完成。记录反馈与把反馈数值纳入优化数据集是不同步骤，后者需要独立审核绑定。见 scientific_world_model.md。当前已存在闭环记录机制，但不能由此声称成熟度评分、因果估计或湿实验自动化均已完成。

### 阶段 10：让系统成为可评估的研究方法

软件测试验证 loader、计算、图调度和版本行为。论文评测检验研究任务上的准确性、证据正确率、可实施性及成本。二者都需要，但不是同一个问题。

建立独立专家核对的任务集，固定数据、工具、模型与预算，比较固定流程、单 Agent 工具调用、当前多领域系统。按论文/数据来源划分评测，防止同源泄漏；移除关键审计/反馈能力做消融。湿实验效果用实际实验验证。

## 三、继续完善当前项目的顺序

| 优先级 | 可交付改进 | 验收证据 |
| --- | --- | --- |
| 1 | 本教程与可运行小示例 | 无 key 运行、单位转换正确、证据能回查；本次已添加 |
| 2 | 固定一组真实输入和预期输出 | 协议身份、stage、单位与 provenance 由专家核对 |
| 3 | 成熟度指标正式实现与校准 | 明确细胞群、基因集、尺度、缺失处理，区分转录指标与功能成熟度 |
| 4 | 参考相似性正式实现与校准 | 参考版本、共有基因、归一化与批次处理固定；独立留出样本验证 |
| 5 | DA → PO 真实小规模整合案例 | readiness、endpoint 可比性、样本/重复身份均通过；失败有原因 |
| 6 | 评测集、基线与消融 | 相同数据/模型/工具/预算下可重复比较 |
| 7 | 下一轮实验和反馈接入 | 基线与实施版本、对照、样本、结果和审核一一对应 |
| 8 | 根据实测瓶颈改善界面与部署 | 用户任务、运行时间、稳定性与维护成本有测量 |

成熟度基因集、参考图谱和统计设计涉及科学选择，不能在没有定义时填入任意算法就宣布功能完成。当前先补教学入口和可复现小流程；后续以经过明确方法定义的单项分析为单位推进。

## 四、第一次动手的练习

1. 运行示例，找到 OUTPUT_DIR 和矩阵。
2. 解释为何输入 100 ng/ml，矩阵得到 0.1 ug/ml。
3. 从该单元格查回 evidence_quote。
4. 把 B 浓度改成 25 ng/ml，先预测输出应为 0.025 ug/ml，再同步修改脚本中已知预期并运行。
5. 将某份 protocol 的 comparison_ready 改成 false，观察质量门与断言失败，理解排除原因；练习后恢复教学输入。
6. 不修改计算函数，尝试用 query CLI 对同一个输出快照做 collection、compare 和 evidence 查询。

```powershell
python -m protocol_optimization_agent.protocol_query_cli --output-dir "实际OUTPUT_DIR" collection
python -m protocol_optimization_agent.protocol_query_cli --output-dir "实际OUTPUT_DIR" compare tutorial_a tutorial_b
python -m protocol_optimization_agent.protocol_query_cli --output-dir "实际OUTPUT_DIR" evidence tutorial_a all_reagent_activin_a_max_concentration
```

每次只改一个因素，保留预测和实际结果。先掌握这一条输入到证据的路径，再扩展到 DA、多 Agent 和网页。

LangGraph 概念可对照官方 [Graph API 参考](https://reference.langchain.com/python/langgraph/graphs) 和 [add_messages 参考](https://reference.langchain.com/python/langgraph/graph/message/add_messages)。本教程中的项目行为以当前代码为准，环境版本变化时先用现有测试确认行为。
