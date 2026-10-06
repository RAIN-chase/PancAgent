# SubAgent Development Coordination

2026-10-06 migration note: historical change logs below retain their original paths. For new work, use `agents/orchestrator.py`, `interfaces/langgraph_server.py`, `analysis/phenotype/h5ad_tools.py`, `analysis/optimization/readiness.py`, `runtime/context.py` and `science/`; old modules only forward imports. The complete map is in `evals/architecture_baseline/module_relocations.json`, with acceptance checks in [steps 4-8](architecture_simplification_steps_4_8.md).

This document provides copy-ready prompts for separate Codex conversations that work on individual SubAgents. Each SubAgent conversation should update this file, or a linked SubAgent-specific markdown file, after finishing a change.

## Coordination Rules

- Work in the same repository: `E:\workspace\Pancreas\Agents_test`.
- Read `docs/current_project_architecture.md` before editing code.
- Keep generated large data out of git: real `.h5ad`, large `inputs/`, large `runs/`, downloaded external packages, and `.env` should not be committed.
- Preserve the Orchestrator contract: user question -> Orchestrator -> callable SubAgent tool(s) -> evidence-grounded answer.
- Do not make causal protocol claims from observational associations. Protocol optimization outputs must remain hypotheses requiring experimental validation.
- Add or update tests for each changed behavior.
- Before handoff, run the focused tests for the changed area and record the result in the update log.
- If changing shared schemas, output filenames, or tool return contracts, update this document and `docs/current_project_architecture.md`.

## Current Target Architecture

```text
User question
  ↓
Orchestrator Agent
  ├─ Protocol Analysis Agent
  │    protocol lookup, protocol comparison, factor/stage events, evidence, feature matrix, visualization
  ├─ Data Analysis Agent
  │    h5ad loading, QC, DE, gene expression, marker scores, composition, maturity, reference similarity
  └─ Protocol Optimizing Agent
       protocol + h5ad integration, candidate ranking, optimized protocol hypotheses, validation plan
  ↓
Final answer with tool provenance and limitations
```

Expected routing:

- Protocol-only questions call only Protocol Analysis Agent.
- Data-only questions call only Data Analysis Agent.
- Optimization questions call Protocol Analysis Agent, Data Analysis Agent, then Protocol Optimizing Agent, unless cached upstream outputs are valid and the user did not request a rerun.

## Prompt: Protocol Analysis Agent Session

Copy the following prompt into the Protocol Analysis Agent conversation.

```text
你是一名资深 agent 架构师和生物信息学专家。你当前负责完善 Protocol Analysis Agent。

项目目录：
E:\workspace\Pancreas\Agents_test

请先完整阅读：
- docs/current_project_architecture.md
- docs/subagent_development_coordination.md
- backend/protocol_optimization_agent/agents/protocol_analysis.py
- backend/protocol_optimization_agent/query_engine.py
- backend/protocol_optimization_agent/langgraph_server.py
- backend/tests/test_protocol_analysis_agent.py
- backend/tests/test_langgraph_server.py

目标：
让 Protocol Analysis Agent 成为 Orchestrator 可稳定调用的协议分析子 agent。它需要回答 protocol 差异、protocol 查找、stage/factor 事件、feature matrix、provenance/evidence、heatmap 可视化等问题。

重点任务：
1. 检查当前 ProtocolAnalysisAgent.run() 的输入、输出文件和 QueryEngine 是否完全一致。
2. 强化 protocol comparison 的确定性结果，包括：
   - stage timeline 差异
   - reagent/factor 差异
   - normalized family 差异
   - semantic strategy module 差异
   - graph-derived workflow/sequence 差异
   - evidence/provenance 可用性
3. 增强 QueryEngine 对自然语言问题的解析，但不要让 LLM 直接生成最终 matrix value。
4. 确保 compare_protocols、get_protocol_events、get_feature_evidence、query_matrix、explain_cell、heatmap tools 的返回字段稳定，方便 Orchestrator 汇总。
5. 对缺失 protocol、缺失 evidence、空 matrix、低置信度输入提供清晰 status 和 limitations。
6. 更新或补充测试，覆盖 Orchestrator 调用 Protocol Analysis Agent 的关键路径。

约束：
- 不要提交真实输入数据、大型 runs 输出、.env。
- 不要重构无关前端。
- 不要把 observational protocol differences 写成 causal conclusions。
- 若修改输出 contract，必须同步更新 docs/current_project_architecture.md 和 docs/subagent_development_coordination.md。

完成后请：
1. 运行相关测试：
   pytest backend/tests/test_protocol_analysis_agent.py backend/tests/test_langgraph_server.py backend/tests/test_orchestrator_agent.py
2. 运行：
   git diff --check
3. 在 docs/subagent_development_coordination.md 的 Update Log 中追加记录：
   - 日期
   - 会话名称：Protocol Analysis Agent
   - 修改文件
   - 新增/改变的能力
   - 测试结果
   - 遗留问题
```

