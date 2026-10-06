# Architecture Simplification Plan

本计划用于逐步整理现有项目。目标是方案评估、数据评估和可实验验证的假设交付；湿实验执行与反馈作为可选扩展。

2026-10-06：第 1～3 步完成后，按后续请求依次实施第 4～8 步。下方目录已按现有职责落地，没有提前创建空计算模块。基线、变更边界和验收记录见 [首批执行记录](architecture_simplification_steps_1_3.md) 与 [后续执行记录](architecture_simplification_steps_4_8.md)。本计划的迁移映射保留旧路径，供核对使用；当前入口以执行记录和主架构文档为准。

## Target Layout

```text
backend/protocol_optimization_agent/
  agents/
    orchestrator.py
    pipeline.py
    protocol_analysis.py
    data_analysis.py
    protocol_optimizing.py
    actions.py
  analysis/
    protocol/
      input_loader.py
      normalization.py
      semantic_annotation.py
      feature_discovery.py
      feature_groups.py
      graph_builder.py
      feature_compiler.py
      validation.py
      comparison.py
      audit.py
      query_engine.py
    phenotype/
      h5ad_tools.py
      representation.py
      external_tools.py
    optimization/
      association.py
      assessment.py
      proposals.py
      contracts.py
      experiment_design.py
      comparability.py
      readiness.py
    visualization/
  science/
    models.py
    state.py
    ingestion.py
    dataset.py
    identity.py
  runtime/
    __init__.py
    context.py
    archive.py
    dataset_registry.py
    access_control.py
    llm_client.py
    config.py
    io.py
    paths.py
  interfaces/
    api.py
    langgraph_server.py
    server_auth.py
    cli.py
    protocol_analysis_cli.py
    protocol_query_cli.py
    scientific_state_cli.py
    run_archive_cli.py
  extensions/
    experimental_feedback/
      lifecycle.py
  schemas.py
  state.py
backend/tests/
evals/
frontend/
```

树中 graph_builder、feature_compiler、association、assessment、feature_groups、identity 等是按当前已有职责提取的目标模块，不应提前创建空文件。schemas.py 是分析输入/输出模型，science/models.py 是跨运行科学实体，迁移初期保留两者区别。state.py 是固定流水线执行状态，可在流水线迁移时一同整理。每个实际 Python 包包含 __init__.py。

## Step 1: Establish A Behavioral Baseline

- 在 protocol_agent 环境记录当前测试结果和依赖版本。先检查已有失败，避免将旧问题归因于迁移。
- 选择已有固定输入和输出作为回归样例。保留矩阵列名、列顺序、数值、缺失语义、单位及源证据映射。
- 记录图 ID、CLI 命令、API 路由、环境变量以及实际返回的输出路径。
- 归档输出中的 run ID、时间戳和绝对路径可随运行变化；比较时只对明确列出的非语义字段作处理，不能整体忽略 provenance。
- 目录迁移阶段不调整算法、评分、门槛或产物 schema。

验收：有一份可重跑的行为基线，以及明确的已知失败列表。使用现有测试，缺少关键入口覆盖时才补测试。

## Step 2: Select The Main Execution Path

- 主对话入口保留 orchestrator 图；protocol_chat 是可选的 PA 专用入口，复用查询工具。
- 固定流水线保留为批处理入口，将 orchestrator.py 中的 ProtocolOptimizationAgent 迁移到 agents/pipeline.py；不要把需要可复现的批处理强制改成 LLM 调度。
- 所有入口调用相同 PA/DA/PO 实现。API、CLI 负责参数、认证和响应适配。
- workflow.py 当前为空骨架。核查外部导入后，移入 examples/legacy 或删除；同步清理 README 和仅验证空骨架的测试。
- tools/scoring.py 的未实现适配器先核查调用；无调用时可移入示例或删除。真实成熟度功能的补齐另作功能任务。

验收：文档明确交互与批处理两条路径；不存在被介绍为可执行业务流程的空骨架。

## Step 3: Consolidate Shared Rules

- 提取 analysis/protocol/feature_groups.py，统一 PA 与 query_engine 的特征分类、分组顺序与排名。
- 逐项核对现有规则差异，例如 ordered_reagent_program；确定期望行为后编写针对差异的回归测试。
- 统一规则接口，响应字段仍由各调用方适配。不要为了共用代码改变已有输出 schema。
- subgraphs.py 的描述与真实图应有一致性检查；能从实际图取得的拓扑不要再手工维护一份。独立保留确有必要的说明文字。

验收：同一特征在 PA 报告与查询结果中使用同一分类依据；规则只有一处维护。

## Step 4: Move Runtime And Scientific Modules

| Current | Target |
| --- | --- |
| runtime.py | runtime/context.py |
| run_archive.py | runtime/archive.py |
| dataset_registry.py | runtime/dataset_registry.py |
| access_control.py | runtime/access_control.py |
| llm_client.py | runtime/llm_client.py |
| config.py | runtime/config.py |
| io.py | runtime/io.py |
| scientific_models.py | science/models.py |
| scientific_state.py | science/state.py |
| scientific_ingestion.py | science/ingestion.py |
| scientific_dataset.py | science/dataset.py |

