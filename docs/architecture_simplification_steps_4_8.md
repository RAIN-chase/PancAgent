# Architecture Simplification: Steps 4-8

日期：2026-10-06。接续 [第 1～3 步](architecture_simplification_steps_1_3.md)，按顺序完成模块迁移、计算提取、接口统一、实验扩展隔离和回归。保留工作区原有修改，本轮未提交 Git。

## Step 4: Runtime And Scientific State

| 原模块 | 当前实现 |
| --- | --- |
| runtime.py | runtime/context.py；runtime/__init__.py 保留旧公开导出 |
| run_archive.py、dataset_registry.py、access_control.py | runtime/archive.py、dataset_registry.py、access_control.py |
| llm_client.py、config.py、io.py | runtime/ 同名模块 |
| scientific_models/state/ingestion/dataset.py | science/models/state/ingestion/dataset.py |

`runtime/paths.py` 集中解析项目与包根目录，避免目录深度改变后定位错误。源码归档扫描整个实际包，而不是只收集 runtime 子目录。科学数据库位置、表结构、实体 ID 算法和持久化契约不变。

授权、运行归档、科学状态与基线定向检查：28 passed。新增测试读取迁移前 SQLite 记录，确认既有 payload 和版本不被重写；旧库缺少历史表时仍按原逻辑补建。

## Step 5: Scientific Calculations

- PA：加载、标准化、语义、特征发现、查询及审计迁入 `analysis/protocol/`；图构建、确定性特征编译、校验和比较分别提取到 graph_builder、feature_compiler、validation、comparison。重复使用的事件、单位和身份计算保留在 `_shared.py`。
- DA：h5ad 与外部工具计算迁入 `analysis/phenotype/`。Phenotype IR 和解释函数进入 representation.py。
- PO：关联、目标与评分、证据及候选修改分别进入 association、assessment、proposals；可比性、实验设计和上游准备检查进入 comparability、experiment_design、readiness。
- 现有 PO TypedDict 迁入 `analysis/optimization/contracts.py`，计算函数与 Agent 共用同一输入/更新契约；没有增加字段或运行步骤。
- 科学稳定 ID 计算进入 `science/identity.py`，函数体原样保留。旧 `scientific_state.stable_id` 继续转发相同函数。

Agent 节点仍维护原图顺序、日志、归档与输出，只调用计算模块并合并结果。计算层不依赖 Agent、服务接口、SQLite store 或湿实验扩展。聊天模型环境工厂迁入 runtime/llm_client.py，避免 Orchestrator 导入服务器模块。

冻结矩阵、数值、单位、缺失值、证据映射、查询与 Phenotype IR 均与原预期一致；没有重新录制 expected.json。175 个提取的原顶层计算函数通过 AST 函数体哈希逐一核对，见 `evals/architecture_baseline/calculation_bodies_before.json`。该记录是迁移核对依据，不禁止后续另行审核算法变更。

计算、PO 与子图定向检查：29 passed；PA、输入和 Orchestrator 检查：38 passed。

## Step 6: Interfaces And Compatibility

主对话入口是 `agents/orchestrator.py:graph`；PA 专用入口是 `interfaces/langgraph_server.py:graph`；固定批处理仍是 `agents/pipeline.py`。API、认证适配和六个 CLI 进入 interfaces，action registry 和 PA 聊天 Agent 进入 agents。

同步更新 pyproject.toml、两份 LangGraph 配置、API 启动脚本、内部导入、测试和示例。图 ID、CLI 命令名称、API 路由与环境变量契约保持不变。原 `orchestrator.py` 只兼容批处理类，不是主对话入口。

完整模块映射见 `evals/architecture_baseline/module_relocations.json`；迁移前和当前入口分别保存在 entrypoints.json 与 migrated_entrypoints.json。旧一对一模块使用模块别名指向唯一实现，保留函数身份与 monkeypatch 行为；scientific_reasoning.py 因拆为多个模块，只做显式符号转发。旧 CLI 的 `python -m` 方式仍可运行。

兼容层仅在下游调用完成迁移、明确发布破坏性版本并提供迁移说明后移除；不再向旧模块添加业务逻辑。

入口、聊天图、授权和批处理定向检查：18 passed。

## Step 7: Optional Experimental Feedback

将审批、执行记录、反馈写入和反馈准入实现提取到 `extensions/experimental_feedback/lifecycle.py`。API 和 researcher CLI 显式调用扩展；ScientificWorldModel 原公开方法保留惰性委托，兼容已有调用方。核心分析冷启动不会加载该扩展，也不会自动执行湿实验。

科学证据、假设和候选身份仍归 science；FeedbackAdmission、ExperimentFeedback 等共享持久化 schema 本轮也保留在 science/models.py，避免破坏历史读取和跨层数据契约。状态转换、审批绑定版本、授权、QC 门槛和反馈事务逻辑不改。科学实体/候选和反馈生命周期已分离，未要求更换 SQLite。