## Prompt: Data Analysis Agent Session

Copy the following prompt into the Data Analysis Agent conversation.

```text
你是一名资深 agent 架构师和单细胞生物信息学专家。你当前负责完善 Data Analysis Agent，使其能够在 Linux 服务器端处理大规模 h5ad，并被 Orchestrator 按需调用。

项目目录：
E:\workspace\Pancreas\Agents_test

请先完整阅读：
- docs/current_project_architecture.md
- docs/data_analysis_agent_tools.md
- docs/subagent_development_coordination.md
- backend/protocol_optimization_agent/agents/data_analysis.py
- backend/protocol_optimization_agent/tools/h5ad_tools.py
- backend/protocol_optimization_agent/tools/external_data_tools.py
- backend/protocol_optimization_agent/orchestrator_agent.py
- backend/tests/test_data_analysis_agent.py
- backend/tests/test_orchestrator_agent.py

目标：
把 Data Analysis Agent 从 placeholder contract 推进到可执行的 h5ad 分析子 agent。用户问 DE、基因表达量、marker expression、cell composition、maturity、reference similarity 时，Orchestrator 应只调用 Data Analysis Agent，并返回数据驱动结果和限制说明。

优先任务：
1. 设计并实现真实 h5ad 工具函数的第一版，优先支持：
   - h5ad manifest validation
   - dataset inventory / obs columns / var gene inspection
   - query_gene_expression
   - differential_expression
   - marker_score
   - cell_type_composition
2. 使用 scanpy/anndata/pandas/numpy/scipy 实现，但要做依赖缺失 fallback：
   - dependency_available
   - h5ad_file_missing
   - obs_column_missing
   - gene_not_found
   - insufficient_groups_for_de
3. 处理超大规模 h5ad 的服务器端运行场景：
   - 支持 backed="r" 或分块读取策略
   - 不要默认把完整 X matrix 转成 dense
   - 输出聚合表，不输出超大中间对象
   - 允许通过 config 限制 max_cells、target_genes、groupby、layer、raw/use_raw
4. 定义 Data Analysis Agent 可被 Orchestrator 调用的稳定返回 contract：
   - status
   - question
   - selected_tool
   - output_files
   - result_preview
   - limitations
   - provenance: dataset_id, protocol_id, h5ad_path, obs columns, layer/raw
5. 增加 data_analysis_agent tool dispatch：
   - 根据问题类型选择 gene expression / DE / marker / composition / summary
   - 先支持确定性关键词路由，后续再接 LLM planner
6. 更新 docs/data_analysis_agent_tools.md，写明服务器部署、输入 manifest、输出文件、限制和示例命令。

建议输出文件：
- gene_expression_summary.csv
- differential_expression.csv
- marker_scores.csv
- cell_type_composition.csv
- h5ad_qc_report.json
- data_analysis_query_result.json

测试要求：
1. 用小型 synthetic AnnData fixture 测试，不提交大 h5ad。
2. 覆盖：
   - 缺依赖 fallback
   - gene expression query
   - DE group comparison
   - missing gene / missing obs column
   - Orchestrator -> Data Analysis Agent 路由
3. 运行：
   pytest backend/tests/test_data_analysis_agent.py backend/tests/test_orchestrator_agent.py
   git diff --check

完成后请在 docs/subagent_development_coordination.md 的 Update Log 中追加记录：
- 日期
- 会话名称：Data Analysis Agent
- 修改文件
- 新增/改变的能力
- 测试结果
- 服务器端运行注意事项
- 遗留问题
```

