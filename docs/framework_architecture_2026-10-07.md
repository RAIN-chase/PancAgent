# 当前完整架构与论文 Framework

核对日期：2026-10-07。依据当前代码及本轮 PA、DA、PO、Orchestrator 专项文档；未运行项目分析、测试、在线模型或文献接口。图像导出仅运行独立绘图脚本，不导入项目业务代码。

## 一句话定位

一个面向胰腺/胰岛类器官的证据驱动科研分析系统，由一个目标验收式 Orchestrator 调度三个领域执行器，把结构化培养方案和单细胞表型转为可追溯比较、科学解释及可交由外部实验验证的假说。

英文定位：**An evidence-grounded research agent for pancreatic/islet organoid protocol assessment, phenotype analysis, and testable optimization hypotheses.**

## 完整分层

| 层 | 当前实现 | 职责与边界 |
| --- | --- | --- |
| 用户交互 | `frontend/`，Next.js + agent-chat-ui | 对话、流式回复、工具调用、分析进度及报告展示；不负责科学计算。 |
| 接口 | `interfaces/`，LangGraph Server + FastAPI + CLI | 对话统一进入 `orchestrator` 图；直接 API 和批处理 CLI 复用领域实现；认证绑定服务端身份。 |
| 科研协调 | `agents/orchestrator.py`、`analysis_flow.py`、`actions.py` | 结构化规划、动作可用性与参数检查、结果绑定、执行、目标验收、证据结论、预算和报告。 |
| 领域执行 | `agents/protocol_analysis.py`、`data_analysis.py`、`protocol_optimizing.py` | 分别处理 protocol 表征、表型计算、联合证据及假说；没有各自的自主重规划循环。 |
| 科学计算 | `analysis/protocol/`、`phenotype/`、`optimization/` | 确定性编译和数值计算、源约束语义解析与机制解释；按领域集中实现。 |
| 科研状态 | `science/` | 有版本的 Protocol、Phenotype、Evidence、Claim、Hypothesis、Experiment、分析数据集、候选与历史；SQLite 持久化，不是因果模拟器。 |
| 运行治理 | `runtime/` | 用户/项目隔离、授权路径、输入快照、结果哈希、运行档案、模型调用、配置和可选 PubMed 适配器。 |
| 外部验证扩展 | `extensions/experimental_feedback/` | 显式审核、实验记录、反馈质控与入库；默认关闭，不会自动培养类器官或测序。 |

源代码与配置在 `backend/`、`frontend/`、`configs/`；输入在 `inputs/`；运行证据在 `runs/`。运行档案和科研记忆是两种对象：前者复现某次执行，后者保存跨执行的科研对象与历史。

## 两条执行路径

### 对话路径

`研究问题 → agent-chat-ui → LangGraph Server → Orchestrator → 所需 PA / DA / PO 动作 → Review → 报告 → 对话界面`

不要求每个问题遍历全部 Agent：比较原始方案主要调用 PA；问数据质量/表达量主要调用 DA；提出整合假说调用 PO 并检查 PA/DA 证据。问题中的对象、终点和科研参数不明确时应澄清。

### 批处理路径

`配置 + 输入 → agents/pipeline.py → PA → DA → PO → 版本化产物`

这是固定批处理流程；不能与对话中的动态任务选择混为一谈。二者使用相同领域计算模块，不维护另一套数值算法。

## 四个 Agent 的核心模块

下列英文名是适合论文图的职责分组，并非新建类或额外 Agent。

### Orchestrator：Goal-Reviewed Scientific Coordinator

**图内模块：** Configure & Scope、Plan & Result Binding、Registered Action Executor、Evidence & Goal Reviewer、Report & Archive。

实际图：`configure → plan → execute → review → execute / finalize`。

- Configure：认证与用户/项目上下文、输入/代码快照、运行档案和预算。
- Plan：LLM 或明确给定计划，描述问题类型、对象、指标、步骤、依赖、预期产物和待澄清信息；注册表是唯一可执行能力表。
- Result Binding：上一步已保存结果的哈希与 JSON pointer 绑定到下一步参数，自动建立依赖；不得覆盖明确科研参数。
- Execute：运行授权的领域动作；重复调用复用已验证结果；局部失败只阻断依赖步骤。
- Review：从实际结果判定目标覆盖；LLM 提议下一步和支持/反对证据绑定的结论；代码检查引用、范围和证据等级。工具成功不等于问题已回答。
- Finalize：保存 `scientific-analysis-report-v3`；数值从源 JSON 引用读取，包含结论、局限、反证、证据缺口、澄清问题和停止原因。
- 科研记忆：自动提供有限的词法相关历史假说与反证，不声称穷尽语义检索。

