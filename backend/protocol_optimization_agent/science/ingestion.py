from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from protocol_optimization_agent.analysis.phenotype.representation import (
    CONTEXT_FIELDS,
    METRIC_COLUMNS,
    build_phenotype_ir,
)
from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode
from protocol_optimization_agent.runtime.archive import TERMINAL, RunArchive, audited_operation
from protocol_optimization_agent.runtime.io import read_csv_dicts, read_json, read_jsonl, write_json
from protocol_optimization_agent.science.identity import stable_id
from protocol_optimization_agent.science.models import (
    AnalysisDataset,
    AnalysisResult,
    Claim,
    Evidence,
    ExperimentCandidate,
    Hypothesis,
    PhenotypeState,
    ProtocolState,
)
from protocol_optimization_agent.science.state import ScientificWorldModel


def ingest_protocol_outputs(
    store: ScientificWorldModel, output_dir: Path, protocol_ids: set[str] | None = None
) -> dict[str, Any]:
    rows = read_csv_dicts(output_dir / "protocol_feature_matrix.csv")
    if protocol_ids is not None:
        rows = [row for row in rows if row.get("protocol_id") in protocol_ids]
    if not rows:
        return {"status": "no_protocol_records", "path": str(store.path)}
    evidence = _evidence(store, "protocol", [{"path": str(output_dir / "protocol_feature_matrix.csv")}], {"rows": rows})
    metadata_fields = {
        "protocol_id",
        "protocol_name",
        "paper_id",
        "dataset_id",
        "confidence_score",
        "comparison_ready",
        *CONTEXT_FIELDS,
    }
    records = [
        ProtocolState(
            id=stable_id("protocol", row["protocol_id"]),
            protocol_id=row["protocol_id"],
            metadata={key: value for key, value in row.items() if key in metadata_fields},
            features={key: value for key, value in row.items() if key not in metadata_fields},
            evidence_ids=[evidence.id],
        )
        for row in rows
        if row.get("protocol_id")
    ]
    return store.put([evidence, *records], "S1")


def ingest_phenotype_outputs(store: ScientificWorldModel, output_dir: Path) -> dict[str, Any]:
    validation = read_json(output_dir / "data_analysis_validation.json")
    if validation.get("validation_status") == "failed":
        evidence = _evidence(
            store,
            "phenotype",
            [{"path": str(output_dir / "data_analysis_validation.json")}],
            {"validation": validation},
            quality="failed",
        )
        store.put([evidence], "S0")
        return {"status": "failed_validation_not_ingested", "path": str(store.path), "evidence_id": evidence.id}
    phenotype_ir = read_json(output_dir / "phenotype_ir.json")
    if not phenotype_ir:
        phenotype_ir = build_phenotype_ir(
            {table: read_csv_dicts(output_dir / f"{table}.csv") for table in METRIC_COLUMNS},
            read_jsonl(output_dir / "h5ad_summary_by_protocol.jsonl"),
            validation,
            str(output_dir),
        )
    phenotypes = [PhenotypeState.model_validate(p) for p in phenotype_ir.get("phenotypes", [])]
    if not phenotypes:
        return {"status": "no_phenotype_records", "path": str(store.path)}
    refs = [ref for phenotype in phenotypes for metric in phenotype.metrics for ref in metric.evidence_refs]
    evidence = _evidence(
        store,
        "phenotype",
        refs or [{"path": str(output_dir / "phenotype_ir.json")}],
        phenotype_ir,
        quality="passed" if all(p.validation_status == "passed" for p in phenotypes) else "unknown",
    )
    for phenotype in phenotypes:
        phenotype.evidence_ids = [evidence.id]
    return store.put([evidence, *phenotypes], "S2")


