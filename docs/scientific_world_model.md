# Scientific State And Experiment Design

Implemented on 2026-09-30. This extends PA, DA, PO and Orchestrator with a structured scientific state layer; it adds no role-specific agents.

## Implemented Flow

```mermaid
flowchart TD
    Structured[Existing structured protocol artifacts] --> PA[Protocol Analysis]
    Data[h5ad and validated numerical analyses] --> DA[Data Analysis Engine]
    DA --> IR[Phenotype IR]
    IR --> Interpretation[Optional independent interpretation trajectories]
    PA --> World[Versioned Scientific World Model]
    IR --> World
    Interpretation --> Consensus[Agreement or researcher review]
    World --> PO[Generate / Critique / Compare / Rank / Select]
    Consensus --> PO
    PO --> Review[Researcher review of next experiment candidates]
    Review --> Performed[Experiment performed outside the agent]
    Performed --> Observation[Validated new phenotype and reviewed outcome]
    Observation --> World
    Extraction[Protocol_extraction workflow: deferred] -. future integration .-> Structured
```

**PA extraction is explicitly deferred.** The unstructured-protocol-to-standardized-intervention step, its final Protocol IR design, and calling the owner's `Protocol_extraction` workflow are pending. Existing PA JSON/events, deterministic matrices, comparisons and evidence retrieval continue to work. This change does not redefine their upstream extraction contract.

## State And Persistence

`scientific_models.py` defines ProtocolState, PhenotypeState, Evidence, Claim, Hypothesis, ExperimentCandidate and ExperimentFeedback. `scientific_state.py` uses Python's SQLite standard library; no graph database is required.

```text
{workspace_root}/users/{user_id}/research/{project_id}/scientific_state.sqlite3
```

`project_id` is independent of `run_id`, so evidence and hypotheses persist across runs within a study. Different users/projects have different databases. The Orchestrator accepts injected `project_id`; its default is `PROTOCOL_RESEARCH_PROJECT_ID=default`. Source data remain outside the database; protocol feature snapshots and small phenotype aggregates are stored with references to their artifacts. h5ad matrices are not copied into scientific state.

- Evidence ids depend on source references, aggregate content, run and trace. Repeating the same write is idempotent; changing an existing Evidence record is rejected.
- Entity revisions and stage events are written in one SQLite transaction. Full old payloads, outcome interpretation, actor, run and trace are retained in `history`.
- Relations use evidence ids, hypothesis ids, protocol ids and dataset ids. Unknown evidence/hypothesis references are rejected.
- A failed DA validation is recorded as failed evidence; its phenotype results do not enter the usable state.
- Regenerating a hypothesis preserves its experimental support, counterevidence and previous tests.
- Experimental support yields `SUPPORTED`, opposition yields `REFUTED`, and conflicting/absent evidence yields `UNRESOLVED`. Confidence is an ordinal label, never a calibrated probability. Association evidence is rationale, not experimental support.
- The optional authenticated deployment profile verifies server-managed credentials, project membership, artifact paths and reviewer identity across both graphs, the API and CLI. Local mode is still a trusted operator interface. See [authenticated_deployment.md](authenticated_deployment.md) for setup and deployment checks.

Stages S0-S9 follow the requested scientific process. They are **per-record events**, not a single global project status: parallel hypotheses can be at different stages. S1 means existing structured protocol artifacts are available; it does not mean the deferred extraction workflow ran.

| Stage | Meaning |
| --- | --- |
| S0 | Evidence ingestion |
| S1 | Existing structured protocol available |
| S2 | Phenotype characterized |
| S3 | Protocol-phenotype association recorded |
| S4 | Hypothesis generated |
| S5 | Experiment candidate proposed |
| S6 | Researcher review |
| S7 | Experiment performed |
| S8 | New validated phenotype observed |
| S9 | Hypothesis assessed |

## DA: Computation And Interpretation

DA additionally exports `phenotype_ir.json` and `phenotype_interpretation.json`.

Phenotype IR organizes metrics into cell_identity, development, maturity, quality, expression and regulation. Each metric preserves its numeric source value, unit, method, logical sample/condition, analysis context and CSV row reference. Pending metrics retain `value=null`; they do not become numerical evidence. A marker mean-expression score is not relabeled as a validated maturity score. Source scores are not rescaled by the representation builder.