主要文件：`agents/orchestrator.py`、`agents/analysis_flow.py`、`agents/actions.py`、`science/reporting.py`、`science/state.py`。

### PA：Source-Grounded Protocol Representation

**图内模块：** Protocol Structure & Step Semantics、Deterministic Feature Compiler、Protocol Comparison & Evidence Audit。

实际图：`input gate → schema validator → timeline → factor normalization → semantic interpreter → matrix → feature discovery → relationships → differences → audit → export`。

- 当前正式输入是上游导出的 `protocols.jsonl`；本文献到 protocol 的抽取不是当前系统已集成的能力。
- 保留阶段、时间、试剂事件、原始操作、状态、条件、否定/可选语句、参数范围和来源位置。
- 步骤语义走规则和有预算的可选 LLM，输出解释标签与源文本锚点；不能补造剂量、日期或把未解析内容视为不存在。
- 特征注册表描述列；确定性编译器计算矩阵值、单位、缺失状态和来源。可选 LLM 可提议特征，不能直接填写矩阵数值。
- 全量原文/结构化表示、步骤语义与数值矩阵并行保留；不是把所有细节压进一个矩阵。
- 比较包含特征、因素、阶段、原始步骤及保守语义对齐；候选对齐不是生物学等价证明。
- 核心产物：`protocol_feature_matrix.csv`、`protocol_feature_provenance.json`、`semantic_step_annotations.jsonl`、步骤/覆盖/发布审计和比较结果。

默认配置：步骤语义最多八次模型尝试；在线提供者及凭据仍是必要条件。标准化、阶段语义及特征发现的独立 LLM 预算为零，可显式启用。

主要文件：`agents/protocol_analysis.py`、`analysis/protocol/step_semantics.py`、`analysis/protocol/` 中的编译、查询及审计模块。

### DA：Task-Driven Phenotype Analysis

**图内模块：** Task Resolver & Phenotype Tools、Maturity & Metric Validation、Phenotype Representation & Interpretation。

实际图：`input gate → resolve tasks → dataset inventory → execute tasks → audit → phenotype representation → export`。

- 输入 h5ad 与明确 manifest：protocol/dataset/sample/condition/study 链接、细胞选择、注释、表达来源及独立重复元数据。
- 显式选任务，不因配置出现某参数就自动计算，也不在内部自主重规划。
- 可用计算：组成、基因表达、marker、gene program、差异表达、Alpha/Beta/Delta 的 Early/Late maturity。
- 区分技术 QC、任务数值有效性、统计适用性；细胞数不是生物学重复。缺少设计信息时只返回描述性结果。
- `phenotype_ir.json` 是数值表型表示；可选 `phenotype_interpretation.json` 是单独解释层，不改数值。该解释模块可保留不同解释和审核需求。
- reference similarity 仍为未完成能力；GRN、轨迹、通信不能画成当前已实现的通用引擎。
- 核心产物：已选任务 CSV、Phenotype IR、QC、执行计划、任务结果、验证和局限。

主要文件：`agents/data_analysis.py`、`analysis/phenotype/h5ad_tools.py`、`replicate_analysis.py`、`maturity_scoring.py`、`endpoint_comparison.py`、`representation.py`。

### PO：Evidence-to-Hypothesis Synthesis

**图内模块：** Joint Evidence Builder、Endpoint Association & Study Sensitivity、Mechanism Evidence Integrator、Hypothesis & Candidate Design、Critique & Conditional Selection。

实际图：`integration gate → phenotype targets → join → association → mechanistic evidence → similar protocol reference → proposal → experiment design → audit → export`。

