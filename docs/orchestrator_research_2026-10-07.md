# Orchestrator 协调机制：已发表文献对照

核查日期：2026-10-07。范围：以下三篇正式发表于 Nature 的原始研究，引用正式论文而非 preprint。仅整理已核实的出版方资料；未运行项目程序，未审阅业务代码。

## 1. Coscientist：Planner 按命令结果继续规划

Boiko et al., **Autonomous chemical research with large language models**, Nature 624, 570–578 (2023)，发表 2023-12-20。[正式论文及发表信息](https://www.nature.com/articles/s41586-023-06792-0)。

- **机制设计：** Planner 接收用户任务，按需调用 GOOGLE、PYTHON、DOCUMENTATION、EXPERIMENT；命令输出作为消息返回 Planner，支持继续规划和错误修正。模块包含 LLM 与非 LLM 工具，不能将所有模块都称为独立科学家代理。依据：Coscientist system architecture、Fig. 1。[主源](https://www.nature.com/articles/s41586-023-06792-0)
- **实验验证及边界：** 实际执行 Suzuki–Miyaura 和 Sonogashira 偶联，GC–MS 确认产物；板转移仍由人完成。另有基于历史反应数据查询的条件优化评估，不能描述为每轮都进行新增湿实验的优化闭环。依据：Integrated chemical experiment design、Chemical reasoning capabilities。[主源](https://www.nature.com/articles/s41586-023-06792-0)

## 2. Virtual Lab：PI 主持结构化会议，人类提供高层反馈

Swanson et al., **The Virtual Lab of AI agents designs new SARS-CoV-2 nanobodies**, Nature 646, 716–723 (2025)，在线发表 2025-07-29。[正式论文及发表信息](https://www.nature.com/articles/s41586-025-09442-9)。

- **机制设计：** 人类提供项目方向、会议议程和高层反馈；PI 创建科学家角色并主持多轮团队会议。科学家回应、Scientific Critic 批评、PI 综合讨论并追问，最后形成议程答案；个人会议也包含批评与修订。[主源：Fig. 1 图注](https://www.nature.com/articles/s41586-025-09442-9/figures/1)
- **实验验证及边界：** 系统构建计算设计流程并设计 92 个纳米抗体，研究者进行表达及 ELISA 结合验证；两种设计改善对 JN.1 或 KP.3 的结合。会议结论、计算评分与实测结合结果是不同证据，不能由讨论共识推断实验有效。依据：摘要、Data availability、作者贡献。[主源](https://www.nature.com/articles/s41586-025-09442-9)

## 3. Co-Scientist：Supervisor 以状态统计调度异步任务

Gottweis et al., **Accelerating scientific discovery with Co-Scientist**, Nature 655, 487–496 (2026)，在线发表 2026-05-19，Version of record 2026-07-01；正式发表年份为 2026。[正式论文及发表信息](https://www.nature.com/articles/s41586-026-10644-y)。

- **机制设计：** Supervisor 管理异步任务队列，依据假说数量、待审任务和竞赛进度等统计分配资源、判断终止，并周期写入持久 context memory。六类专门代理分别负责 Generation、Reflection、Ranking、Evolution、Proximity、Meta-review；Supervisor 是额外的协调角色。Ranking 使用 Elo 成对评审；Meta-review 将综合反馈加入后续提示词。依据：Methods、Fig. 1。[主源](https://www.nature.com/articles/s41586-026-10644-y)
- **实验验证及边界：** AML 候选及组合有体外验证，肝纤维化靶点有人肝类器官验证；AMR 案例独立推导了研究者已发现但未发表的机制。Elo 属于自动评审的相对排名，不是独立实验真值或成功概率。依据：Real-world validations、Extended Data Fig. 1。[主源](https://www.nature.com/articles/s41586-026-10644-y)

## 对照结论（研究判断）

三者分别展示命令反馈循环、结构化会议协调、状态统计驱动的异步调度。可比较的是信息如何回流、谁负责综合与决策，以及何种证据触发下一步；代理名称和数量本身不能证明架构优劣，也不构成照搬六类代理的依据。

**机制设计不等于效果验证。** 上述实验支持各论文所测试的系统与任务，不能据此保证当前 orchestrator 或采用相似机制的实现获得等同效果；任务、模型、工具、数据与人类参与均会影响结果。本文不对当前实现作正确性或性能判断。