## Prompt: Protocol Optimizing Agent Session

Copy the following prompt into the Protocol Optimizing Agent conversation.

```text
你是一名资深 agent 架构师、生物信息学专家和实验方案优化顾问。你当前负责完善 Protocol Optimizing Agent。

项目目录：
E:\workspace\Pancreas\Agents_test

请先完整阅读：
- docs/current_project_architecture.md
- docs/subagent_development_coordination.md
- backend/protocol_optimization_agent/agents/protocol_optimizing.py
- backend/protocol_optimization_agent/optimization_orchestration.py
- backend/protocol_optimization_agent/orchestrator_agent.py
- backend/protocol_optimization_agent/query_engine.py
- backend/tests/test_protocol_optimizing_agent.py
- backend/tests/test_orchestrator_agent.py

目标：
让 Protocol Optimizing Agent 能在 Protocol Analysis Agent 和 Data Analysis Agent 输出都可用时，生成 evidence-labeled、风险受控、可实验验证的 protocol 优化假设。

重点任务：
1. 明确输入 readiness contract：
   - protocol_feature_matrix.csv
   - normalized_factor_events.jsonl
   - protocol_feature_provenance.json
   - data_analysis_protocol_summary.csv
   - marker_scores.csv
   - gene_expression_summary.csv
   - differential_expression.csv
   - maturity_scores.csv
   - similarity_scores.csv
2. 实现或增强 phenotype target builder：
   - beta-like maturity
   - endocrine composition
   - off-target reduction
   - stress/proliferation penalties
   - reference similarity
   - user-defined marker/gene programs
3. 实现保守 association/ranking：
   - small-n 时禁止过度统计解释
   - Pearson/Spearman 只作为 hypothesis screen
   - 明确 correlation_not_causality
   - 使用 mechanism prior 对 pathway/timing/reagent/workflow feature 分类
4. 生成 optimized protocol proposal：
   - proposed changes
   - supporting protocol evidence
   - supporting h5ad evidence
   - expected phenotype direction
   - confidence
   - risk
   - required validation experiment
5. 支持用户给定 new_protocol_features 时做 counterfactual scoring：
   - nearest-neighbor protocol similarity
   - feature deltas
   - predicted phenotype direction only if evidence sufficient
6. 确保 Orchestrator 调用 optimization 时：
   - 若 PA/DA 输出缺失，返回 upstream_rerun_required
   - 若输出存在，可运行或复用 cached optimization
   - 返回 markdown + json + validation plan

约束：
- 不要把相关性写成因果。
- 不要生成不可追溯的实验建议。
- 不要要求用户把大 h5ad 提交到 git。
- 输出必须可被 Orchestrator 简洁汇总。

测试要求：
1. 使用小型 synthetic protocol matrix 和 data outputs。
2. 覆盖：
   - missing upstream outputs
   - cached outputs reuse
   - optimization run
   - low evidence confidence behavior
   - new protocol counterfactual scoring
3. 运行：
   pytest backend/tests/test_protocol_optimizing_agent.py backend/tests/test_orchestrator_agent.py
   git diff --check

完成后请在 docs/subagent_development_coordination.md 的 Update Log 中追加记录：
- 日期
- 会话名称：Protocol Optimizing Agent
- 修改文件
- 新增/改变的能力
- 测试结果
- recommendation safety policy
- 遗留问题
```

## Prompt: Orchestrator Agent Session

Copy the following prompt into the Orchestrator Agent conversation if a separate session is used for routing and integration.

