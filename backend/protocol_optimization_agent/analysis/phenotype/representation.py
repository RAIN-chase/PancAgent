"""Extracted representation calculations; no agent execution."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from protocol_optimization_agent.analysis.optimization._values import _number
from protocol_optimization_agent.runtime.llm_client import LLMClient
from protocol_optimization_agent.science.identity import stable_id
from protocol_optimization_agent.science.models import (
    PhenotypeMetric,
    PhenotypeState,
)

METRIC_COLUMNS = {
    "cell_type_composition": ("cell_identity", ["fraction", "proportion", "percentage"]),
    "maturity_scores": ("maturity", ["maturity_score", "score"]),
    "similarity_scores": ("development", ["cosine_similarity", "similarity_score", "score"]),
    "marker_scores": ("expression", ["mean_score", "marker_score", "score"]),
    "gene_program_scores": ("maturity", ["mean_score", "program_score", "score"]),
    "gene_expression_summary": ("expression", ["mean_expression", "mean"]),
    "differential_expression": ("expression", ["log2_fold_change", "logfoldchanges"]),
    "regulatory_program_scores": ("regulation", ["regulatory_score", "score"]),
    "trajectory_scores": ("development", ["trajectory_alignment_score", "pseudotime_score", "score"]),
}

CONTEXT_FIELDS = (
    "cell_line",
    "stage",
    "sequencing_modality",
    "reference_version",
    "normalization",
    "batch",
    "group",
    "groupby",
    "group_a",
    "group_b",
    "cell_filter",
    "lineage",
    "expression_source",
    "expression_scale",
    "effect_size_type",
    "statistical_scope",
    "multiple_testing",
    "n_cells",
    "p_value",
    "method_version",
    "cell_population",
    "sampling_condition",
    "biological_replicate_id",
    "study_id",
    "culture_batch",
    "sequencing_batch",
    "adjusted_p_value",
    "n_replicates_a",
    "n_replicates_b",
    "source_table",
    "source_column",
    "measurement_unit",
)


def build_phenotype_ir(
    tables: dict[str, list[dict[str, Any]]],
    summaries: list[dict[str, Any]],
    validation: dict[str, Any],
    source_dir: str,
) -> dict[str, Any]:
    records: dict[tuple[str, str, str, str], PhenotypeState] = {}
    for table, rows in tables.items():
        if table not in METRIC_COLUMNS:
            continue
        family, columns = METRIC_COLUMNS[table]
        for index, row in enumerate(rows):
            key = tuple(
                str(row.get(field) or "") for field in ("protocol_id", "dataset_id", "sample_id", "condition_id")
            )
            if not key[0] or not key[1]:
                continue
            if key not in records:
                summary = next(
                    (
                        s
                        for s in summaries
                        if all(
                            str(s.get(f) or "") == row_value
                            for f, row_value in zip(
                                ("protocol_id", "dataset_id", "sample_id", "condition_id"), key, strict=True
                            )
                        )
                    ),
                    {},
                )
                metadata = {f: summary[f] for f in CONTEXT_FIELDS if summary.get(f) not in (None, "")}
                records[key] = PhenotypeState(
                    id=stable_id("phenotype", [source_dir, *key]),
                    protocol_id=key[0],
                    dataset_id=key[1],
                    sample_id=key[2],
                    condition_id=key[3],
                    metadata=metadata,
                    validation_status=str(validation.get("validation_status") or "unknown"),
                )
            status = str(row.get("status") or "unknown")
            column = next((c for c in columns if _number(row.get(c)) is not None), "")
            value = _number(row.get(column))
            if any(term in status.lower() for term in ("pending", "missing", "failed", "unavailable", "not_")):
                value = None
            endpoint = str(
                next(
                    (
                        row[f]
                        for f in ("endpoint", "cell_type", "marker_set", "gene_program", "gene", "lineage", "regulon")
                        if row.get(f)
                    ),
                    table,
                )
            )
            actual_family = (
                "quality"
                if any(term in endpoint.lower() for term in ("stress", "off_target", "proliferation", "abnormal"))
                else family
            )
            records[key].metrics.append(
                PhenotypeMetric(
                    id=stable_id("metric", [source_dir, table, index, row]),
                    family=actual_family,
                    endpoint=endpoint,
                    value=value,
                    unit=str(
                        row.get("unit")
                        or records[key].metadata.get("measurement_unit")
                        or (
                            "fraction"
                            if column in {"fraction", "proportion"}
                            else "percent"
                            if column == "percentage"
                            else "source_scale"
                        )
                    ),
                    status=status,
                    method=str(row.get("method") or row.get("score_method") or table),
                    context={
                        **{f: row[f] for f in CONTEXT_FIELDS if row.get(f) not in (None, "")},
                        "source_table": table,
                        "source_column": column,
                    },
                    evidence_refs=[
                        {
                            "path": f"{source_dir}/{table}.csv",
                            "row_index": index,
                            "column": column,
                            "protocol_id": key[0],
                            "dataset_id": key[1],
                        }
                    ],
                )
            )
    return {
        "schema_version": "phenotype-ir-v1",
        "computation_policy": "source_numeric_values_only",
        "phenotypes": [p.model_dump(mode="json") for p in records.values()],
    }


def interpret_phenotypes(
    phenotype_ir: dict[str, Any],
    interpreter: Callable[[dict[str, Any], int], list[dict[str, Any]]] | None,
    trajectories: int = 3,
) -> dict[str, Any]:
    if not 2 <= trajectories <= 5:
        raise ValueError("Interpretation trajectories must be between 2 and 5.")
    if interpreter is None:
        return {
            "schema_version": "phenotype-interpretation-v1",
            "status": "not_requested",
            "trajectories": [],
            "consensus": [],
        }
    metric_ids = {m["id"] for p in phenotype_ir["phenotypes"] for m in p["metrics"] if m["value"] is not None}
    runs, errors = [], []
    for index in range(trajectories):
        try:
            claims = interpreter(deepcopy(phenotype_ir), index)
            if not isinstance(claims, list) or len(claims) > 100:
                raise ValueError("Each interpretation trajectory must return at most 100 claims.")
            seen: set[str] = set()
            for claim in claims:
                if not claim.get("claim_id") or claim["claim_id"] in seen:
                    raise ValueError("Interpretation requires unique claim ids per trajectory.")
                if not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
                    raise ValueError("An explicit scientific statement is required.")
                if claim.get("conclusion") not in {"supports", "refutes", "uncertain"}:
                    raise ValueError("Unknown interpretation conclusion.")
                if not claim.get("metric_ids") or not set(claim["metric_ids"]).issubset(metric_ids):
                    raise ValueError("Interpretation must reference observed numeric metric ids.")
                seen.add(claim["claim_id"])
            runs.append({"trajectory": index, "claims": claims})
        except Exception as exc:
            errors.append({"trajectory": index, "error_type": type(exc).__name__, "message": str(exc)})
    claims_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        for claim in run["claims"]:
            claims_by_id[claim["claim_id"]].append(claim)
    consensus = []
    for claim_id, claims in sorted(claims_by_id.items()):
        conclusions = {c["conclusion"] for c in claims}
        statements = {c["statement"].strip() for c in claims}
        agreed = (
            len(claims) == trajectories
            and len(conclusions) == 1
            and len(statements) == 1
            and "uncertain" not in conclusions
        )
        consensus.append(
            {
                "claim_id": claim_id,
                "status": "agreement" if agreed else "human_review_required",
                "conclusions": sorted(conclusions),
                "statements": sorted(statements),
                "metric_ids": sorted({m for c in claims for m in c["metric_ids"]}),
            }
        )
    return {
        "schema_version": "phenotype-interpretation-v1",
        "status": "agreement"
        if consensus and all(c["status"] == "agreement" for c in consensus) and not errors
        else "human_review_required",
        "policy": "independent_interpretations_of_one_numeric_snapshot; agreement_is_not_causal_proof",
        "trajectories": runs,
        "consensus": consensus,
        "errors": errors,
    }


def make_phenotype_interpreter(client: LLMClient, claims: list[dict[str, str]], max_metrics: int = 200) -> Callable:
    statements = {claim["claim_id"]: claim["statement"] for claim in claims}
    if not statements or len(statements) != len(claims) or any(not k or not v.strip() for k, v in statements.items()):
        raise ValueError("Interpretation requires distinct claim_id/statement pairs.")
    if not 1 <= max_metrics <= 1000:
        raise ValueError("Interpretation max_metrics must be between 1 and 1000.")

    def interpret(phenotype_ir: dict[str, Any], trajectory: int) -> list[dict[str, Any]]:
        metrics = [
            {"protocol_id": p["protocol_id"], "dataset_id": p["dataset_id"], **m}
            for p in phenotype_ir["phenotypes"]
            for m in p["metrics"]
            if m["value"] is not None
        ]
        selected = metrics[:max_metrics]
        result = client.complete_json(
            "phenotype_interpretation",
            {
                "trajectory": trajectory,
                "claims_to_assess": claims,
                "metrics": selected,
                "metrics_total": len(metrics),
                "metrics_included": len(selected),
                "instructions": "Return claims with exactly the provided claim_id and statement, conclusion (supports/refutes/uncertain), metric_ids, and rationale. Use only observed metrics; do not infer causal effects. Missing or insufficient evidence means uncertain.",
                "evidence_quote": str(selected),
            },
            "phenotype-interpretation-claims-v1",
        )
        if not result or not isinstance(result.get("claims"), list):
            raise ValueError("No structured interpretation was returned.")
        if {claim.get("claim_id") for claim in result["claims"]} != set(statements):
            raise ValueError("Interpreter must assess every requested scientific statement.")
        for claim in result["claims"]:
            if statements.get(claim.get("claim_id")) != claim.get("statement"):
                raise ValueError("Interpreter changed the scientific question being assessed.")
            if not set(claim.get("metric_ids", [])).issubset({metric["id"] for metric in selected}):
                raise ValueError("Interpreter cited a metric outside its supplied snapshot.")
        return result["claims"]

    return interpret
