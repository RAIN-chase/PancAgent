# PO 科学依据：LLM 假说、批评与实验验证

核查日期：2026-10-07。范围：三篇正式发表于 Nature 的原始研究；仅引用出版方页面及作者官方仓库。未运行业务代码或实验程序，未评审当前 BioDiscoveryAgent 实现。

## 1. Virtual Lab：计算设计接到新增结合实验

Swanson et al., **The Virtual Lab of AI agents designs new SARS-CoV-2 nanobodies**, Nature 646, 716–723 (2025)，在线发表 2025-07-29。[正式论文](https://www.nature.com/articles/s41586-025-09442-9)；[作者仓库](https://github.com/zou-group/virtual-lab)。

- **验证了什么：** 人类指导下的 LLM 科学家团队构建 ESM、AlphaFold-Multimer、Rosetta 计算设计流程，设计 92 个纳米抗体；研究者进行了新增表达与 multiplexed ELISA 结合实验。两种新设计对 JN.1 或 KP.3 的结合改善，同时保留祖先株结合。
- **边界：** 计算评分是候选筛选依据，ELISA 是实测结果；论文不能据此证明临床有效性、病毒中和能力或通用协议优化成功率。它支持“提出候选 → 专业工具筛选 → 人类湿实验验证”，不能把讨论达成共识当作实验证据。依据：摘要、Data availability、作者贡献及 Extended Data Fig. 5–6。[论文](https://www.nature.com/articles/s41586-025-09442-9)

## 2. Coscientist：真实合成与历史数据优化分别成立

Boiko et al., **Autonomous chemical research with large language models**, Nature 624, 570–578 (2023)，发表 2023-12-20。[正式论文](https://www.nature.com/articles/s41586-023-06792-0)。

- **新增湿实验：** GPT-4 驱动系统检索资料、选择试剂、生成并修正机器人协议，执行 Suzuki–Miyaura 和 Sonogashira 偶联；GC–MS 确认目标产物形成。板转移仍由人完成。
- **计算验证：** 另有基于既往实验数据的条件选择与优化评估，并比较贝叶斯优化；不能将该部分写成持续新增湿实验的闭环优化。它验证受限化学任务的工具调用、执行和利用观测反馈，未验证胰腺分化协议或细胞表型预测。依据：Integrated chemical experiment design、Chemical reasoning capabilities、Extended Data Fig. 3。[论文](https://www.nature.com/articles/s41586-023-06792-0?trk=article-ssr-frontend-pulse_little-text-block)

## 3. Co-Scientist：更新且已正式发表的生物医学案例

Gottweis et al., **Accelerating scientific discovery with Co-Scientist**, Nature 655, 487–496 (2026)，在线发表 2026-05-19，Version of record 2026-07-01。此处引用正式论文，不把 2025 年预印本当作正式发表证据。[正式论文](https://www.nature.com/articles/s41586-026-10644-y)；[出版方发表信息](https://www.nature.com/nature-index/article/10.1038/s41586-026-10644-y)。

- **验证了什么：** Gemini 多代理生成、批评、排序并改进假说；专家选择后的 AML 药物及组合有体外验证，单药案例报告剂量—生长抑制曲线、IC50 和生物学重复；肝纤维化靶点案例有人肝类器官抗纤维化及再生表型验证。
- **边界：** AMR 案例独立再推导了研究者已经发现但尚未发表的 cf-PICI 借用不同噬菌体尾部扩展宿主范围机制，不能称为 AI 首先产生并驱动全部实验的新发现。Elo 是假说之间的相对评审排名；计算评估提升与选定案例湿实验成功是两类证据，均不能转换为每个假说的效应概率。依据：Fig. 1、Fig. 3、System analysis and evaluation、Real-world validations。[论文](https://www.nature.com/articles/s41586-026-10644-y)

## 对 PO 的有限启示（研究判断，非上述论文直接验证）

这些研究支持让 LLM 提出和批评可检验干预、整理证据并辅助实验规划；没有直接证明其能在当前项目的小样本上预测“协议修改 → 胰腺细胞表型改善”。PO 应把每项候选写成明确的干预（阶段、剂量、时长）、对照、测量时间和预期读出，并保留支持证据、替代解释与失败条件。

排序应区分文献支持度、机制合理性、可执行性、领域模型预测和已测效果。**LLM 自评、共识分或 Elo 不等于 P(干预改善表型)**；概率或效应区间需要针对目标实验数据建立并验证统计模型。小样本评估应预先定义主要终点，记录独立批次与重复，控制批次差异，并用新实验检验候选。上述三篇不能提供当前 PO 的样本量保证、概率校准或因果效应保证。