```text
你是一名资深 agent 架构师。你当前负责完善 Orchestrator Agent，使其成为 agent-chat-ui 的主入口。

项目目录：
E:\workspace\Pancreas\Agents_test

请先完整阅读：
- docs/current_project_architecture.md
- docs/subagent_development_coordination.md
- backend/protocol_optimization_agent/orchestrator_agent.py
- backend/protocol_optimization_agent/langgraph_server.py
- backend/protocol_optimization_agent/optimization_orchestration.py
- backend/tests/test_orchestrator_agent.py
- langgraph.json

目标：
实现稳定的用户问题统筹层：
- protocols 差异问题 -> Protocol Analysis Agent
- DE、基因表达、marker、h5ad 问题 -> Data Analysis Agent
- protocol 优化问题 -> Protocol Analysis Agent + Data Analysis Agent + Protocol Optimizing Agent

重点任务：
1. 强化 route classifier：
   - protocol_analysis
   - data_analysis
   - protocol_optimization
   - ambiguous / needs_clarification
2. 定义 Orchestrator tool-call policy：
   - protocol-only: one subagent
   - data-only: one subagent
   - optimization: upstream readiness check -> PA/DA if needed -> PO
3. 给每个 SubAgent tool 返回统一 envelope：
   - subagent
   - status
   - question
   - selected_operation
   - result_preview
   - output_files
   - limitations
   - provenance
4. 支持 cached outputs：
   - 默认复用已有输出
   - 用户要求重跑时设置 force_*_rerun
5. 优化最终回答：
   - 简洁中文科学总结
   - 标注使用的 subagents
   - 标注数据来源、输出路径、限制
   - 对 optimization 明确实验验证要求
6. 确认 `langgraph.json` 暴露 `orchestrator`，并考虑前端默认图是否应切换到 orchestrator。

测试要求：
1. 使用 fake chat model 测 tool calls。
2. 使用 offline fallback 测确定性路由。
3. 覆盖：
   - protocol question
   - data question
   - optimization question
   - missing upstream output
   - cached optimization output
4. 运行：
   pytest backend/tests/test_orchestrator_agent.py backend/tests/test_langgraph_server.py
   git diff --check

完成后请在 docs/subagent_development_coordination.md 的 Update Log 中追加记录：
- 日期
- 会话名称：Orchestrator Agent
- 修改文件
- 新增/改变的能力
- 测试结果
- 与 SubAgent contract 的兼容性
- 遗留问题
```

## Prompt: Linux Server Runtime Session

Copy the following prompt into a separate conversation dedicated to Linux deployment and large h5ad testing.

```text
你是一名资深 Linux/ML infra 工程师和单细胞分析工程师。你当前负责让 ProtocolOptimizationAgent 在服务器端运行大规模 h5ad 分析。

项目目录在本地是：
E:\workspace\Pancreas\Agents_test

服务器目标形态：
- GitHub 拉取代码
- 真实 h5ad 保存在服务器本地数据目录，不进 git
- Python venv 或 conda 环境安装 scanpy/anndata/scipy/pandas/numpy
- Orchestrator / Data Analysis Agent 读取 manifest 中的服务器本地 h5ad 路径

请先阅读：
- docs/current_project_architecture.md
- docs/data_analysis_agent_tools.md
- docs/subagent_development_coordination.md
- pyproject.toml
- .gitignore

目标：
设计并补充服务器运行说明、配置模板和安全约束，确保项目可以在 Linux 服务器端 pull 后运行。

重点任务：
1. 检查 `.gitignore` 是否排除：
   - .env
   - inputs/
   - runs/
   - *.h5ad
   - external_tools/ 下载目录或大型 vendor 源码
   - Python/Node 缓存
2. 增加服务器配置说明：
   - clone/pull
   - venv/conda setup
   - pip install -e ".[dev,h5ad]"
   - env vars
   - manifest 示例
   - pytest
   - LangGraph server 启动
3. 增加服务器 manifest 模板，路径使用 Linux absolute path 示例。
4. 给出大 h5ad 运行原则：
   - backed mode
   - sparse matrix
   - no dense full matrix by default
   - chunking / max cells
   - output aggregation
5. 不要提交真实 h5ad 或服务器私有路径。

测试要求：
- pytest
- git diff --check
- 如果没有 Linux 环境，只做文档和配置模板，不假装已经服务器验证。

完成后请在 docs/subagent_development_coordination.md 的 Update Log 中追加记录：
- 日期
- 会话名称：Linux Server Runtime
- 修改文件
- 新增/改变的能力
- 测试结果
- 服务器端未验证项
- 遗留问题
```

