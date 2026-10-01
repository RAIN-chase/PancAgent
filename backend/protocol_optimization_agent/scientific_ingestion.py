from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol_optimization_agent.io import read_csv_dicts, read_json, read_jsonl
from protocol_optimization_agent.scientific_models import (
    Claim,
    Evidence,
    ExperimentCandidate,
    Hypothesis,
    PhenotypeState,
    ProtocolState,
)
from protocol_optimization_agent.scientific_reasoning import CONTEXT_FIELDS, METRIC_COLUMNS, build_phenotype_ir
from protocol_optimization_agent.scientific_state import ScientificWorldModel, stable_id


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
        hypothesis.rationale_evidence = [evidence.id]
    store.put(hypotheses, "S4")
    candidates = [ExperimentCandidate.model_validate(c) for c in report.get("candidates", [])]
    return store.put(candidates, "S5")


def record_comparison_claim(store: ScientificWorldModel, result: dict[str, Any], preconditions: dict[str, Any]) -> None:
    comparison = result.get("tool_result", {})
    if result.get("intent") != "compare_protocols" or comparison.get("status") != "ok":
        return
    ids = [comparison.get(key, {}).get("protocol_id", "") for key in ("protocol_a", "protocol_b")]
    refs = [{"protocol_id": protocol_id, "operation": "deterministic_feature_comparison"} for protocol_id in ids]
    observations = {
        "protocol_ids": ids,
        "feature_differences": comparison.get("feature_differences", []),
        "preconditions": preconditions,
    }
    evidence = _evidence(store, "protocol", refs, observations)
    claim = Claim(
        id=stable_id("claim", observations),
        statement=f"Structured features differ between {ids[0]} and {ids[1]}.",
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
