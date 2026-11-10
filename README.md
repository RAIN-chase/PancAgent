# PancAgent

科研分析工作台：比较胰腺／胰岛类器官 protocols，连接单细胞表型证据，
提出可追溯、待验证的优化假说。保留 Orchestrator、PA、DA、PO 四个角色；
不将关联、候选排名或 LLM 解释视为因果证据。

## 从这里开始

1. [当前架构与能力边界](docs/current_project_architecture.md)
2. [Linux 安装、配置与启动](docs/linux_deployment.md)
   · [内部试用演示与发布门禁](docs/linux_internal_trial.md)
3. [正式 protocol 输入](docs/protocol_input_contract.md)、
   [h5ad 目录与 N 套 protocol 比较](docs/data_catalog_and_multi_compare.md)
4. [工作台：对话、已有结果与来源证据](docs/research_workbench.md)
5. [运行归档、版本及结果位置](docs/run_archives.md)

## 最短启动路径

Linux 推荐 Python 3.11、Node.js 22；完整前提见部署文档。

```bash
conda create -n protocol_agent python=3.11 -y
conda activate protocol_agent
python -m pip install -e ".[h5ad,server,dev]"
cp -n .env.example .env
cp -n frontend/.env.local.example frontend/.env.local
```

填写本地配置，不提交密钥；随后在 `frontend/` 执行
`pnpm install --frozen-lockfile`，回到仓库根目录，在两个终端分别启动：

```bash
bash scripts/start_linux.sh chat
bash scripts/start_linux.sh frontend-dev
```

主要对话图 ID 是 `orchestrator`，不是已移除的 PA-only 对话入口。
批处理使用 `bash scripts/start_linux.sh batch --help` 查看参数。
Windows 启动脚本保留在 `scripts/`。默认本地模式不提供多人身份隔离；
共享部署必须使用[认证与授权配置](docs/authenticated_deployment.md)。

## 一次可核对的分析

不调用模型、不需要大型 h5ad 的固定样例：

```bash
python evals/architecture_baseline/run_baseline.py
python -m pytest backend/tests/test_architecture_baseline.py -q
```

输入和冻结期望值位于 `evals/architecture_baseline/`，命令默认产物位于
`runs/architecture_baseline/`；检查矩阵、单位、缺失值、步骤与证据引用。
不要在重构时使用 `--record` 重建期望结果。
真实分析的保存结果以各运行的 manifest、JSON 和证据引用为准，
不能从聊天 Markdown 反推测量值。

## 代码导航

| 目录 | 职责 |
| --- | --- |
| `backend/protocol_optimization_agent/agents/` | 规划调度、领域执行、批处理；唯一能力清单为 `actions.py` |
| `analysis/` | 无对话状态的 protocol、表型与优化计算 |
| `science/` | 科学对象、来源、版本及持久记录 |
| `runtime/` | 权限、配置、路径、归档与模型调用 |
| `interfaces/` | HTTP、CLI、服务认证适配 |
| `extensions/experimental_feedback/` | 可选审批、执行记录与反馈 |
| `frontend/` | Next.js 工作台与 LangGraph 通信 |
| `configs/`、`evals/`、`backend/tests/` | 配置模板、固定样例与回归验收 |

DA 批处理接口在 `agents/data_analysis.py`，科学动作／对话适配在
`agents/data_actions.py`，实际计算在 `analysis/phenotype/`。
`inputs/`、`runs/`、`users/`、`external_tools/` 是数据、运行或依赖位置，
不应按源码整理策略整目录删除或移动。

## 专题文档

- 方法：[DA 任务执行](docs/data_analysis_task_execution.md)、
  [成熟度](docs/maturity_scoring_tool.md)、[PA 步骤语义](docs/pa_step_semantics.md)、
  [PO 科学证据](docs/po_scientific_evidence.md)。
- 状态：[会话记忆](docs/memory_layer.md)、[科学记录](docs/scientific_world_model.md)、
  [问题验收与停止](docs/orchestrator_goal_review.md)。
- 验证：[科研质量与待验收项](docs/research_quality_validation.md)、
  [方法准入、独立验收与失败恢复](docs/methods_and_recovery.md)、
  [24 项评测试点](evals/research_tasks/README.md)、
  [受控代码执行](docs/controlled_code_execution.md)。
- 开发：[提交规则](CONTRIBUTING.md)、[本轮职责整理记录](docs/structure_cleanup_2026-11-01.md)。

成熟度计算已接入，但跨研究生物学适用性仍须验证；参考相似度尚非完整的已验证方法。
代码执行默认关闭。方法可调用、前提满足、完成科学验证是三件不同的事。

文档权威顺序为 README 导航 → 当前架构 → 功能专题。
带日期的调研、`framework_architecture_*`、`architecture_simplification_*`
和改造记录属于历史／设计资料，不作为当前能力保证。