Numeric computation runs once. Interpretation operates on copies of the same numerical snapshot. An optional interpreter callback can be injected into `DataAnalysisAgent`; a configured JSON LLM client is also supported:

```json
{
  "interpretation": {
    "enabled": true,
    "trajectories": 3,
    "max_metrics": 200,
    "llm": {
      "provider": "deepseek",
      "model": "deepseek-chat",
      "api_key_env": "DEEPSEEK_API_KEY"
    },
    "claims": [
      {"claim_id": "beta_identity", "statement": "The analyzed cells show evidence of beta-cell identity."}
    ]
  }
}
```

This is an opt-in `h5ad_config` block. Defaults perform no extra LLM calls. Each of 2-5 trajectories independently assesses the same named statements, cites observed metric ids and returns supports/refutes/uncertain. Claims changing the question or citing nonexistent/unsupplied metrics are rejected. Failures, missing votes, uncertain conclusions or disagreement produce `human_review_required`; PO cannot select an eligible experiment while this gate is unresolved. Agreement is consistency checking, not statistical meta-analysis or causal validation. The configured metric budget limits each LLM payload; omitted metrics cannot be cited. LLM calls are sequential and bounded by the configured trajectory count and existing client timeout.

## Execution And Scientific Preconditions

Existing execution gates determine whether PA/DA artifacts are present, valid and joinable. The scientific gate builds the explicitly requested endpoint cohort and checks cell line, stage, modality, reference version, method, unit, expression scale, normalization, method version, cell population and sampling condition. `source_scale`/unknown labels are not evidence of compatible scales. Only QC-passed numeric endpoint observations enter counts; duplicate sample/endpoint observations and missing biological-replicate identities block numerical merging. The configurable computational floor is at least three protocols and two identified biological replicates per protocol; it is not a power calculation or sufficient statistical evidence.

Protocol comparison remains descriptive. Unknown/mismatched metadata blocks PO's numeric pooling as well as automatic experiment eligibility, but not inspection. Raw values remain visible with `numeric_merge_allowed=false`. `objective.comparison.stratum` can explicitly select a matched sub-cohort; excluded samples, actual endpoint/protocol/sample/replicate counts and scale profiles are exported. No implicit cross-scale conversion or batch correction is performed. Causal claims are not enabled by matching metadata.

Supply cell_line, stage, sequencing_modality and reference_version in source protocol metadata and DA manifest rows when available. The implementation deliberately does not infer them from paper titles or filename conventions.

## PO: Next Experiments

The existing deterministic association and evidence modules feed a new `experiment_design` node. It produces `next_experiment_candidates.json` with hypotheses, critiques, pairwise utility comparisons, ranks and a shortlist for researcher review. Existing output files remain compatible; the legacy proposal JSON additionally embeds this report and its Markdown now describes candidate experiments.

Each candidate retains its source-supported intervention suggestion and controls. No new numerical dose or timing is invented. Missing source evidence, unresolved scientific gates, missing decision assessments or prior tests without an explicit retest reason block eligibility. Multiple candidates compete; they are not declared optimal protocols.

Configure `objective.experiment_design` to provide explicit ordinal assessments, keyed by the candidate feature:

```json
{
  "primary": "increase_beta_like_maturity",
  "baseline_protocol_id": "REPLACE_BASELINE_PROTOCOL_ID",
  "applicable_context": {"cell_line": "REPLACE_ACTUAL_CELL_LINE"},
  "comparison": {
    "endpoints": ["REPLACE_ACTUAL_METRIC_ENDPOINT"],
    "minimum_protocols": 3,
    "minimum_replicates_per_protocol": 2
  },
  "experiment_design": {
    "max_candidates": 10,
    "select_count": 3,
    "weights": {"improvement": 1, "information": 1, "cost": 1, "risk": 1},
    "assessments": {
      "reagent_factor_x": {
        "expected_improvement": 0.6,
        "information_gain": 0.8,
        "experimental_cost": 0.2,
        "risk": 0.3,
        "source": "researcher_reviewed_design",
        "rationale": "Compare two competing mechanisms with a matched control."
      }
    }
  }
}
```