科学状态、版本、集成和授权定向检查：20 passed。

## Step 8: Verification And Documentation

- 最终完整后端回归：120 passed、6 skipped、1 warning，耗时 169.38 秒。六项跳过均因 protocol_agent 环境缺少 anndata；警告为现有 Starlette/httpx 弃用提示。
- 新增五项架构回归：新旧实现身份、计算依赖方向、核心冷启动、实际源码指纹/项目定位、旧 SQLite 记录兼容。与归档和科学基线组合检查：24 passed。
- Ruff 检查通过；科学基线通过。代码缓存和 quantitative result 指纹覆盖实际 PO/计算/science 实现，不再只哈希兼容转发文件。代码版本变动可令缓存和结果版本更新，但不改变冻结数值。
- Mypy 尚未全绿：迁移前快照与迁移后均有 41 项错误，错误消息多重集合一致。此次拆分引入的状态类型错误已修正；剩余问题包括历史动态 schema、SDK 回调和 TypedDict 边界类型。详情见 `evals/architecture_baseline/migration_static_checks.json`，不能将本轮宣称为类型检查全部通过。
- README、主架构、教学文档、DA 工具文档、归档与科学状态文档同步到新路径；首批记录和历史日志不改写，仅附后续迁移说明。
- 离线 wheel 构建通过，核对 13 个关键实际实现均在包中，旧 runtime.py、空 workflow.py 和 scoring.py 不在包中。protocol_agent 可编辑安装已更新，六个安装命令及六个旧模块 CLI 的 --help 均通过；git diff --check 通过。

重复验收命令：

```powershell
conda run -n protocol_agent python -m pytest -q -rs
conda run -n protocol_agent python -m ruff check backend evals/architecture_baseline/run_baseline.py
conda run -n protocol_agent python evals/architecture_baseline/run_baseline.py
conda run -n protocol_agent python -m mypy backend/protocol_optimization_agent
git diff --check
```

本轮仅整理架构，不补齐 DA 成熟度/参考相似性 placeholder，不引入因果推断或自动湿实验执行。真实大型 h5ad 与生物学评测仍需在具备依赖和数据的 Linux 环境验证。PA 的 Protocol_extraction 调用、非结构化协议到最终干预表示继续待定；唯一正式协议输入仍为 `inputs/Protocols inputs/protocols.jsonl`。

## Follow-Up: h5ad Dependency Verification

2026-10-06：按用户要求，在本机 Python 3.11 的 protocol_agent 环境安装 anndata 及其必要依赖，未修改分析算法、测试断言或项目依赖声明。上方 120 passed / 6 skipped 保留为安装前的历史记录；安装后六项 h5ad 测试全部实际执行并通过。

| 依赖 | 安装后版本 |
| --- | --- |
| anndata | 0.12.19 |
| h5py | 3.16.0 |
| numpy | 2.1.3，保持原版本 |
| pandas | 2.2.3，保持原版本 |
| scipy | 1.17.1，保持原版本 |
| zarr | 3.1.6 |

执行命令：

```powershell
conda run -n protocol_agent python -m pip install --no-user "anndata>=0.10"
conda run -n protocol_agent python -m pip check
conda run -n protocol_agent python -m pytest -q -rs backend/tests/test_data_analysis_agent.py
conda run -n protocol_agent python -m pytest -q -rs
conda run -n protocol_agent python evals/architecture_baseline/run_baseline.py
```

- pip check：No broken requirements found。
- Data Analysis 定向测试：11 passed，14.37 秒，无跳过。覆盖小型 h5ad 分析、同文件不同数据集筛选、缺失 obs 列、raw 基因索引、缺失 layer 和 log 尺度 DE 等检查。
- 完整后端回归：126 passed、0 skipped、1 warning，185.75 秒；仍仅为原有 Starlette/httpx 弃用警告。
- 科学基线：矩阵、单位、缺失值、证据、查询和 Phenotype IR 与冻结预期一致。

这些结果验证当前 Windows 环境中的小型合成数据与现有测试，不证明真实超大 h5ad 的内存、性能、Linux 部署或生物学统计充分性；这些仍需独立验证。本次没有安装全部外部分析工具，也未补齐已有 placeholder。

## 2026-10-06 后续简化

上述兼容层描述是目录迁移时的历史记录。现已完成内部调用方迁移并删除旧 Python 模块转发、符号重导出和冗余查询包装；仅保留规范路径，旧 `python -m` 路径不再有效。持久化 SQLite 实体与历史记录没有删除，CLI 命令、HTTP 路由和图 ID 保持稳定。主对话流程与破坏性导入迁移说明见 [有计划的科学分析流程](planned_scientific_analysis.md)。