## Update Log

Use this format for every SubAgent session.

```text
### YYYY-MM-DD - <Session Name>

- Scope:
- Files changed:
- Capabilities added or changed:
- Tests run:
- Results:
- Contract changes:
- Limitations:
- Next actions:
```

### 2026-09-04 - Coordination Prompts

- Scope: Added shared prompts for separate SubAgent development conversations.
- Files changed: `docs/subagent_development_coordination.md`
- Capabilities added or changed: Defined coordination rules, SubAgent-specific prompts, expected routing, testing requirements, and update-log format.
- Tests run: Not applicable; documentation-only change.
- Results: Ready for prompts to be copied into Protocol Analysis, Data Analysis, Protocol Optimizing, Orchestrator, and Linux Server Runtime sessions.
- Contract changes: None.
- Limitations: This document coordinates work but does not implement missing Data Analysis numeric h5ad tools by itself.
- Next actions: Each SubAgent session should append its own update record after making code or documentation changes.

### 2026-09-04 - Architecture Governance

- Scope: Added project-level governance for runtime isolation, evidence envelopes, deterministic matrix ownership, deployment configuration, and observability.
- Files changed: `backend/protocol_optimization_agent/runtime.py`, `backend/protocol_optimization_agent/orchestrator_agent.py`, `backend/tests/test_orchestrator_agent.py`, `.env.example`, `docs/architecture_governance.md`, `docs/current_project_architecture.md`, `docs/subagent_development_coordination.md`
- Capabilities added or changed: Orchestrator-facing SubAgent tools now return `orchestrator-evidence-envelope-v1` with `used_tools`, `used_features`, `evidence_refs`, `confidence_caveats`, provenance, output files, `trace_id`, `run_id`, and `user_id`; runtime context writes run manifests and tool-call logs under scoped user/run directories.
- Tests run: `pytest backend/tests/test_orchestrator_agent.py`
- Results: Passed locally before this log entry was written.
- Contract changes: New Orchestrator evidence envelope is the required response boundary for frontend-specific renderers and LLM summaries.
- Limitations: LLM latency/cost tracking and protocol-specific frontend components are documented as required next steps, not fully implemented here.
- Next actions: Data Analysis Agent should fill real h5ad numeric operations behind the envelope; frontend work should render envelope blocks such as feature diff tables, evidence cards, stage timelines, DE previews, and heatmap previews.

### 2026-09-05 - Orchestrator Review Fixes

- Scope: Addressed Orchestrator review findings around structured Data Analysis requests, cache/readiness validity, runtime path isolation, audit semantics, and frontend default graph selection.
- Files changed: `backend/protocol_optimization_agent/orchestrator_agent.py`, `backend/protocol_optimization_agent/optimization_orchestration.py`, `backend/protocol_optimization_agent/runtime.py`, `backend/tests/test_orchestrator_agent.py`, `docs/current_project_architecture.md`, `docs/architecture_governance.md`, `frontend/.env.local.example`, `scripts/start_frontend.ps1`
- Capabilities added or changed: Data Analysis Orchestrator tool now accepts structured `request`, `h5ad_manifest_rows`, `h5ad_config`, and `force_rerun`; cached DA rows are filtered by requested genes/protocol/dataset/sample/condition/groups and return `no_matching_cached_result` instead of unrelated data. Protocol optimization can execute PA/DA first when structured upstream inputs are supplied, validates cache with upstream fingerprints plus objective/new protocol features, and records actual used tools. Runtime path resolution defaults to scoped user/run directories and rejects output paths outside the authorized workspace unless explicitly allowed for local development.
- Tests run: `pytest backend/tests/test_orchestrator_agent.py`; `pytest`; `git diff --check`
- Results: `backend/tests/test_orchestrator_agent.py` passed with 7 tests; full backend test suite passed with 34 tests; whitespace check passed.
- Contract changes: PO cache metadata now uses `optimization-cache-metadata-v1`; optimization readiness now includes `empty_files`, `fingerprint`, and `cache_key`; frontend defaults to `orchestrator`.
- Limitations: Protocol Analysis execution from raw artifacts is only available to the PO Orchestrator tool when callers provide `protocol_records` and `factor_events`; production multi-user auth still needs to bind `user_id` server-side rather than from client-controlled state.
- Next actions: Add service-layer auth binding for `RunContext`, richer LLM planning for structured DA requests, and frontend renderers for the evidence envelope.

