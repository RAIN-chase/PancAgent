from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol_optimization_agent.io import write_csv_dicts, write_json, write_jsonl
from protocol_optimization_agent.tools.h5ad_tools import summarize_h5ad_manifest_entry


class H5ADAnalysisAgent:
    """Runs pluggable h5ad phenotype analyses and writes protocol-level summaries."""

    def __init__(self, output_dir: Path, h5ad_config: dict[str, Any]) -> None:
        self.output_dir = output_dir
        self.h5ad_config = h5ad_config

    def run(self, manifest_rows: list[dict[str, str]]) -> dict[str, Path]:
        summary_records: list[dict[str, Any]] = []
        composition_rows: list[dict[str, Any]] = []
        marker_rows: list[dict[str, Any]] = []
        maturity_rows: list[dict[str, Any]] = []
        similarity_rows: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = []

        for row in manifest_rows:
            result = summarize_h5ad_manifest_entry(row, self.h5ad_config)
            summary_records.append(result["summary"])
            composition_rows.extend(result["cell_type_composition"])
            marker_rows.extend(result["marker_scores"])
            maturity_rows.extend(result["maturity_scores"])
            similarity_rows.extend(result["similarity_scores"])
            limitations.extend(result["limitations"])

        outputs = {
            "h5ad_summary_by_protocol": self.output_dir / "h5ad_summary_by_protocol.jsonl",
            "maturity_scores": self.output_dir / "maturity_scores.csv",
            "similarity_scores": self.output_dir / "similarity_scores.csv",
            "cell_type_composition": self.output_dir / "cell_type_composition.csv",
            "marker_scores": self.output_dir / "marker_scores.csv",
            "h5ad_qc_report": self.output_dir / "h5ad_qc_report.json",
            "h5ad_analysis_limitations": self.output_dir / "h5ad_analysis_limitations.json",
        }
        write_jsonl(outputs["h5ad_summary_by_protocol"], summary_records)
        write_csv_dicts(outputs["maturity_scores"], maturity_rows)
        write_csv_dicts(outputs["similarity_scores"], similarity_rows)
        write_csv_dicts(outputs["cell_type_composition"], composition_rows)
        write_csv_dicts(outputs["marker_scores"], marker_rows)
        write_json(outputs["h5ad_qc_report"], {"n_manifest_rows": len(manifest_rows), "status": "placeholder_or_plugin_backed"})
        write_json(outputs["h5ad_analysis_limitations"], {"limitations": limitations})
        return outputs
