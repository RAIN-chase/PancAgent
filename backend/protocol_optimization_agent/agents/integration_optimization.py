from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol_optimization_agent.io import read_csv_dicts, read_jsonl, write_csv_dicts, write_json


class IntegrationOptimizationAgent:
    """Combines protocol factors and h5ad phenotypes into evidence-labeled recommendations."""

    def __init__(self, output_dir: Path, objective: dict[str, Any]) -> None:
        self.output_dir = output_dir
        self.objective = objective

    def run(self, protocol_outputs: dict[str, Path], h5ad_outputs: dict[str, Path]) -> dict[str, Path]:
        stage_rows = read_csv_dicts(protocol_outputs["stage_factor_matrix_or_events_summary"])
        summaries = read_jsonl(h5ad_outputs["h5ad_summary_by_protocol"])
        maturity_rows = read_csv_dicts(h5ad_outputs["maturity_scores"])
        similarity_rows = read_csv_dicts(h5ad_outputs["similarity_scores"])
        composition_rows = read_csv_dicts(h5ad_outputs["cell_type_composition"])

        recommendations = _build_initial_recommendations(
            stage_rows=stage_rows,
            summaries=summaries,
            maturity_rows=maturity_rows,
            similarity_rows=similarity_rows,
            composition_rows=composition_rows,
            objective=self.objective,
        )

        outputs = {
            "proposed_improved_protocol_md": self.output_dir / "proposed_improved_protocol.md",
            "proposed_improved_protocol_json": self.output_dir / "proposed_improved_protocol.json",
            "factor_recommendation_table": self.output_dir / "factor_recommendation_table.csv",
            "evidence_and_risk_report": self.output_dir / "evidence_and_risk_report.json",
        }
        protocol_doc = {
            "objective": self.objective,
            "recommendation_policy": "correlation_is_not_causality",
            "recommendations": recommendations,
        }
        write_json(outputs["proposed_improved_protocol_json"], protocol_doc)
        write_csv_dicts(outputs["factor_recommendation_table"], recommendations)
        write_json(
            outputs["evidence_and_risk_report"],
            {
                "risk_policy": "All protocol changes require experimental validation before adoption.",
                "recommendations": recommendations,
            },
        )
        outputs["proposed_improved_protocol_md"].write_text(_render_markdown(protocol_doc), encoding="utf-8")
        return outputs


def _build_initial_recommendations(
    stage_rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    maturity_rows: list[dict[str, Any]],
    similarity_rows: list[dict[str, Any]],
    composition_rows: list[dict[str, Any]],
    objective: dict[str, Any],
) -> list[dict[str, Any]]:
    available_protocols = sorted({row.get("protocol_id", "") for row in stage_rows if row.get("protocol_id")})
    phenotype_protocols = sorted({row.get("protocol_id", "") for row in summaries if row.get("protocol_id")})
    evidence_note = (
        f"Protocol factors available for {len(available_protocols)} protocol(s); "
        f"h5ad summaries available for {len(phenotype_protocols)} protocol(s)."
    )
    evidence_detail = (
        f"maturity_rows={len(maturity_rows)}; similarity_rows={len(similarity_rows)}; "
        f"composition_rows={len(composition_rows)}"
    )

    return [
        {
            "recommendation_id": "rec_001",
            "category": "requires_experimental_validation",
            "target": objective.get("primary", "increase_beta_like_maturity"),
            "proposed_change": "Prioritize protocol factors repeatedly associated with higher maturity and similarity scores after real h5ad scoring is connected.",
            "source_basis": evidence_note,
            "protocol_factor": "to_be_ranked_from_protocol_factor_difference_report",
            "h5ad_result": evidence_detail,
            "confidence": "low_until_real_scores_available",
            "risk": "Current scaffold does not infer factor effect direction without numeric phenotype metrics.",
            "requires_experimental_validation": "true",
        }
    ]


def _render_markdown(protocol_doc: dict[str, Any]) -> str:
    lines = [
        "# Proposed Improved Pancreatic / Islet Organoid Differentiation Protocol",
        "",
        "## Objective",
        "",
        f"- Primary: `{protocol_doc['objective'].get('primary', 'not_specified')}`",
        "",
        "## Recommendation Policy",
        "",
        "- Associations are treated as hypotheses, not causal proof.",
        "- Every proposed modification must remain traceable to protocol factors and h5ad phenotype results.",
        "- Experimental validation is required before adopting a modified protocol.",
        "",
        "## Recommendations",
        "",
    ]
    for rec in protocol_doc["recommendations"]:
        lines.extend(
            [
                f"### {rec['recommendation_id']}",
                "",
                f"- Category: `{rec['category']}`",
                f"- Target: {rec['target']}",
                f"- Proposed change: {rec['proposed_change']}",
                f"- Source basis: {rec['source_basis']}",
                f"- Protocol factor: {rec['protocol_factor']}",
                f"- H5AD result: {rec['h5ad_result']}",
                f"- Confidence: {rec['confidence']}",
                f"- Risk: {rec['risk']}",
                f"- Requires experimental validation: {rec['requires_experimental_validation']}",
                "",
            ]
        )
    return "\n".join(lines)