### 2026-09-04 - Data Analysis Agent

- Scope: Advanced Data Analysis Agent from placeholder contracts to first-pass executable h5ad analysis and Orchestrator-readable query outputs.
- Files changed: `backend/protocol_optimization_agent/agents/data_analysis.py`, `backend/protocol_optimization_agent/tools/h5ad_tools.py`, `backend/protocol_optimization_agent/orchestrator_agent.py`, `backend/tests/test_data_analysis_agent.py`, `backend/tests/test_orchestrator_agent.py`, `docs/current_project_architecture.md`, `docs/data_analysis_agent_tools.md`.
- Capabilities added or changed: Added anndata-backed h5ad inspection, cell type composition, target gene expression summary, marker/gene-program scoring, lightweight target-gene differential expression, dependency/file/obs/gene fallback statuses, `gene_expression_summary.csv`, `differential_expression.csv`, `data_analysis_query_result.json`, and deterministic Orchestrator dispatch for Data Analysis result tables.
- Tests run: `pytest backend/tests/test_data_analysis_agent.py backend/tests/test_orchestrator_agent.py`; `python -m pytest backend\tests`; `git diff --check`.
- Results: Focused tests 10 passed; full backend tests 31 passed; diff check passed with Windows LF/CRLF warnings only.
- Contract changes: Data Analysis Agent outputs now include `gene_expression_summary.csv`, `differential_expression.csv`, and `data_analysis_query_result.json`; Orchestrator Data Analysis tool now returns `selected_operation`, `result_preview`, `output_files`, and dataset-level `provenance`.
- Limitations: Maturity scoring remains a placeholder beyond gene-program score rows; reference similarity, GRN, trajectory, scVI/scIB/CellTypist/GSEApy/pySCENIC/CellOracle/CellRank/scVelo wrappers are registered but not yet fully wired for numeric execution.
- Server-side running notes: Use `backed=True`, target gene lists, sparse-safe vector reads, and small aggregate outputs; do not commit real `.h5ad`, large `inputs/`, large `runs/`, or downloaded external source directories.
- Next actions: Implement reference-normalized maturity scoring and organoid-reference pseudobulk/latent similarity once matched reference atlas metadata and lineage program conventions are finalized.

### 2026-09-10 - Data Analysis Agent Execution Paths

- Scope: Added validated standard and exploratory execution contracts and tested logical dataset slicing against the supplied h5ad metadata.
- Files changed: `backend/protocol_optimization_agent/agents/data_analysis.py`, `backend/protocol_optimization_agent/tools/h5ad_tools.py`, `backend/protocol_optimization_agent/orchestrator_agent.py`, `backend/protocol_optimization_agent/optimization_orchestration.py`, `backend/tests/test_data_analysis_agent.py`, `backend/tests/test_orchestrator_agent.py`, `docs/current_project_architecture.md`, `docs/data_analysis_agent_tools.md`, `docs/subagent_development_coordination.md`.
- Capabilities added or changed: `standard` is the default deterministic path; `exploratory` archives sandbox-generated script/environment/config/diagnostics; `data_analysis_validation.json` checks sample selection, gene identity, expression source, comparison groups, expression scale, maturity, and reference similarity; PO rejects failed DA validation.
- Tests run: `python -m pytest backend\tests\test_data_analysis_agent.py backend\tests\test_orchestrator_agent.py`; focused Ruff checks; backed metadata inspection of the three local h5ad test files.
- Results: Focused tests passed with 18 tests; Ruff passed. The h5ad files opened successfully in backed mode and logical dataset slicing is covered by a synthetic regression test.
- Contract changes: Added `data_analysis_execution_plan.json`, `data_analysis_validation.json`, and `exploratory/*`; `data_analysis_validation.json` is now a required PO upstream artifact and must explicitly report `po_ready=true`.
- Limitations: Maturity and reference-similarity functions remain pending, so current real DA runs correctly remain blocked from PO. The two organoid test files inspected on 2026-09-10 do not expose a final cell-type annotation column.
- Next actions: Supply the final organoid annotation keys/files and lineage gene programs, then implement and calibrate maturity and reference-similarity tools so the deterministic gate can pass.