These example values are illustrative judgments, not measured biological predictions. Utility is the configured weighted improvement + information - cost - risk. Each assessment requires an explicit source/rationale and finite scores in [0,1]. Absent assessments remain null and produce no eligible shortlist. Information gain is currently a supplied ordinal judgment; Bayesian outcome modeling and formal expected information gain are not implemented.

Evolution happens after experimental feedback: a later PO run reads prior hypothesis/test history, critiques previously tested ideas and can propose a revised or explicitly justified retest. It does not spend iterations generating identical deterministic candidates. Reviews/results contribute to the optimization cache fingerprint, so feedback invalidates old PO results.

## Registered Actions

Orchestrator tools `describe_scientific_actions`, `execute_scientific_action`, and `inspect_scientific_state` expose the registry and scientific state. Registry entries declare inputs, required inputs, execution/scientific preconditions, version, availability and outputs.

Executable actions are protocol.compare, protocol.lookup, phenotype.cell_composition, phenotype.gene_expression, phenotype.differential_expression, experiment.propose and scientific.state. Dispatch uses the existing PA/DA/PO tools and their validated pipelines. Undeclared arguments cannot override injected runtime paths or user identity. The registry describes preconditions; the corresponding domain tools execute their existing gates, and PO performs the additional scientific gate.

phenotype.beta_maturity and phenotype.reference_similarity are `partial` and are not executed through the action dispatcher. protocol.extract is `deferred` and returns `executed=false`. Literature search and other unavailable pipelines are not falsely registered as executable.

## Researcher Commands

Run `pip install -e .` to update the `scientific-state` console entry point; alternatively run `python -m protocol_optimization_agent.interfaces.scientific_state_cli` in the installed backend environment. Commands work on Windows and Linux. Explicit review, execution, feedback and admission operations live in `extensions/experimental_feedback/lifecycle.py`; scientific identities and shared persisted contracts remain in `science/`.

```bash
scientific-state --user-id local_user --project-id default snapshot
scientific-state --user-id local_user --project-id default history hypothesis HYPOTHESIS_ID
scientific-state --user-id local_user --project-id default ingest pa runs/protocol_analysis_agent_simplified_autofeatures
scientific-state --user-id local_user --project-id default ingest da runs/data_analysis
scientific-state --user-id local_user --project-id default ingest po runs/protocol_optimization
scientific-state --user-id local_user --project-id default review EXPERIMENT_ID --evaluation-id EVALUATION_ID --decision approve --researcher RESEARCHER_ID --reason "Matched design and budget checked"
scientific-state --user-id local_user --project-id default performed EXPERIMENT_ID --researcher RESEARCHER_ID
scientific-state --user-id local_user --project-id default feedback experimental_feedback.json
scientific-state --user-id local_user --project-id default admit-feedback feedback_admission.json
```

CLI commands are explicit researcher actions. Chat tools cannot approve, mark performed or record results. Eligible proposed experiments can be approved/rejected; only approved experiments can be marked performed; only performed experiments can accept feedback. Feedback requires QC-passed experimental evidence, a matching experiment id, validated numerical phenotype observations, a reviewed outcome for every linked hypothesis and a researcher interpretation. Retrying identical feedback is idempotent; changed feedback cannot overwrite an assessed result.

Use [experimental_feedback.example.json](../examples/experimental_feedback.example.json) as the payload shape. Replace its example identifiers and measurements with actual reviewed experiment and analysis records before ingestion. The schema is also available through `ExperimentFeedback.model_json_schema()`.

## Version-Bound Candidates And Hypotheses (2026-10-01)

Candidate identity is separate from evaluation identity. A PROPOSED candidate can acquire new `evaluation_id`, `evaluation_version`, `evaluated_at`, `input_snapshot`, gate results and assessments. Identical evaluations are idempotent. Reviews require the exact current evaluation ID in a transaction and preserve it as `approved_evaluation_id`; stale reviews fail. Reviewed/executed evaluations are locked. Full previous evaluations remain in history, rather than being overwritten by eligibility changes.

