# 有计划的科学分析流程

更新日期：2026-10-06。唯一对话入口为 LangGraph 图 `orchestrator`；PA 专用聊天图与聊天 API 已删除。没有新增 Agent，也没有接入尚待确定的 Protocol_extraction workflow 或最终干预 IR。

2026-10-07 更新：新增问题验收、引用结论、历史反证检索和结果字段绑定，详见 [目标验收与复核契约](orchestrator_goal_review.md)。以下旧验收统计是此前版本记录，本轮统计见新文档。

## 执行与停止

```text
configure -> plan -> execute -> review
                       ^          |
                       +----------+
                                  +-> finalize (report + archive)
```

- 循环只保存 `request`、`plan`、`step_results`、`budget`、`stop_reason`；认证上下文、目录和消息仍为服务基础设施字段。`plan` 保存问题、目标指标、对象、步骤、输入、依赖、预期输出、证据缺口及替代解释。LLM 提议，Pydantic 和 Action Registry 校验；未实现、partial、deferred Action 不能进入可执行计划。
- `step_results` 保存执行状态、可用性、缺口、结果引用和是否新增证据。依赖步骤必须可用，才执行下游步骤。失败不通过虚构替代数值补齐。
- `review` 首先由代码检查结果状态、问题对象与终点覆盖、可比性、任务有效性、重复证据和预算；模型返回引用绑定的结论、替代解释、澄清问题和经过同样校验的新步骤。
- 相同 Action、参数、输入版本和工具/源码版本不重复执行。检索的匹配分数变化不算新增科学证据。
- `stop_reason` 包含 `completed`、`evidence_insufficient` 及细分原因、`budget_exhausted:<维度>`。重复步骤复用已有结果，不再全局停止；失败只阻断其依赖步骤。正常停止和模型错误都保存部分报告，不以递归超限代替停止条件。未解决的子问题使报告保持部分可用。
- 无模型时使用确定性路由和单步计划；跨任务迭代需要已配置的模型，或服务端给出 `request.plan`。本轮测试使用离线模型替身，未验证真实供应商返回计划的稳定性。

服务端可通过 `request={"plan": <以下对象>}` 传入计划；执行后删除请求中的计划副本。生产环境的数据身份和授权路径仍由认证层解析，计划不能声明新的工具或审批权限。

```json
{
  "question": "比较 p1 与 p2 的协议差异",
  "target_metrics": [],
  "objects": ["p1", "p2"],
  "steps": [{
    "id": "compare",
    "action_id": "protocol.compare",
    "inputs": {"protocol_id_a": "p1", "protocol_id_b": "p2"},
    "depends_on": [],
    "expected_outputs": ["feature_diff", "factor_diff", "stage_diff", "event_step_diff", "evidence_refs"]
  }]
}
```

预算默认值：6 个调度 Action、4 轮执行、6 次编排模型调用、300 秒、16000 个模型 token；硬上限分别为 12、8、12、900、32000。成本上限默认不设置，可通过 `max_model_cost` 指定供应商返回的成本单位。服务端或调用者只能在硬上限内调小/设置预算，不能继承客户端提交的已用计数。

**预算边界：**工具调用计数是编排调度的 Action，不是每个内层计算函数；内层 PA/DA/PO 调用仍有完整 trace。时间在操作之间检查，不强行终止已经开始的 h5ad 计算。模型 token/成本使用已返回的供应商用量计数，属于调用后检查，不是供应商端的绝对计费上限；没有用量或必要成本信息时停止后续模型调用，并明确标记未知。暂未覆盖子 Agent 内部独立模型调用的统一预算，也未实现后台作业硬取消。

## 可调用任务

| Action | 当前能力 | 限制 |
| --- | --- | --- |
| `protocol.assess` | 检查矩阵、完整事件、结构化步骤、来源与单位缺口、审计信息 | 未接入上游提取工作流，不虚构缺失步骤 |
| `protocol.compare` | 矩阵、因子/阶段差异、完整事件/步骤、来源 | 描述性比较，不推断因果 |
| `data.assess` | 读取/检查 h5ad 或已有 DA 产物，返回任务验证及重复结构 | 有 manifest 时重新执行标准 DA；无数据时返回不可用 |
| `phenotype.cell_composition` / `gene_expression` / `differential_expression` | 任务级确定性计算与验证 | 数值适用性和统计适用性分开 |
| `protocol.phenotype_compare` | 指定终点的有效观察与比较队列 | 未知尺度不合并；上下文不同先要求分层或校正；不排名 |
| `hypothesis.generate` | 可证伪假设、反对证据、替代解释、适用条件与未知预期幅度 | 草案，不审批、不执行；证据指针未经额外验证时不视为已确认引用 |
| `experiment.propose` | 仅消费已保存的 PA/DA 结果；上游依赖由 Orchestrator 调度 | 科学门槛仍严格；无法选实验不影响独立假设生成 |