### 2026-09-30 - Scientific World Model And Experiment Selection

- Scope: Implemented the supplied architecture recommendations while explicitly deferring PA unstructured-protocol extraction and final intervention IR design for later integration with `Protocol_extraction`.
- Files changed: Added `scientific_models.py`, `scientific_state.py`, `scientific_reasoning.py`, `scientific_ingestion.py`, `action_space.py`, `scientific_state_cli.py`, three focused test files, `docs/scientific_world_model.md` and a feedback payload template; integrated DA, PO, Orchestrator, subgraph metadata, config and project docs.
- Capabilities added: Transactional user/project-scoped Protocol/Phenotype/Evidence/Claim/Hypothesis/Experiment persistence; full revision history; deterministic Phenotype IR; opt-in bounded interpretation trajectories with consensus checks; scientific comparability gates; generate/critique/compare/rank/select experiment operators; available/partial/deferred action registry and dispatcher; explicit researcher review, completion and validated-feedback commands.
- Contract changes: DA additionally exports `phenotype_ir.json` and `phenotype_interpretation.json`; PO additionally exports `next_experiment_candidates.json` and embeds it in the legacy proposal. Orchestrator state accepts `project_id` and reads scientific state across runs. PO cache includes experimental lifecycle feedback, interpretation, all consumed endpoint tables, metadata, and scientific reasoning/model/PO code fingerprints.
- Tests run: Full `pytest -q`; focused Ruff checks; `git diff --check`; scientific-state CLI help smoke check.
- Results: 59 backend tests passed, including protocol comparison via the registered action dispatcher, DA IR/interpretation separation, PO artifact ingestion, independent interpretation agreement/conflict, transactional/concurrent writes, user/project isolation, immutable evidence, guarded experimental lifecycle, supportive and contradictory feedback, and cache invalidation.
- Limitations: DA maturity/reference methods retain their existing pending status; missing scientific metadata blocks experiment eligibility. Decision utility is based on explicit sourced ordinal assessments, not causal predictions or computed Bayesian information gain. Researcher commands are local operator interfaces; service authentication and dedicated review UI remain future integration work. Live LLM calls and wet-lab experiments were not executed during verification.
- Next actions: Connect final DA numeric methods; supply comparable cohort metadata and reviewed experiment assessments; subsequently register the owner's `Protocol_extraction` workflow under the deferred extraction action.

### 2026-10-01 - Scientific Consistency, Quantitative Feedback And Authorization

- Scope: Fixed all five review areas without adding Agents or replacing SQLite. PA extraction/final intervention IR remain deferred.
- Candidate contracts: Distinct immutable identity, evaluation-content fingerprint and version-specific evaluation ID; transactional stale-review rejection; locked reviewed/executed evaluations; SQLite-authoritative JSON/embedded report exports. Reversion to older evaluation content cannot revive a stale approval.
- Scientific contracts: Broad research topics contain specific hypotheses bound to baseline snapshot/version, intervention location/direction/dose, context and endpoints. Explicit valid endpoint cohorts check scales, normalization, methods, cell population, sampling conditions and actual samples/biological replicates. Unknown/mismatched cohorts cannot numerically pool. Matched strata are supported; implicit corrections are not.
- Feedback contracts: Recording outcomes and admitting numerical data are separate researcher actions. Admission binds actual persisted PA features, all treatment samples and validated controls; immutable dataset/result snapshots retain before/after quantitative calculations, computation fingerprints and change explanations. New values affect actual PO scores/associations, not merely cache invalidation; causal effects and Bayesian utility estimates are not claimed.
- Authorization: Added an authenticated server-managed credential/project/artifact policy, guarded both LangGraph graphs, FastAPI endpoints, scientific CLI and reviewer identity, and enabled per-user Bearer headers in the frontend. New `langgraph.authenticated.json`, `configs/auth.example.json` and `docs/authenticated_deployment.md` describe setup. Local mode remains trusted single-operator use, not multi-user authorization.
- Verification: Default Python suite 68 passed/1 skipped (FastAPI absent); `protocol_agent` suite 63 passed/6 skipped (AnnData absent), including authenticated FastAPI tests. Together the environments cover all 69 tests. Changed Python Ruff, frontend TypeScript, authentication header checks and whitespace checks passed. Starlette emitted a dependency deprecation warning in the API test environment.
- Limitations: No live LLM/wet-lab run or production LangGraph HTTP deployment was performed. Real auth-hook/thread-ownership tests, TLS/proxy header forwarding, credential provisioning and backups are required before multi-user release. DA maturity/reference implementation and a dedicated approval UI remain pending. Generic legacy analysis CLIs remain trusted OS-operator tools, not public multi-user API gateways.