The evaluation content fingerprint and version-specific evaluation ID are distinct: reverting to earlier evaluation content creates a new ID, so an old approval request cannot become valid again after intervening changes.

PO JSON starts as a generation draft. Orchestrator/CLI ingestion persists it and exports candidates/hypotheses from SQLite, including selected evaluation IDs and version-bound pairwise comparisons. Embedded proposal JSON/Markdown are refreshed from the persisted report. Locked candidates cannot be advertised as new approval targets. Direct PO users must ingest the draft before review; generation alone is not approval authority.

`topic_id` groups a feature/objective research theme. A `hypothesis_v2` ID binds a concrete baseline protocol snapshot/version, stage and timing, operation/direction/dose, applicable context, objective and endpoints. Unknown conditions remain explicit and block eligibility. Merely fixing an evaluation or changing its utility does not change candidate identity. Changing the actual intervention/background creates a distinct hypothesis. Regeneration preserves belief only for the exact specification; legacy feature-only hypotheses do not propagate outcomes into v2 hypotheses. Feedback for v2 hypotheses must cite the approved `evaluation_id` and exact `evidence.observations.test_specs` map.

Existing SQLite tables migrate additively through typed payload defaults. PROPOSED legacy candidates are versioned when regenerated/ingested; existing historical reviews/results remain untouched. An old approval without a bound evaluation cannot be newly marked performed: submit a versioned candidate for review instead of pretending the old approval applied to a new evaluation.

## Quantitative Feedback Admission

Recording QC-passed feedback updates the explicitly linked hypothesis and invalidates cache, but **does not yet admit the numbers to PO**. A separate researcher-reviewed `FeedbackAdmission` must bind:

- The assessed experiment and its exact `feedback_digest`.
- Actual executed protocol: an exact versioned ProtocolState already ingested from PA, with protocol evidence and compiled features. Feedback cannot invent or overwrite final protocol feature values.
- All treatment sample IDs, a distinct control protocol and existing QC-passed control samples. Unknown/unbound samples or mismatching actual protocol snapshots are rejected.
- Authenticated reviewer and reason in secure mode (operator-supplied actor in local mode).

The server captures treatment/control PhenotypeState and control ProtocolState snapshots, ignoring caller-supplied phenotype replacements. Admission is immutable/idempotent. Its fingerprint invalidates PO cache independently of recording feedback. Use [feedback_admission.example.json](../examples/feedback_admission.example.json) for the shape; it is intentionally a placeholder, not usable experimental data.

PO merges source DA Phenotype IR and admitted numerical observations into an immutable content-addressed `analysis_dataset.json`. It then rebuilds objective scores, associations, predictions and proposals from that dataset through the endpoint gate; it no longer just carries evidence IDs as history. Admission does not bypass DA readiness or scientific comparability. Source/feedback sample conflicts fail instead of silently replacing an approved snapshot. Rerunning against changed control/actual protocol versions requires reconciling the reviewed binding, not silently rebinding it.

`analysis_result.json` preserves dataset ID, computation fingerprint, objective, protocol scores, associations, prior result ID, admitted experiments and before/after score changes. SQLite retains immutable dataset/result records even when latest run-directory files are replaced. Change summaries distinguish new admitted experiments, changes to base inputs and changes to the objective; they do not attribute causality. Endpoint weights are applied after aggregating each protocol/endpoint so endpoints with more samples do not implicitly receive larger weights. Ordinal design assessments remain researcher judgments and are not silently recalibrated from new measurements.

## Current Limits

- PA extraction and final intervention IR are deferred for later Protocol_extraction integration.
- Maturity/reference tools must still be implemented/calibrated in DA; the existing readiness gate remains active.
- LLM agreement is not a calibrated confidence measure, and differing runs of one model are not statistically independent experiments.
- Utility scores are explicit design judgments. There is no learned causal effect estimator or computed Bayesian information gain yet.
- Experiment execution occurs outside the agent; feedback recording is available, not wet-lab automation.
- SQLite supports local/server project storage and transactional writes. Distributed multi-host deployment needs a shared authenticated persistence adapter and backup policy.
- The chat UI can show returned JSON; dedicated hypothesis/experiment review UI is a later integration.
