# 已发表科研 Agent 的公开架构对照

检索日期：2026-10-06。对象是五个已发表工作对应的作者 GitHub 仓库，不是对全部科研 Agent 的穷尽统计。目录仅列关键部分；检索的是当前默认分支，未固定论文版本 commit，因此不能把当前实现完全等同于发表时实现。

## Virtual Lab

- 论文：[Nature 2025](https://www.nature.com/articles/s41586-025-09442-9)。
- 作者仓库：[zou-group/virtual-lab](https://github.com/zou-group/virtual-lab)。
- 关键目录：`src/virtual_lab/`（Agent 核心）、`nanobody_design/`（科学任务）、`images/`（展示）。[目录来源](https://github.com/zou-group/virtual-lab)
- 核心目录包括 `agent.py`、`prompts.py`、`run_meeting.py`、`utils.py`、`constants.py`，以及包初始化、版本与类型标记文件。[核心目录](https://github.com/zou-group/virtual-lab/tree/main/src/virtual_lab)
- 编排：`run_meeting()` 使用 Python 的轮次与成员循环，构造角色提示词、共享会议历史、调用模型与可选 PubMed 工具；最后由负责人或个体总结，并保存讨论。不是必须使用 LangGraph 的多 Agent 系统。[源码](https://github.com/zou-group/virtual-lab/blob/main/src/virtual_lab/run_meeting.py)
- 判断：多角色可以由少量编排代码实现，但纳米抗体计算流程与实验验证的复杂度不能忽略。

## ChemCrow

- 论文：[Nature Machine Intelligence 2024](https://www.nature.com/articles/s42256-024-00832-8)。
- 作者仓库：[ur-whitelab/chemcrow-public](https://github.com/ur-whitelab/chemcrow-public)。
- `chemcrow/` 下主要为 `agents/`、`tools/`、`data/`、`docker/`、`frontend/`；根目录有 `tests/` 与打包文件。[核心目录](https://github.com/ur-whitelab/chemcrow-public/tree/main/chemcrow)
- `agents/` 包括 `chemcrow.py`、`prompts.py`、`tools.py` 与初始化文件。[Agent 目录](https://github.com/ur-whitelab/chemcrow-public/tree/main/chemcrow/agents)
- 编排：ChemCrow 类构建工具集合与提示词，交给 `ChatZeroShotAgent` 和 `RetryAgentExecutor` 进行迭代执行。更接近一个主 Agent 配多种科学工具，而非每个工具都成为独立 Agent。[源码](https://github.com/ur-whitelab/chemcrow-public/blob/main/chemcrow/agents/chemcrow.py)
- 限制：README 明确说明由于 API 限制，公开包不包含论文全部工具，也不能完全复现论文结果。不能用这个公开包的大小代替完整系统规模。[说明](https://github.com/ur-whitelab/chemcrow-public)

## CellAtria

- 论文：[npj Artificial Intelligence](https://www.nature.com/articles/s44387-025-00064-0)。
- 作者仓库：[AstraZeneca/cellatria](https://github.com/AstraZeneca/cellatria)。
- 根目录主要分为 `agent/`、`cellexpress/`、`docs/`，配有 Docker 部署文件。[仓库](https://github.com/AstraZeneca/cellatria)
- `agent/` 包括 `base.py`、`chatbot.py`、`toolkit.py`、`system_prompts.md`、`toolkit.md`、`utils.py` 等。[Agent 目录](https://github.com/AstraZeneca/cellatria/tree/main/agent)
- 编排：`base.py` 定义消息状态，把工具绑定给模型，构建 `chatbot` 与 `tools` 两个 LangGraph 节点；工具执行后返回 chatbot，无工具调用则结束；使用内存检查点。该文件还包含 Gradio 交互层。[源码](https://github.com/AstraZeneca/cellatria/blob/main/agent/base.py)
- 科学处理层放在 `cellexpress/`，与 Agent 交互层区分。[科学工具目录](https://github.com/AstraZeneca/cellatria/tree/main/cellexpress)
- 判断：适合作为入门对照，重点是 Agent 层和真实分析工具层的分离，而不是复制其 UI 与代码执行权限设计。

## Biomni

- 论文：[Science 2026，出版记录](https://pubmed.ncbi.nlm.nih.gov/42424436/)。
- 作者仓库：[snap-stanford/Biomni](https://github.com/snap-stanford/Biomni)。
- 根目录包含 `biomni/`、`biomni_env/`、`docs/`、`tutorials/` 等。[仓库](https://github.com/snap-stanford/Biomni)
- `biomni/` 内包括 `agent/`、`tool/`、`model/`、`eval/`、`task/`、`know_how/`，以及 `config.py`、`llm.py`、`utils.py` 等。[核心目录](https://github.com/snap-stanford/Biomni/tree/main/biomni)
- 编排：A1 的 LangGraph 以 `generate` 和 `execute` 为核心，执行后返回生成节点，并可启用 `self_critic`。同时有工具、数据和软件资源的检索选择。[源码](https://github.com/snap-stanford/Biomni/blob/main/biomni/agent/a1.py)
- 判断：图很小，不等于整个系统很小；当前 `a1.py` 本身已有大量代码，科学工具与环境也很丰富。不要把把代码合并到一个大文件当成简化方案。

## AutoBA

- 论文：[Advanced Science 2024](https://onlinelibrary.wiley.com/doi/10.1002/advs.202407094)。
- 作者仓库：[JoshuaChou2018/AutoBA](https://github.com/JoshuaChou2018/AutoBA)。
- 根目录包括 `app.py`、`gui.py`、`src/`、`examples/`、`softwares_config/`、`softwares_database/` 等。[仓库](https://github.com/JoshuaChou2018/AutoBA)
- `src/` 包括 `agent.py`、`prompt.py`、`executor.py`、`local_llm.py`、`build_RAG_private.py`，以及本地模型相关目录。[源码目录](https://github.com/JoshuaChou2018/AutoBA/tree/main/src)
- `app.py` 读取任务数据、目标和输出位置，实例化 Agent 并调用 `run()`。[入口](https://github.com/JoshuaChou2018/AutoBA/blob/main/app.py)
- `agent.py` 结合 PromptGenerator、CodeExecutor 和可选 RAG，进行任务规划、代码生成、执行检查与修复。[Agent 源码](https://github.com/JoshuaChou2018/AutoBA/blob/main/src/agent.py)
- 判断：清晰的入口、Agent、提示词、执行器分工值得学习；自动执行生成代码需要隔离和权限控制，不宜直接照搬至多用户服务。

## 不适合直接用公开规模比较的例子

[CRISPR-GPT 作者仓库](https://github.com/cong-lab/crispr-gpt-pub) 提供的是 light version；[论文](https://www.nature.com/articles/s41551-025-01463-z) 与仓库公开范围不相同。不把其简版目录作为完整论文系统复杂度的证据。

## 当前项目的对照判断

本地检查：`backend/protocol_optimization_agent/` 包含 47 个 Python 文件（不含后端测试与前端）。数量本身不是架构优劣指标。

- `langgraph.json` 同时暴露 `protocol_chat` 与 `orchestrator` 两个图；此外有 FastAPI、CLI 和固定分析流程。初学者需要辨别每个入口对应哪条执行链路。
- `workflow.py` 是九节点流程骨架，节点目前直接返回 state；不应把这个流程图当作已完成的分析实现。
- `tools/scoring.py` 中 maturity 和 similarity 接口仍抛出 `NotImplementedError`，项目架构文档也明确记录了相关数值工具待接入。
- 科学状态、证据、研究者反馈、运行归档和身份权限承担真实需求，不应仅因目录较多就删除。

判断：对于先学习并跑通科研原型的阶段，目前入口和外围层的认知负担偏重。优先明确一条主流程，区分科研计算核心与产品服务层，补足真实计算与评估，而不是增加 Agent 数量或简单合并文件。

建议学习顺序：输入契约 → 一个确定性分析工具 → 单 Agent 工具循环 → PA/DA/PO 协作 → 保存证据与结果 → 基线、指标与消融 → 再理解聊天、服务和权限。该顺序是本次对照后的建议，不是所有发表项目统一采用的架构。

本次仅新增调研笔记，未修改现有业务代码或删除文件。

## 本轮复核与精简建议

本轮重新读取上述作者仓库的目录及核心源码，并检查本地当前工作区。此前已有的笔记予以保留；以下补充不代表已执行代码重构。

直接核查的编排源码：

- [CellAtria base.py](https://github.com/AstraZeneca/cellatria/blob/main/agent/base.py)：消息状态、chatbot/tools 循环及界面适配。
- [Virtual Lab run_meeting.py](https://github.com/zou-group/virtual-lab/blob/main/src/virtual_lab/run_meeting.py)：角色、轮次、共享会议历史和总结。
- [Biomni a1.py](https://github.com/snap-stanford/Biomni/blob/main/biomni/agent/a1.py)：generate/execute 循环和可选 self_critic；当前文件超过 2,000 行，不能以图的节点数推断代码规模。
- [ChemCrow chemcrow.py](https://github.com/ur-whitelab/chemcrow-public/blob/main/chemcrow/agents/chemcrow.py)：工具组装、提示词及 AgentExecutor。
- [AutoBA src](https://github.com/JoshuaChou2018/AutoBA/tree/main/src)：agent、prompt、executor、可选 RAG 和本地模型适配。

对本地复杂度的判断：四个逻辑 Agent 合理；可精简的是入口、重复规则和尚未使用的扩展。47 个 Python 文件不构成复杂度超标的证据。PA 文件已超过 3,000 个物理行，混合图构建、特征编译、比较、审计与导出，职责集中比文件数量更值得处理。

按优先级建议：

1. 将 orchestrator 明确为主对话入口。protocol_chat 可作为 PA 受限入口保留，但应共用查询工具和响应契约；FastAPI 与 CLI 保持薄适配器，不各自演进分析流程。
2. workflow.py 的节点仍为直接返回 state 的空骨架，引用主要在 README 与骨架测试。确认外部调用后移到 examples，或移除骨架及对应说明；不要同时维护骨架流程图和真实执行流程。
3. PA 的 _feature_group 与 query_engine 的 _feature_importance_group 维护相近分类规则，且已经存在 ordered_reagent_program 的分类差异。统一规则来源，保留不同响应格式的适配。
4. 根据现有职责拆出 PA 的图构建/编译与审计计算，使 Agent 文件主要表达执行顺序。避免将所有代码合并为单文件，也避免每个辅助函数成为新模块。
5. 湿实验审批、performed、反馈准入与重算作为可选扩展，当前三类实验前任务不依赖该生命周期。已有科研状态、证据引用与版本信息继续保留。
6. run_archive 与 scientific_state 不应直接合并：前者记录计算执行，后者保存跨运行的科学实体。可统一 artifact/run/evaluation ID 的关联和对外查询方式。

建议的职责目录仅是后续整理方向，不是要求一次性搬迁：

```text
protocol_optimization_agent/
  agents/          # 主调度及 PA/DA/PO 的执行顺序
  analysis/        # protocol 表征、h5ad 计算、比较与假设评价
  science/         # 科学模型、状态与产物摄取
  runtime/         # 运行上下文、归档、数据注册
  interfaces/      # LangGraph Server、API、CLI 的适配
  extensions/      # 实验反馈等可选能力
tests/
evals/             # 固定案例、基线与科学评价；当前属于建议新增的职责
frontend/
```

以上目录应随具体模块改动逐步整理。优先收敛主流程和去除重复，单独搬目录不会降低实际复杂度。没有经过删除与回归验证，不给出可减少代码行数或依赖数量的估计。