成熟度已接入 counts → normalize_total(1e4) → log1p → Scanpy Early/Late 模块分数工具，支持 Alpha、Beta、Delta，详见 [成熟度工具](maturity_scoring_tool.md)。参考相似度仍为 `partial`；协议提取仍为 `deferred`。注册表中的 available 表示有可调用实现，不意味着所有数据都满足科学前提。

## DA 统计边界

`data_analysis_validation.json` 增加 `technical_qc`、`metric_status`、`task_validity`、`requested_tasks`、`statistical_applicability`。细胞组成不再因成熟度/参考相似度尚未实现而整体无效。验证契约升级为 `data-analysis-validation-v2`，删除 `po_ready`。PO 按优化目标终点的实际来源选择任务验证；无显式终点时要求成熟度任务有效。未知来源或缺失目标终点阻断数值优化，其他无关指标失败不阻断已有效的任务。旧版验证文件需要重新运行 DA，不能沿用旧 readiness 判断。

数据清单可保留 study、sample、生物学重复、细胞系、培养批次、测序批次。h5ad inspection 的 `design_metadata` 记录所选细胞的实际元信息、不同标识数量及缺失；仅有 ID 不证明独立重复。

重复层面探索 DE 的配置示例：

```python
h5ad_config = {
    "backed": True,
    "requested_tasks": ["differential_expression"],
    "expression_scale": "log1p",
    "sample_column": "sample_id",
    "biological_replicate_column": "biological_replicate_id",
    "replicate_kind": "biological",
    "study_column": "study",
    "cell_line_column": "cell_line",
    "culture_batch_column": "culture_batch",
    "sequencing_batch_column": "sequencing_batch",
    "differential_expression": {
        "groupby": "condition", "group_a": "treated", "group_b": "control",
        "genes": ["INS", "GCG"]
    }
}
```

对每个生物学重复聚合目标基因均值，再对独立重复均值执行 Welch 探索检验，并对**本次比较中有效的请求基因**执行 BH 校正。记录重复均值、细胞数、重复数、p/q 值和检验家族。细胞数不作为独立样本数；两个重复仅是计算可行的下限，不保证统计效能。

DE Action 与工具注册版本为 `2.0.0`，明确区分旧细胞级探索检验和当前重复层面的语义。大型 h5ad 应显式设置 `backed=True`；这不保证所有 layer/raw 或外部分析工具都不加载完整矩阵，本轮没有进行超大文件内存/性能压测。

缺少重复/样本身份、未确认 biological 类型、未知 study/细胞系/批次、组间批次完全混杂、重复跨组重叠，或原始 counts/未知尺度时，只返回描述性差异，p/q 留空。已有归一化并不自动满足所有统计假设；当前仍未校正协变量，不支持配对设计、负二项 counts 模型、DESeq2/muscat 或因果推断。多批次部分混杂仍须研究者检查。大量细胞不能弥补生物学重复不足。