- 先检查每个模块的导入、运行时延迟导入与 __file__ 路径计算，再移动。
- runtime.py 与 runtime/ 同名。迁移后用 runtime/__init__.py 临时导出 RunContext 等旧公开符号，随后内部导入逐步改为 runtime.context；不要同时保留两个实现。
- 其他旧模块需要兼容时只保留明确的转发导入，并记录移除条件，避免永久维持两套业务代码。
- config.py、context.py、archive.py 等若使用 parents[N] 计算项目根路径，搬迁后必须修正；源码归档和缓存代码指纹也要更新。
- 先保持现有数据库路径、schema、ID 算法及存量状态可读，不进行数据库格式迁移。

验收：模块可导入、原有状态可读取、输出仍写到授权位置，归档能够找到实际源码。旧入口仍可用或已明确同步更新。

## Step 5: Extract Scientific Computation

| Current | Target |
| --- | --- |
| input_loader.py | analysis/protocol/input_loader.py |
| normalization.py | analysis/protocol/normalization.py |
| semantic_annotation.py | analysis/protocol/semantic_annotation.py |
| feature_discovery.py | analysis/protocol/feature_discovery.py |
| audit.py | analysis/protocol/audit.py |
| query_engine.py | analysis/protocol/query_engine.py |
| tools/h5ad_tools.py | analysis/phenotype/h5ad_tools.py |
| tools/external_data_tools.py | analysis/phenotype/external_tools.py |
| optimization_orchestration.py | analysis/optimization/readiness.py |
| scientific_reasoning.py: Phenotype IR | analysis/phenotype/representation.py |
| scientific_reasoning.py: comparability | analysis/optimization/comparability.py |
| scientific_reasoning.py: design_experiments | analysis/optimization/experiment_design.py |

- PA 中先提取 protocol 图构建与特征编译，再提取独立审计计算；保留节点名称、执行次序和最终产物。
- PO 中提取关联计算与方案评分；Agent 节点只收集输入、调用计算并更新状态。
- optional interpretation 可以与表型表示暂时同模块，确有独立复杂度后再拆分。
- stable_id 属于科学实体身份的共享规则，迁入 science/identity.py，避免纯计算通过数据库模块获取该函数。
- 工具的 LangChain 包装负责声明和调用，实际计算落在 analysis。具有不同外部运行方式的工具不强制统一成新框架。

验收：固定输入下矩阵、数值结果与关键证据语义一致；单独调用计算模块不需要启动聊天服务器。

## Step 6: Make Entry Adapters Thin

- orchestrator_agent.py 的业务编排迁到 agents/orchestrator.py，action_space.py 迁到 agents/actions.py。
- API、LangGraph Server、server_auth 与 CLI 移到 interfaces；协议专用对话实现按实际职责迁为入口适配或复用 agents 的业务逻辑。
- 同步更新 pyproject.toml 的 console scripts、langgraph.json 和 langgraph.authenticated.json 的模块路径，以及启动脚本、前端配置和文档。
- 保持现有命令名、图 ID 与 API 路由不变；迁移的是实现位置。
- API/CLI 不重复实现 protocol 比较、数据分析、科学状态摄取或候选计算。

验收：原命令、两种 LangGraph 部署配置及 API 路由仍能找到实际实现；接口结果契约不变。服务器联网验证另按运行环境执行。

## Step 7: Isolate Optional Experimental Feedback

- 优先通过显式调用隔离审批、performed、反馈和准入能力，再决定是否移动文件。
- FeedbackAdmission、ExperimentFeedback 及生命周期实现可迁入 extensions/experimental_feedback；共享 Hypothesis、Evidence 和候选身份继续留在 science。
- 避免 science 顶层隐式导入所有扩展。由 CLI/API 或明确的扩展调用入口组装能力。
- 数据库已有记录类型和历史数据继续可读，不因迁目录删除已存实验记录。
- 方案评估、数据评估和假设生成不要求反馈扩展先执行。拆分任务门槛属于单独的行为改动，必须与目录迁移分开验证。

验收：三类主任务可独立运行，显式实验反馈入口仍能访问旧记录。

## Step 8: Finish Regression And Documentation

- 按实际改动运行相关测试；全部迁移完成后再运行完整测试集和已配置的静态检查。
- 核对 config 根路径、注册数据路径、科学数据库、源码归档、缓存指纹、API/CLI 输出路径。
- tests 主要验证计算及状态行为；evals 保存科研任务案例、期望证据、基线配置和评测结果，测试与科研评测不混用。
- 更新 current_project_architecture.md、README、项目讲解及部署文档。只保留当前实现的主流程图。
- 清理仓库内部对旧模块的导入；外部兼容转发有明确移除时间或版本。

验收：使用者能从一个入口找到三类主任务；核心计算结果保持一致，所有迁移后的公共入口有效，旧历史产物仍可解释。

## Incremental Delivery

每步单独形成可审阅变更。建议顺序：基线 -> 入口说明/空骨架 -> 共用规则 -> runtime/science -> 计算提取 -> 入口迁移 -> 可选扩展 -> 全量回归。初次迁移不新建通用服务框架、不增加 Agent、不替换数据库，不将算法变化混入文件搬迁。
