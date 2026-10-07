# Published Agent Architecture: Research Note

Date: 2026-10-06. Scope: source inspection only; no project programs executed. AgentLaboratory and ChemCrow observations below were supplied as verified by the user, without repeating their research.

## CellAgent: Official Repository Unresolved

The [arXiv paper, v1](https://arxiv.org/abs/2407.09811) identifies the work by Yihang Xiao, Jinyi Liu and colleagues and links to [cell.agent4science.cn](http://cell.agent4science.cn/), rather than directly establishing a GitHub repository. That site could not be retrieved in this session. The supplied [ICLR 2026 OpenReview PDF](https://openreview.net/pdf?id=BsA2GNkJhz) and its forum page returned browser-verification challenges. An author-to-repository provenance chain was therefore **not verified**; this does not establish that official code is unavailable.

Supported architecture facts from [arXiv Methods 4.1-4.2](https://arxiv.org/html/2407.09811v1#S4): a Planner produces JSON subtasks; the Executor separates Tool Selector and Code Programmer responsibilities; the Evaluator assesses trial outputs and guides iterative optimization. Global memory retains final code from previous steps, while local dialogue memory resets between subtasks. Registered tools provide documentation to code generation. The paper describes notebook execution through `nbconvert`. These are paper descriptions, not independently verified repository implementation or sandbox-security guarantees.

Repository disambiguation:

- [cellagent659/cellagent](https://github.com/cellagent659/cellagent) is a candidate whose README describes hierarchical agents, sc-Omni and self-reflective optimization. Its observed `main` root contains `codes/`, `figs/`, `scOmni/`, `tutorial/Batch_correction/` and `README.md`. The inspected [codes directory](https://github.com/cellagent659/cellagent/tree/main/codes) lists only `environment.yml`. No main agent entrypoint was established from those inspected listings, and official status remains unverified.
- [liu-shiqiang/CellAgent](https://github.com/liu-shiqiang/CellAgent) presents its own software citation to Liu Shiqiang, a `src/` layout and `main.py`. Treat it as a separate implementation, not evidence of the paper authors' implementation; similar role names do not prove provenance.
- [xuanyuelingwu/CellAgent](https://github.com/xuanyuelingwu/CellAgent) describes an alpha ReAct package inspired by Biomni. Its package layout and tool-count claims must not be attributed to the published CellAgent.
- [earthcomputing/CellAgent](https://github.com/earthcomputing/CellAgent) describes an Earth Computing systems repository, unrelated to the single-cell paper.

Consequently, official CellAgent entrypoint size, orchestration dependencies and source-level persistence behavior remain unresolved. No simplicity ranking based on an alleged official entrypoint is justified.

## Biomni: Observed Official Package Layout

The [snap-stanford/Biomni `biomni/` directory on main](https://github.com/snap-stanford/Biomni/tree/main/biomni) lists directories `agent/`, `biorxiv_scripts/`, `eval/`, `know_how/`, `model/`, `task/`, `tool/`; files `__init__.py`, `config.py`, `env_desc.py`, `env_desc_cm.py`, `llm.py`, `utils.py`, `version.py`.

The inspected [agent directory](https://github.com/snap-stanford/Biomni/tree/main/biomni/agent) lists `__init__.py`, `a1.py`, `env_collection.py`, `function_generator.py`, `qa_llm.py`, `react.py`. This confirms an observed package structure, not whole-system complexity. Browser results for these directories reported cached crawls approximately three weeks old, so exact live-head freshness was not independently established.

## User-Verified Comparison Observations

- AgentLaboratory's [official main root](https://github.com/SamuelSchmidgall/AgentLaboratory) includes `agents.py`, `ai_lab_repo.py`, `inference.py`, `mlesolver.py`, `papersolver.py`, `tools.py`, `utils.py`. The user reports [ai_lab_repo.py](https://raw.githubusercontent.com/SamuelSchmidgall/AgentLaboratory/main/ai_lab_repo.py) has an approximately 830-line `LaboratoryWorkflow` with phase loops and pickle-based `save_state`. The approximate count is retained as supplied, not remeasured here.
- ChemCrow's user-verified [agent setup](https://raw.githubusercontent.com/ur-whitelab/chemcrow-public/main/chemcrow/agents/chemcrow.py) is 81 lines and delegates `ChatZeroShotAgent` and `RetryAgentExecutor` to `rmrkl`, with `max_iterations`. The user also verified that the [official README](https://github.com/ur-whitelab/chemcrow-public) warns that its public tool subset cannot reproduce the same paper results.

Interpretation: a short setup module can delegate substantial orchestration complexity to a dependency; a larger workflow module can implement that behavior locally. File length alone does not measure total architecture complexity or reproducibility.

## Revision Caveat

**Default branch != paper commit.** All GitHub `main`/`master` links here are mutable observations, not verified experimental snapshots. No paper-specific commit was established. Repository identity, publication provenance, inspected source revision and reproducibility should be assessed separately.