采用现成统计实现：[SciPy Welch t-test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ttest_ind.html)、[SciPy BH/FDR](https://docs.scipy.org/doc/scipy-1.11.0/reference/generated/scipy.stats.false_discovery_control.html)。更完整的多样本分析方法背景见 [Bioconductor OSCA](https://www.bioconductor.org/books/3.19/OSCA.multisample/multi-sample-comparisons.html)，不代表本项目已实现其模型。

## PA 与 PO 的科学输出

PA 比较新增 `event_step_diff`：完整双方事件、已保存的结构化步骤、仅在一方出现的事件及来源。矩阵零值不自动表示“明确未使用”；未报告、存在明确否定证据、无法确定性解析必须区别处理。正式输入仍只有 `inputs/Protocols inputs/protocols.jsonl`。本轮没有改变特征编译算法，也没有让模型直接增加最终数值列。

假设新增机制解释、支持/反对证据、替代解释、适用条件、预期方向/未知幅度及证伪条件。既往反驳、已测试记录和假设状态保留。没有合格可评分候选时返回 `unranked_hypothesis_set`，`rank=null`；效用只是显式序数评价的加权结果，不是成功概率。

## 产物与展示

每次独立 run 在原归档之上增加：

```text
analysis_plan.json
step_results.json
intermediate/<result_id>.json
final_report.json
final_report.md
```

保留原有 `config.json`、`input_manifest.json`、模型/prompt/源码记录、`tool_calls.jsonl`、`evidence.json`、错误与审计清单。运行结果先保存，再读取用于复核和展示。报告区分事实、描述性差异、探索性关联、待验证假设与无法判断。

`quantitative_bindings` 只保存引用，不复制 `value` 或结果预览。它保存每个数值的结果 ID、JSON Pointer 字段路径、产物路径/SHA256、输入版本、Action 参数、工具和代码版本。它包含结果中的计数和参数，不意味着这些数值都属于测量结果。Markdown 根据引用重新读取并校验原结果 JSON；前端显示最终消息和字段引用，下载的是引用清单，不是另一套数值副本；模型的自由文本不能覆盖报告数值。被拒绝的模型原文仅在 trace 中保留，不以校验异常原文污染最终科学报告。

最终报告不作为独立循环状态保存；归档中的 `final_report.json` 是报告权威来源，终止 AI 消息携带同一引用清单（`additional_kwargs.analysis_report`）。已有聊天界面增加计划、步骤状态、Action 调用计数、停止原因、证据缺口、绑定结果引用和报告 JSON 下载。服务入口及启动脚本不变，需重启后端和前端加载新代码。报告合成当前采用确定性模板；尚未加入任意自由文本解释的逐句引用校验。

## 验收与复现

```powershell
conda run -n protocol_agent python -X utf8 -m pytest -q
conda run -n protocol_agent python -m ruff check backend/protocol_optimization_agent backend/tests evals/architecture_baseline/run_baseline.py
cd frontend
pnpm exec tsc --noEmit
```

前端独立回归：在项目根目录运行 `node evals/planned_analysis/verify_progress.mjs`；缺少 Playwright 自带浏览器时可设置 `PLAYWRIGHT_CHANNEL=chrome`，使用已安装的 Chrome。生成的桌面/手机截图和下载结果在忽略的 `runs/planned_analysis/ui_checks/` 中。此测试不等于真实 LangGraph 服务与 LLM 的联网端到端验证。

冻结基线不重录，只明确投影掉新增的 `comparison.event_step_diff`；旧矩阵、数值、单位、缺失与证据仍逐项比较，新增完整事件另行断言。回归覆盖证据不足、失败依赖、重复调用/重复证据、未实现工具、预算、缺失用量、单研究标记、反对证据、数值与归档一致性、重复检验及尺度/批次阻断。

## 删除的兼容入口

删除根目录转发模块、旧 `tools/` 与 `visualization/` 转发、批处理 `orchestrator.py` 别名、`scientific_reasoning.py` 聚合转发及 `runtime/__init__.py` 符号重导出。仅保留规范模块路径；历史迁移映射仍保存在 `evals/architecture_baseline/module_relocations.json` 供查阅。外部 Python 调用方也需据此修改导入。

例如 `from protocol_optimization_agent.runtime import RunContext` 改为 `from protocol_optimization_agent.runtime.context import RunContext`；批处理类从 `agents.pipeline` 导入，聊天图从 `agents.orchestrator` 导入。CLI 命令名与 `orchestrator` 图 ID 保持不变。`protocol_chat` 图与 `/protocol-query/chat` 已删除；实验审批与反馈 API 默认关闭，显式启用后保留原路由。旧 `python -m protocol_optimization_agent.cli` 不再支持，应使用命令 `protocol-optimization-agent` 或规范模块 `interfaces.cli`。

模型仅输出结构化计划和复核 JSON，不再接受 tool-call 格式的计划提议；服务端通过 `execute_scientific_action` 执行。能力描述与离线路由来自 Action Registry。旧直接工具提议、`describe_subagents` 与重复工具列表已删除。PA/DA/PO 仅执行各自领域任务，固定计算图不包含自主规划回路；PO 不再接受原始 PA/DA 输入，也不在内部触发上游 Agent。缺少协议提取工作流时明确停止，不把协议评估当作协议编译替代品。

工具结果统一使用 `orchestrator-evidence-envelope-v2`，删除重复的 `result` 与 PO 顶层数值别名；数据位于 `result_preview`，有效性位于 `validation`。调度器保存结果后为返回消息添加 `result_refs`，不在文件内声明自身 SHA256。

本次全量回归 159 项通过、0 跳过，保留 1 项既有 Starlette/httpx 弃用警告；最终封装/编排/科学状态/归档专项复测 50 项通过。冻结科学基线再次通过，并验证只用一个 `protocol.compare` Action 从主入口完成真实编译产物的比较，不调用 DA/PO。Ruff、TypeScript、桌面/手机 Playwright 展示与引用下载、wheel 打包检查通过；wheel 不包含已删除的兼容模块。前端现有 Thread/Stream 仍有 3 项既有 ESLint warning。

全项目 mypy 仍有 32 项既有错误，未将其描述为全部通过。真实 LLM 联网稳定性、大型 h5ad 性能压测与硬取消仍未覆盖。
