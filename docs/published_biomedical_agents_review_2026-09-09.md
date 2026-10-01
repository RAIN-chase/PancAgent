# 已发表生物分析 Agent：架构借鉴与项目落地

检索日期：2026-09-09。以期刊官网及正式会议论文集为发表依据；年份区分在线发表与卷期。本文是文献对照与设计建议，不是新一轮代码缺陷审计，也不代表下述能力已在本项目实现。

## 已核实的代表工作

| 系统 | 发表信息及原始来源 | 论文中的机制 | 本项目的借鉴方向（建议） |
| --- | --- | --- | --- |
| AutoBA | Advanced Science，2024；[An AI Agent for Fully Automated Multi-Omic Analyses](https://onlinelibrary.wiley.com/doi/abs/10.1002/advs.202407094) | 将数据路径、数据描述和目标转成计划，再生成和执行代码，包含自动修复环节。 | DA 将计划、执行记录和结果分别保存；已验证流程优先使用固定工具。 |
| CRISPR-GPT | Nature Biomedical Engineering，2025-07-30 在线，2026 卷期；[原文](https://www.nature.com/articles/s41551-025-01463-z) | Planner、任务执行器、工具与用户接口协作；执行器使用状态机，并提供问答、引导和自动模式。 | Orchestrator 区分查询、分析和优化；用依赖状态控制执行，并保留研究者的关键决策。 |
| CellAtria / CellExpress | npj Artificial Intelligence，2026-01-16；[An agentic AI framework for ingestion and standardization of single-cell RNA-seq data analysis](https://www.nature.com/articles/s44387-025-00064-0) | LangGraph 编排经过验证的工具和标准单细胞流程，保存元数据、配置与运行记录；不依赖自由生成分析代码。 | 最接近本项目的确定性分析方向。强化 DA 数据契约与标准分析工具，而不是给每个计算节点加入 LLM。 |
| CASSIA | Nature Communications，2025-12-07 在线，2026 卷期；[原文](https://www.nature.com/articles/s41467-025-67084-x) | 将细胞注释、校验、质量评分、报告分开；低质量结果可进一步细化。 | 将提出解释与验证证据分开；DA 注释和 PA 语义特征可加入有界复核。质量评分不能直接当作统计置信概率。 |
| BioMedAgent | Nature Biomedical Engineering，2026-03-30；[Empowering AI data scientists using a multi-agent LLM framework with self-evolving capabilities for autonomous, tool-aware biomedical data analyses](https://www.nature.com/articles/s41551-026-01634-6) | 通过工具探索和记忆检索组织可执行工作流；提出 BioMed-AQA 并使用外部基准评价。 | 建立审核后的分析配方库，并用本领域端到端题集评估执行正确率。 |
| BioDiscoveryAgent | ICLR 2025 正式会议论文；[论文集](https://proceedings.iclr.cc/paper_files/paper/2025/hash/4252dc94531833029000f85dc5fac792-Abstract-Conference.html) | 根据已有扰动实验结果选择下一批实验，结合文献、代码工具和批判性评价。 | PO 从单次建议发展为多轮候选选择，记录试验预算、已测试条件与失败结果。遗传扰动上的效果不能直接外推到类器官 protocol。 |
| Robin / Finch | Nature，2026-05-19；[A multi-agent system for automating scientific discovery](https://www.nature.com/articles/s41586-026-10652-y) | 连接文献检索、实验假设、数据分析和反馈；Finch 保存 notebook 分析过程。 | 连接候选 protocol、实际实验版本、表型结果和下一轮假设。实验仍由人实施；原文也说明精确可执行 protocol 是后续方向。 |
| Virtual Lab | Nature，2025-07-29；[The Virtual Lab of AI agents designs new SARS-CoV-2 nanobodies](https://www.nature.com/articles/s41586-025-09442-9) | PI Agent 组织专业 Agent，研究者提供高层反馈，并对设计进行实验验证。 | 仅在跨领域设计或有争议的候选上引入专业复核，避免简单查询也进行多 Agent 讨论。 |

上述论文的任务、数据与评价方法不同，不能横向比较报道的成功率，也不能直接证明本项目在胰腺类器官任务上的准确性。

## 补充核实：Biomni 与 CellAgent

- Biomni 已有 Science 正式版，题名为 [Autonomous biomedical research with an artificial intelligence agent](https://www.science.org/doi/10.1126/science.adz4351)，DOI 10.1126/science.adz4351。在线日期 2026-07-09 由 [AAAS 官方新闻稿](https://www.eurekalert.org/news-releases/1134718)明确给出；此次 Science 正文工具读取失败。工具环境、检索增强规划与代码执行机制可查[作者官方仓库](https://github.com/snap-stanford/Biomni)。对本项目，适合借鉴按任务检索工具说明、声明输入前提与维护统一工具环境，不建议将全部矩阵计算改为临时生成代码。
- CellAgent 已有 [ICLR 2026 正式会议稿](https://openreview.net/pdf?id=BsA2GNkJhz)，题名为 CellAgent: LLM-Driven Multi-Agent Framework for Natural Language-Based Single-Cell Analysis。Planner–Executor–Evaluator 的分层适合 DA 任务规划、执行和结果检查；迭代评估应限制重试次数，并检查科学语义而非只检查代码能否运行。未核实到期刊正式版，引用时应写会议论文。
- CellAgent 与 BioMedAgent 不同：Nature Biomedical Engineering 的 DOI 10.1038/s41551-026-01634-6 对应 BioMedAgent，不应混淆。

## 四个 Agent 的具体改造建议

### Orchestrator：计划、执行与检查分开

参考 CRISPR-GPT 和 CellAtria，保留现有四 Agent 的职责划分，增加轻量任务计划及状态转换，不必新增常驻 Agent。

- 查询：解析 protocol/dataset/sample 身份，读取已验证产物，再生成解释。
- 分析：解析输入、过滤条件、工具及参数，先检查前提，再提交执行。
- 优化：核实 PA/DA 产物版本与目标一致后调用 PO。
- 每步记录输入 artifact ID、配置、状态、输出 artifact ID、失败原因与重试次数。
- 只针对影响结果的缺失信息澄清；不让 LLM 猜测样本、对照组或关键剂量。
- 修复重试有上限，重试不得静默改变研究问题或统计设计。

验收场景：同一问题重复查询结果一致；变更目标不误用缓存；上游失败时不报告下游分析成功；多篇候选文章不能静默选错 protocol。

### Protocol Analysis Agent：验证特征含义和数值，而不仅是格式

借鉴 CASSIA 的提出与校验分离，将复核落实为代码检查和可定位证据；这属于跨任务的设计迁移，并非 CASSIA 已验证了 protocol 量化。

- FeatureSpec 记录字段选择器、阶段/时序约束、单位、缺失语义及计算规则版本。
- 每条候选规则附正例和反例：仅 A、仅 B、A/B 同阶段、跨阶段、未报告等。
- 对基础列和新增列统一做单元格重算检查。
- 字段级账本区分：保留于结构化记录、投影到矩阵、排除并说明原因、尚未支持。
- 固定每次运行所用 FeatureSpec 和语义标注快照；解释生成不得改变事实产物。
- 外部文献知识只能支持解释或候选定义，不能补写某篇 protocol 未报告的实验事实。

验收指标：源字段保留率、关键数值字段映射率、单元格重算一致率、错误来源引用率、未报告值误填率。明确各指标的分母，不能把事件 ID 被引用率称为全部细节覆盖率。

### Data Analysis Agent：固定可靠分析路径，开放受约束的探索路径

优先参考 CellAtria；AutoBA、BioMedAgent 的灵活工具组织适合后续探索能力。

- 数据契约明确表达矩阵来源、基因索引、样本选择、组别、预处理状态、参考版本。
- 将已验证分析封装为可独立调用工具，返回表格、配置、诊断信息及证据路径。
- 可将 CASSIA 作为注释辅助候选，但必须在自身类器官数据及参考标签上评估；不能以注释质量分替代成熟度。
- 完善成熟度与参考相似性之前，不把占位产物计为可用表型证据。
- 探索性代码在独立运行环境执行，保存脚本/notebook、依赖与产物；通过校验后再供 PO 使用。
- 成功配方经审核后进入带版本的配方库，不自动传播一次运行的偶然结论。

验收场景：raw 与 X 的基因顺序不同、多个样本共用 h5ad、缺失目标基因、空组、错误 layer、稀疏矩阵及部分工具失败。

### Protocol Optimizing Agent：将建议绑定到具体实验及结果

参考 BioDiscoveryAgent 和 Robin，建立最小实验反馈记录，而不是先扩充自由文本推荐。

- 每个候选包含基线 protocol 版本、明确的修改、保持不变的条件、支持/反对证据、预期方向和失败判据。
- 区分不可变事实、可干预变量及尚未确定的参数；不优化文献置信度等元数据。
- 区分“提高已知目标”与“减少关键不确定性”的实验目的，在预算内选择下一批候选。
- 记录所有已测试条件与阴性结果，避免重复推荐无效方案。
- 建立 recommendation ID → 实施版本 → experiment ID → sample ID → 表型结果的关联。
- 新结果只创建新的协议/分析版本，不覆盖历史证据。

验收方式：先使用按研究来源或时间隔离的历史结果回放，再进行小规模前瞻性验证；报告证据可追溯性、候选可实施性、重复建议率和实验结果，不把回顾性关联当作干预效果。

## 最小评测方案

建议先建立 30–50 个专家核对案例，属于本项目建议规模，不是上述论文的统一要求。

| 层次 | 示例 | 判定 |
| --- | --- | --- |
| 协议事实 | 两篇文章同一阶段的差别 | protocol 解析、阶段对齐、原文引用正确 |
| 定量计算 | 混合单位、共现、重复加药、缺失值 | 与独立参考实现及已审核期望值一致 |
| 数据执行 | 指定样本和基因的表达比较 | 细胞选择、矩阵来源、统计设计正确 |
| 调度可靠性 | 缺上游、缓存失效、工具失败 | 正确执行或明确返回不可完成状态 |
| 建议质量 | 对指定基线提出修改 | 证据、可实施性、验证设计由专家盲评 |

比较固定工具流程、单 LLM + 同一工具集、四 Agent 三种条件；固定基础模型、数据、工具和预算。进一步移除证据校验、动态 FeatureSpec 或实验记忆做消融。由此区分收益来自模型、工具、数据，还是 Agent 架构。

## 项目定位建议

将项目定位为“证据可追溯的类器官 protocol 表征、表型关联与实验迭代系统”。优先建立协议事实到实验结果的可靠映射，再扩展通用问答与工具数量。这是项目发展建议，不是已经证明的独创性声明。