def ingest_experiment_proposals(store: ScientificWorldModel, output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if authenticated_mode():
        policy = AccessPolicy()
        policy.require(store.context.user_id, store.project_id, "write")
        policy.authorize_path(store.context.user_id, store.project_id, output_dir, "read")
    for directory in (output_dir, *output_dir.parents):
        manifest = directory / "run_manifest.json"
        if not manifest.is_file():
            continue
        source_run = json.loads(manifest.read_text(encoding="utf-8"))
        if source_run.get("status") not in TERMINAL:
            break
        # Persisting an assessment changes the export; never rewrite a sealed source run.
        context = replace(store.context, run_id=f"ingestion_{uuid4().hex}",
                          trace_id=f"trace_{uuid4().hex}", project_id=store.project_id)
        archive = RunArchive(context)
        archive.start({"operation": "ingest_experiment_proposals", "source": str(output_dir)},
                      parent_run_id=source_run["run_id"])
        try:
            with archive.scope():
                target = archive.seed(output_dir, "protocol_optimization")
                result = _ingest_experiment_proposals(ScientificWorldModel(context, store.project_id), target)
                report = read_json(target / "next_experiment_candidates.json")
                with archive.operation("persisted_candidate_export", {"output_dir": target}) as call:
                    archive.snapshot_outputs({path.name: path for path in target.iterdir() if path.is_file()}, call)
                    archive.result(call, {"next_experiment_candidates": report})
                result.update(output_dir=str(target), run_id=context.run_id, trace_id=context.trace_id)
                archive.finish(result)
                return result
        except BaseException as exc:
            archive.record("error", {"type": type(exc).__name__, "message": str(exc)})
            archive.finish({"error": str(exc)}, status="failed")
            raise
    return _ingest_experiment_proposals(store, output_dir)


@audited_operation
def _ingest_experiment_proposals(store: ScientificWorldModel, output_dir: Path) -> dict[str, Any]:
    report = read_json(output_dir / "next_experiment_candidates.json")
    if not report:
        return {"status": "no_experiment_proposals", "path": str(store.path)}
    evidence = _evidence(
        store,
        "association",
        [
            {"path": str(output_dir / "protocol_phenotype_associations.csv")},
            {"path": str(output_dir / "next_experiment_candidates.json")},
        ],
        {
            "hypotheses": report.get("hypotheses", []),
            "associations": read_csv_dicts(output_dir / "protocol_phenotype_associations.csv"),
            "scientific_preconditions": report.get("scientific_preconditions", {}),
        },
    )
    store.put([evidence], "S3")
    hypotheses = [Hypothesis.model_validate(h) for h in report.get("hypotheses", [])]
    for hypothesis in hypotheses:
        hypothesis.status = "UNRESOLVED"
        hypothesis.evidence_for, hypothesis.evidence_against, hypothesis.tested_experiment_ids = [], [], []
        hypothesis.rationale_evidence = [evidence.id]
    store.put(hypotheses, "S4")
    candidates = [ExperimentCandidate.model_validate(c) for c in report.get("candidates", [])]
    pending = [c for c in candidates if c.status == "PROPOSED"]
    update = store.put(pending, "S5")
    report["candidates"] = [store.get("experiment", c.id) for c in candidates]
    report["hypotheses"] = [store.get("hypothesis", h.id) for h in hypotheses]
    limit = int(report.get("selection_limit", 3))
    report["selected_for_review"] = [c["id"] for c in report["candidates"]
                                     if c["eligible"] and c["status"] == "PROPOSED"][:limit]
    report["selected_evaluations"] = [{"candidate_id": c["id"], "evaluation_id": c["evaluation_id"],
                                       "evaluation_version": c["evaluation_version"]}
                                      for c in report["candidates"] if c["id"] in report["selected_for_review"]]
    report["pairwise_comparisons"] = [
        {"a": a["id"], "a_evaluation_id": a["evaluation_id"], "b": b["id"],
         "b_evaluation_id": b["evaluation_id"], "utility_difference": a["utility"] - b["utility"]}
        for i, a in enumerate(report["candidates"]) for b in report["candidates"][i + 1:]
        if a["utility"] is not None and b["utility"] is not None
    ]
    report["authority"] = "scientific_world_model"
    write_json(output_dir / "next_experiment_candidates.json", report)
    proposal = read_json(output_dir / "proposed_improved_protocol.json")
    if proposal:
        from protocol_optimization_agent.analysis.optimization.proposals import _render_protocol_markdown

        proposal["next_experiment_candidates"] = report
        write_json(output_dir / "proposed_improved_protocol.json", proposal)
        (output_dir / "proposed_improved_protocol.md").write_text(_render_protocol_markdown(proposal), encoding="utf-8")
    dataset = read_json(output_dir / "analysis_dataset.json")
    if dataset:
        store.put([AnalysisDataset.model_validate(dataset)], "S2")
    result = read_json(output_dir / "analysis_result.json")
    if result:
        store.put([AnalysisResult.model_validate(result)], "S3")
    return {**update, "output_dir": str(output_dir)}


def record_comparison_claim(store: ScientificWorldModel, result: dict[str, Any], preconditions: dict[str, Any]) -> None:
    comparison = result.get("tool_result", {})
    if result.get("intent") != "compare_protocols" or comparison.get("status") != "ok":
        return
    ids = comparison.get("protocol_ids") or [comparison.get(key, {}).get("protocol_id", "") for key in ("protocol_a", "protocol_b")]
    refs = [{"protocol_id": protocol_id, "operation": "deterministic_feature_comparison"} for protocol_id in ids]
    observations = {
        "protocol_ids": ids,
        "feature_differences": comparison.get("feature_differences", []),
        "preconditions": preconditions,
    }
    evidence = _evidence(store, "protocol", refs, observations)
    claim = Claim(
        id=stable_id("claim", observations),
        statement=f"Structured feature comparison for {', '.join(ids)}; differences are recorded in the result.",
        evidence_for=[evidence.id],
        scope={"protocol_ids": ids, "interpretation": "descriptive_only"},
        caveats=["Differences do not establish causal phenotype effects."],
    )
    store.put([evidence, claim], "S1")


def _evidence(
    store: ScientificWorldModel,
    kind: str,
    refs: list[dict[str, Any]],
    observations: dict[str, Any],
    quality: str = "unknown",
) -> Evidence:
    provenance = {
        "kind": kind,
        "refs": refs,
        "observations": observations,
        "run_id": store.context.run_id,
        "trace_id": store.context.trace_id,
    }
    return Evidence(
        id=stable_id("evidence", provenance),
        kind=kind,
        source_refs=refs,
        run_id=store.context.run_id,
        trace_id=store.context.trace_id,
        quality=quality,
        observations=observations,
    )
