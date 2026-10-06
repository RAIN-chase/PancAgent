# Architecture Simplification: Steps 1-3

本文保留首批变更时的事实与路径；后续第 4～8 步及当前模块位置见 [后续执行记录](architecture_simplification_steps_4_8.md)。

日期：2026-10-06。本轮仅完成第 1～3 步，不迁移 runtime/science/interfaces，不改变数据库、实体身份、评分算法或科学门槛。工作区原有未提交修改保留；本轮没有执行 Git 提交。

## 1. 迁移前基线

环境：`protocol_agent`，Python 3.11.15；依赖版本与输入 SHA256 保存在 `evals/architecture_baseline/environment_before.json`。

`code_before.json` 从首次基线运行的既有归档中提取 Git HEAD 与全部实际 Python 源码指纹，包含当时的未提交代码，不以 HEAD 代替工作区。原运行归档保留当时的源码副本；该版本记录只用于追溯，后续迁移不要求源码指纹保持不变。

迁移前完整测试：**94 passed, 6 skipped, 1 warning**，耗时 152.22 秒。没有已有失败。6 项 AnnData 测试因缺少 `anndata` 跳过；警告是 Starlette/httpx 的弃用提示。

固定输入在 `evals/architecture_baseline/`：三个合成 protocol（100 ng/ml、50 ng/ml、未知剂量）和已计算的表达量表（真实零、2.5、pending 占位零）。它们用于软件回归，不是生物学验证，也不覆盖真实 h5ad 的执行或性能。

`expected.json` 在业务迁移前生成并冻结，包含：原始及最终矩阵的完整列顺序和行、规范化事件、protocol 表征/图、特征和单元格 provenance、schema/release gate、协议比较、剂量证据、完整 Phenotype IR。100/50 ng/ml 应换算为 0.1/0.05 ug/ml；未知剂量仍为空；pending 表达量成为 null，而真实零保留。

唯一允许的可变信息：运行归档 ID、trace ID、时间戳和实际存储位置。运行归档外壳不参与科学快照比较；被选科学产物内只将本次 PA 输出根目录与 fixture 根目录替换为 `$PA` / `$CASE`。事件来源使用稳定的仓库相对 locator；证据字段、证据 ID、行号、列名、数值、单位和缺失语义全部保留比较。

入口清单保存在 `entrypoints.json`：两个 graph ID、六个 CLI、九个业务 API 路由、环境变量名称和输出路径约定。返回路径指向新运行目录，不假设固定根目录下有最终文件。

入口验收发现当前 conda 环境的旧安装元数据只注册了四个 CLI；已用 `python -m pip install --no-index --no-deps --no-build-isolation --no-user -e .` 刷新本仓库 editable 安装，六个入口现均已注册，没有下载或增加依赖。基线目录的 `.gitattributes` 固定 LF，避免跨 Windows/Linux checkout 改变输入字节哈希。

第一次最终全量检查为 114 passed / 1 failed / 6 skipped；失败只来自仓库中旧 `backend/*.egg-info` 遮蔽已更新的环境元数据。已通过离线 wheel 构建重新生成这些已跟踪的元数据，使来源清单和入口清单与当前源码一致；入口专项测试随后通过。构建出的 wheel 已核对包含 pipeline/shared rules，不包含已删除的 workflow/scoring；临时 `build/` 已清理，wheel 仅保存在忽略的 `runs/architecture_package_check/`。

复核命令（仓库根目录，已安装 editable backend）：

```powershell
conda run -n protocol_agent python evals/architecture_baseline/run_baseline.py
conda run -n protocol_agent python -m pytest -q backend/tests/test_architecture_baseline.py backend/tests/test_execution_entrypoints.py
```

`--record` 仅用于首次建立基线；预期文件已存在时拒绝覆盖。后续迁移必须验证，不能通过重新录制掩盖行为变化。后续应补真实、专家核对的 protocol/h5ad 评测集。

## 2. 执行入口

| 变更 | 验收依据 |
| --- | --- |
| 真实批处理 `orchestrator.py` → `agents/pipeline.py` | smoke 测试执行真实 PA/DA/PO，检查九步顺序、实际产物和迁移后源码归档 |
| 原 `orchestrator.py` 仅转发 `ProtocolOptimizationAgent` | 新旧导入是同一个类；外部 Python 调用仍有效 |
| 删除空 `workflow.py` 及其两个骨架测试 | 搜索仅发现 README 和骨架测试使用，不存在业务入口调用 |
| 删除无调用的 `tools/scoring.py` | 不替代、不声称补齐真实成熟度/参考相似性计算 |
| 更新 CLI 导入、README、当前架构及教程 | 主对话 `orchestrator`、PA 专用 `protocol_chat`、固定批处理分别明确 |

仓库外部调用无法穷尽检查；原空 workflow/scoring 的直接导入不再支持。批处理兼容转发层只有在下游迁移完成并发布明确的破坏性版本后移除。

本步后 smoke、Orchestrator 和基线定向检查：**11 passed**。原 CLI `protocol-optimization-agent --help` 仍可使用；LangGraph 配置及 API 路由未改名。

## 3. 共享规则

唯一维护点：`analysis/protocol/feature_groups.py`。PA 和查询引擎共享分类、分组顺序和 rank；各自保留原响应字段与值类型。

迁移前分类逐列保存在 `classification_before.json`。唯一规则差异是 `ordered_reagent_program`：PA 原为 `other`，查询原为 `workflow`，现统一到 `workflow`。该字段当前在两端差异列表中均作为元数据排除，此约定保留；分类函数的行为变化不意味着本次报告多出一列差异，也不改变定量结果。专项测试核对该例外、规则优先级、所有冻结列、元数据排除及两种响应契约。热图 layer 是另一种视觉分层，没有合并或修改。

`subgraphs.py` 从三个 Agent 的真实未编译 StateGraph 获取内部节点/边，仅保留科学说明文字。补齐 PA 导出、DA planner/导出节点，删除错误的“LLM 补时间”说明。Mermaid 节点采用 graph ID 前缀，避免导出节点冲突；跨 Agent 连线表示导出产物交给 PO，不表示自动执行聊天调度。起止哨兵不在图示内部节点中。过期说明或未支持的条件分支会明确报错，不静默生成不完整图。

## Final Verification

最终完整回归：**115 passed, 6 skipped, 1 warning**，耗时 154.29 秒。跳过项仍仅是缺少 `anndata` 的六项测试；弃用警告与迁移前相同，没有新增失败。

- 科研基线复核通过，矩阵、数值、单位、缺失值、证据、协议查询和 Phenotype IR 与冻结预期完全一致。
- 分组与子图专项检查：23 passed；完整回归还覆盖真实 PA、Orchestrator、批处理、接口和科学状态测试。
- `conda run -n protocol_agent python -m ruff check backend evals/architecture_baseline/run_baseline.py`：通过。
- `git diff --check`：通过。
- 离线 wheel 构建与模块清单检查：通过；三个子图分别为 11/14/10 个实际节点。

第 4～8 步仍未开始，Git 尚未提交。真实 h5ad 执行与生物学评测仍需在具备相应依赖和数据的环境验证。