### 2026-10-01 - Versioned Run Archives And Input Registration

- Scope: Unified archival for Orchestrator/PA chat graphs, fixed pipeline, direct PA/DA/PO runs, PA/query CLIs and analysis/query/chat API. Scientific state/evaluation identities are retained; PA extraction remains deferred.
- Contracts: Fresh run directories, parent/thread linkage, transactional SQLite journal, redacted effective configs, actual prompt/response and model records, tool arguments/durations/errors, internal stage snapshots, content-versioned outputs/evidence, saved final response/report and bundle verification.
- Large inputs: Removed the 100 MiB hash skip and truncated directory metadata fingerprints. Added trusted one-time read-only dataset registration, immutable receipt checks and input-change detection. Cache identities use content rather than snapshot location.
- Migration: Configured output directories are storage roots or selected source collections. Callers must consume returned paths, not append filenames to a fixed root. API query runs snapshot authorized source artifacts into private execution workspaces.
- Sealed proposals: Ingestion creates a child run with database-authoritative candidate exports and evaluation IDs instead of rewriting the historical PO run. CLI model initialization failures are sealed and query/plot commands use private collection copies.
- Verification (2026-10-02): Default Python suite: 87 passed, 1 skipped (FastAPI absent). `conda run -n protocol_agent python -m pytest -q -rs`: 82 passed, 6 skipped (AnnData absent), one Starlette/httpx deprecation warning. Authenticated API tests pass in conda; numeric AnnData tests pass in default Python. Focused archival/revision tests: 24 passed. Ruff checks on touched Python modules and `git diff --check` pass.
- Limits: No live LLM/production HTTP/huge-file load test. Unknown provider costs/revisions remain null. SIGKILL leaves an unsealed running archive; investigate before retrying. Server-owned dataset receipts/ACLs, backups and retention are operational requirements.

### 2026-10-05 - Single Formal Protocol Input

- Contract: `inputs/Protocols inputs/protocols.jsonl` is the only formal protocol source. Removed JSONL/SQLite fallback and sidecar CSV reads. Missing/empty/invalid input fails; fixed-pipeline legacy input keys require migration.
- Projection: Structured metadata, stages/preparation and nested reagents deterministically supply PA records, events and source identity. Events retain record indexes/JSON pointers/evidence. Unknown numbers, exposure intervals, confidence and readiness are not upgraded; extraction from evidence quotes remains deferred.
- Entrypoints: PA CLI/API and fixed pipeline use the shared loader. PA API archives only the formal file. Removed the obsolete `protocol_database_path` SDK argument. Preparation is retained in standardized protocol outputs. Updated default config, README and architecture; see `docs/protocol_input_contract.md`.
- Local source check: Existing JSONL loads 183 protocols and 6,374 projected events; all 183 records have `comparison_ready=false`. The original file and quality gates were not modified. Existing PA output collections remain historical snapshots until regenerated.
- Verification: `conda run -n protocol_agent python -m pytest -q -rs`: 94 passed, 6 skipped (AnnData absent), one existing Starlette/httpx deprecation warning. Default Python input/pipeline tests: 13 passed, 1 skipped (FastAPI absent). Included JSONL-only PA/comparison, malformed/duplicate/foreign records, no fallback, unknown-value preservation, config migration and API snapshot coverage. Ruff and `git diff --check` pass.
