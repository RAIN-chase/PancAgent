from __future__ import annotations

from pathlib import Path
from typing import Any


def summarize_h5ad_manifest_entry(row: dict[str, str], h5ad_config: dict[str, Any]) -> dict[str, Any]:
    """Summarize one h5ad manifest row.

    This function is the stable tool boundary for future scanpy/anndata-backed analysis.
    The current implementation is deterministic and reports placeholder metrics when
    the h5ad file is unavailable or h5ad dependencies have not been installed.
    """
    protocol_id = row.get("protocol_id", "")
    dataset_id = row.get("dataset_id", "")
    sample_id = row.get("sample_id", "")
    condition_id = row.get("condition_id", "")
    h5ad_path = Path(row.get("h5ad_path", ""))
    file_exists = h5ad_path.exists()

    limitation = {
        "protocol_id": protocol_id,
        "dataset_id": dataset_id,
        "h5ad_path": str(h5ad_path),
        "limitation": "h5ad_file_not_found" if not file_exists else "h5ad_numeric_analysis_not_yet_connected",
    }

    common = {
        "protocol_id": protocol_id,
        "dataset_id": dataset_id,
        "condition_id": condition_id,
        "sample_id": sample_id,
    }

    return {
        "summary": {
            **common,
            "h5ad_path": str(h5ad_path),
            "h5ad_file_exists": file_exists,
            "analysis_status": "pending_real_h5ad_analysis",
            "cell_type_column": row.get("cell_type_col") or h5ad_config.get("annotation_cell_type_column", "cell_type"),
            "batch_column": row.get("batch_col") or h5ad_config.get("batch_column", "batch_id"),
        },
        "cell_type_composition": [
            {**common, "cell_type": "beta_like", "proportion": "", "n_cells": "", "status": "pending"},
            {**common, "cell_type": "endocrine", "proportion": "", "n_cells": "", "status": "pending"},
            {**common, "cell_type": "off_target", "proportion": "", "n_cells": "", "status": "pending"},
        ],
        "marker_scores": [
            {**common, "marker_set": "beta_maturity", "score": "", "status": "pending"},
            {**common, "marker_set": "stress", "score": "", "status": "pending"},
            {**common, "marker_set": "proliferation", "score": "", "status": "pending"},
        ],
        "maturity_scores": [{**common, "maturity_score": "", "score_method": "external_tool_pending", "status": "pending"}],
        "similarity_scores": [{**common, "similarity_score": "", "reference": "primary_islet_pending", "status": "pending"}],
        "limitations": [limitation],
    }