- PA 提供培养条件、特征、步骤和来源；DA 提供原始终点、样本/重复/研究身份和验证；PO 检查身份版本及各终点的可比性。
- 每个观测保留值、单位、方法、尺度、归一化、培养/采样上下文、源引用和 JSON 路径。
- 重复测量先在生物学重复内汇总，再在 protocol/study 内汇总；原始终点优先，复合分数仅为相对辅助信息。
- Pearson 筛选、共干预共线性标志和整项研究移除敏感性属于探索性分析，不是因果调整、显著性证明或干预效果预测。
- 机制证据区分 protocol 使用事实、phenotype 观测和机制文献；可选 PubMed 摘要检索及 LLM 解释保留支持、反对、竞争解释与检索审计。
- 生成包含 baseline、版本、阶段、干预、对照、终点、方向、适用条件与可证伪标准的假说/候选。
- 排名依赖明确、有来源的 improvement/information/cost/risk 序数评价；缺失评价可以保持 unranked。不是成功概率。
- 相似方案仅作为已观测参考，候选效果估计为空；新方案必须外部验证。
- 核心产物：`analysis_dataset.json`、`joint_evidence.json`、`endpoint_associations.json`、`mechanism_evidence.json`、`similar_protocol_reference.json`、`next_experiment_candidates.json`、方案与风险报告。

主要文件：`agents/protocol_optimizing.py`、`analysis/optimization/evidence.py`、`association.py`、`mechanisms.py`、`assessment.py`、`proposals.py`、`experiment_design.py`、`runtime/literature.py`。

默认机制设置为零模型调用、零文献查询；图中的 Optional LLM / PubMed 表示可配置能力，不表示每次执行必定调用。

## 数值与证据的一致性

1. 上游原始 protocol 细节与矩阵并存：没有数值列的内容仍可被追溯、查询或标为未量化。
2. PA/DA 的工具计算拥有数值权威；语义/机制解释不是新测量。
3. PO 使用保留身份和范围的样本级联合证据，而非只靠一个总分关联方案。
4. Orchestrator 的结论引用具体结果和字段，检查哈希；展示值由保存的 JSON 读取。
5. 前端可只展示问题相关子集，但必须区分展示范围和完整档案；不得声称摘要涵盖所有步骤。
6. 引用校验与覆盖审计不等于科学正确性、因果成立或语言理解百分之百准确。

## Framework 图的解读

- 实线：当前动作调度、证据整合或结果回流。
- PA 与 DA：并列领域；彼此没有必然的数据依赖。
- PA/DA 指向 PO：证据依赖，不代表每个对话都必须运行三者。
- Review 回 Execute：增加证据/修改分析步骤的受预算控制循环，不是模型训练或权重自进化。
- 底部共享层：科研记录、来源版本、档案与验证边界；不是额外的 Memory Agent。
- 虚线：默认关闭的外部实验审核与反馈扩展；当前工作可以在输出可检验假说处结束。
- 图中的职责模块是现有代码的论文级分组，不是新增 agent 或真实代码类名。

## 图注

**English caption.** Framework of the evidence-grounded protocol research agent for pancreatic/islet organoids. A goal-reviewed Orchestrator maintains a bounded planning, execution and review loop and dispatches registered actions to three domain executors. PA preserves source-grounded protocol structure and step semantics while deterministically compiling quantitative features. DA performs explicitly requested phenotype analyses and exports numerical evidence with task-specific validity and statistical applicability. PO integrates protocol interventions, observed endpoints and mechanism sources to generate competing, falsifiable hypotheses and conditionally ranked validation candidates. Shared versioned scientific records and hash-verified run artifacts support traceability; model-authored interpretations do not replace numerical results. The dashed path denotes optional researcher-led external experimentation and QC-passed feedback admission, not autonomous wet-lab execution.

**中文图注。** 胰腺/胰岛类器官证据驱动科研 Agent 框架。Orchestrator 通过有预算的规划、执行、复核循环调度 PA、DA 和 PO，分别实现来源锚定的方案表示、任务驱动的表型计算及联合证据支持的可证伪假说生成。版本化科研状态、结果引用和运行档案贯穿全过程。LLM 负责规划和解释，确定性工具负责数值。虚线表示可选的研究者主导外部实验与质控反馈，不代表系统自主开展培养和测序。

## 产物与重绘

- `figures/protocol_research_framework_2026-10-07.svg`：矢量主图，文字保留为可编辑文本。
- `figures/protocol_research_framework_2026-10-07.pdf`：适合排版和投稿的矢量 PDF。
- `figures/protocol_research_framework_2026-10-07.png`：高分辨率展示图。
- `figures/protocol_research_framework_design_reference_2026-10-07.png`：生成式视觉参考稿，不作为精确拓扑的权威来源。
- `figures/render_framework_2026_10_07.py`：仅绘图的复现脚本，使用 protocol_agent 环境的 matplotlib，不调用项目分析。

当前图按代码核对，不依据 `current_project_architecture.md` 中残留的历史状态或 Planned Work 段落推断已实现能力；本次不修改这些历史文档。
