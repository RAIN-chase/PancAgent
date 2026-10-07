from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from importlib.metadata import version
from pathlib import Path
from typing import Any

from protocol_optimization_agent.analysis.phenotype.h5ad_tools import (
    _common_dataset_fields,
    _read_h5ad_metadata,
    _select_manifest_cells,
    normalize_manifest_row,
)
from protocol_optimization_agent.runtime.archive import audited_operation, current_archive, file_hash
from protocol_optimization_agent.runtime.io import read_csv_dicts

METHOD_VERSION = "early-late-scanpy-v1"
CELL_TYPES = ("Alpha", "Beta", "Delta")
DEFAULT_PROGRAM_DIR = Path(__file__).resolve().parents[4] / "inputs" / "Gene_Programs"
CAVEAT = (
    "Late minus Early is a descriptive, background-corrected transcriptional program shift, "
    "not a maturity probability or causal effect. Dataset-dependent control bins and gene "
    "coverage limit cross-dataset comparisons; cells are not independent biological replicates."
)


def _gene_key(value: Any) -> str:
    return re.sub(r"\.\d+$", "", str(value).strip().upper())


def _cell_type(value: Any) -> str:
    label = str(value).strip().lower().replace("_", "-")
    return label.removeprefix("sc-").capitalize()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@audited_operation
def load_maturity_programs(program_dir: Path | str | None = None,
                          cell_types: list[str] | None = None) -> dict[str, Any]:
    """Load unweighted Early/Late membership lists and immutable source identities."""
    directory = Path(program_dir or os.getenv("PROTOCOL_MATURITY_PROGRAM_DIR") or DEFAULT_PROGRAM_DIR)
    targets = [_cell_type(item) for item in (cell_types if cell_types is not None else CELL_TYPES)]
    if not targets or len(set(targets)) != len(targets) or any(item not in CELL_TYPES for item in targets):
        raise ValueError("maturity.cell_types must contain distinct Alpha, Beta or Delta labels")
    programs: dict[str, Any] = {}
    for target in targets:
        programs[target] = {}
        for phase in ("Early", "Late"):
            path = directory / f"{target}_Stability_{phase}_Program.csv"
            identity = file_hash(path)
            archive = current_archive()
            if archive:
                archive.inputs({f"{target}_{phase}": str(path)})
                path_to_read = archive.root / "inputs" / identity / path.name
            else:
                path_to_read = path
            records = read_csv_dicts(path_to_read)
            if not records or "gene" not in records[0]:
                raise ValueError(f"Program CSV requires a nonempty gene column: {path}")
            genes = list(dict.fromkeys(_gene_key(row["gene"]) for row in records
                                      if _gene_key(row["gene"]) not in {"", "NAN", "NONE"}))
            if not genes:
                raise ValueError(f"No valid genes in {path}")
            programs[target][phase] = {"genes": genes, "path": str(path), "sha256": identity}
    return programs


@audited_operation
def compute_maturity_scores(row: dict[str, Any], h5ad_config: dict[str, Any],
                            programs: dict[str, Any], cell_scores_path: Path) -> dict[str, Any]:
    """Notebook-equivalent counts -> normalize/log1p -> Scanpy Early/Late scores.

    Only target-cell matrices are materialized; full-source count validation and
    all-zero gene detection are streamed. Scoring is NOT split into independent
    chunks because that would change Scanpy's expression bins and control genes.
    """
    normalized = normalize_manifest_row(row, h5ad_config)
    common = _common_dataset_fields(normalized)
    config = h5ad_config.get("maturity", {})
    params = {"target_sum": 10000.0, "n_bins": 25, "ctrl_size": 50, "random_state": 0,
              "min_target_cells": 20, "min_genes": 5, "chunk_size": 4096,
              "max_matrix_bytes": 2_000_000_000, **config}
    audit: dict[str, Any] = {"schema_version": "maturity-audit-v1", **common,
                            "method": METHOD_VERSION, "parameters": params,
                            "programs": programs, "confidence_caveats": [CAVEAT], "lineages": {}}
    rows: list[dict[str, Any]] = []

    def failure(status: str, message: str) -> dict[str, Any]:
        audit.update(status=status, message=message)
        return {"rows": [{**common, "cell_type": target, "lineage": target, "status": status,
                          "maturity_score": "", "message": message} for target in programs], "audit": audit}

    try:
        import anndata as ad
        import h5py
        import numpy as np
        import scanpy as sc
        from scipy import sparse
    except ImportError as exc:
        return failure("dependency_missing", str(exc))
    try:
        if config.get("preprocessing", "counts_normalize_log1p") != "counts_normalize_log1p":
            raise ValueError("Only counts_normalize_log1p preprocessing is implemented")
        for key in ("target_sum", "ctrl_size", "min_target_cells", "min_genes", "chunk_size", "max_matrix_bytes"):
            if float(params[key]) <= 0 or not np.isfinite(float(params[key])):
                raise ValueError(f"{key} must be positive and finite")
        for key in ("ctrl_size", "n_bins", "min_target_cells", "min_genes", "chunk_size", "max_matrix_bytes", "random_state"):
            if int(params[key]) != params[key]:
                raise ValueError(f"{key} must be an integer")
            params[key] = int(params[key])
        if params["n_bins"] < 2 or params["random_state"] < 0:
            raise ValueError("n_bins must be >=2 and random_state must be nonnegative")
        metadata = _read_h5ad_metadata(Path(normalized["h5ad_path"]))
        selection = _select_manifest_cells(metadata, normalized)
        if selection["status"] != "ok":
            return failure(selection["status"], selection["message"])
        column = normalized["cell_type_column"]
        if column not in metadata.obs:
            return failure("obs_column_missing", f"Missing cell-type annotation: {column}")
        names = list(map(str, metadata.var_names))
        keys = [_gene_key(name) for name in names]
        gene_indexes = {key: index for index, key in enumerate(keys)}
        if len(set(keys)) != len(keys):
            return failure("ambiguous_gene_identifiers", "Case/version-normalized gene names are not unique")
        labels = metadata.obs[column].map(_cell_type).to_numpy()
        selected = selection["mask"]
        quality_column = config.get("mapping_status_column")
        if quality_column:
            if quality_column not in metadata.obs or not config.get("mapping_status_values"):
                raise ValueError("Mapping filter requires an existing column and mapping_status_values")
            selected &= metadata.obs[quality_column].isin(config["mapping_status_values"]).to_numpy()
        with h5py.File(normalized["h5ad_path"], "r") as handle:
            layer = str(config.get("counts_layer", "counts"))
            source = f"layers/{layer}" if layer and f"layers/{layer}" in handle else "X"
            if config.get("counts_layer") and source == "X":
                return failure("counts_source_missing", f"Explicit counts layer {layer!r} is missing")
            if source not in handle:
                return failure("counts_source_missing", "No counts layer or X matrix")
            node = handle[source]
            matrix = ad.io.sparse_dataset(node) if isinstance(node, h5py.Group) else node
            if tuple(matrix.shape) != (metadata.n_obs, metadata.n_vars):
                raise ValueError("Count matrix shape does not match obs/var")
            bytes_per_row = max(1, metadata.n_vars * matrix.dtype.itemsize)
            if bytes_per_row * 8 > params["max_matrix_bytes"]:
                raise ValueError("Count row exceeds maturity.max_matrix_bytes working-memory guard")
            scan_chunk_size = min(params["chunk_size"], max(1, params["max_matrix_bytes"] // (bytes_per_row * 8)))
            nonzero = np.zeros(metadata.n_vars, dtype=bool)
            # All source cells define the pool, including cells excluded by manifest filters.
            for start in range(0, metadata.n_obs, scan_chunk_size):
                block = matrix[start:start + scan_chunk_size, :]
                values = block.data if sparse.issparse(block) else np.asarray(block).ravel()
                if (not np.all(np.isfinite(values)) or np.any(values < 0)
                        or not np.allclose(values, np.round(values), rtol=0, atol=1e-6)):
                    return failure("invalid_raw_counts", "Counts must be finite, nonnegative and integer-like; no normalized-X fallback")
                nonzero |= np.asarray(block.sum(axis=0)).ravel() > 0
            pool = [name for name, present in zip(names, nonzero, strict=True) if present]
            if len(pool) < 10:
                return failure("insufficient_gene_pool", "At least 10 nonzero source genes are required")
            audit.update(expression_source=source, scanpy_version=version("scanpy"),
                         gene_pool_sha256=_digest(pool), n_pool_genes=len(pool),
                         cell_filter=selection["filter_summary"], annotation_column=column,
                         counts_validation="integer_like_full_source_scan")
            cell_scores_path.parent.mkdir(parents=True, exist_ok=True)
            with cell_scores_path.open("w", encoding="utf-8", newline="") as out:
                fields = [*common, "cell_id", "cell_type", "early_score", "late_score", "maturity_score"]
                writer = csv.DictWriter(out, fieldnames=fields)
                writer.writeheader()
                for target, definitions in programs.items():
                    indexes = np.flatnonzero(selected & (labels == target))
                    used: dict[str, list[str]] = {}
                    coverage: dict[str, Any] = {}
                    for phase, definition in definitions.items():
                        input_genes = definition["genes"]
                        lookup = dict(zip(keys, names, strict=True))
                        used[phase] = [lookup[gene] for gene in input_genes if gene in lookup and nonzero[gene_indexes[gene]]]
                        coverage[phase] = {"n_input": len(input_genes), "n_used": len(used[phase]),
                                           "coverage": len(used[phase]) / len(input_genes), "used": used[phase],
                                           "missing": [gene for gene in input_genes if gene not in lookup],
                                           "all_zero": [gene for gene in input_genes if gene in lookup and not nonzero[gene_indexes[gene]]]}
                    audit["lineages"][target] = {"n_cells": len(indexes), "coverage": coverage}
                    base = {**common, "cell_type": target, "lineage": target, "n_cells": len(indexes)}
                    if len(indexes) < params["min_target_cells"] or any(len(genes) < params["min_genes"] for genes in used.values()):
                        rows.append({**base, "status": "insufficient_cells_or_program_genes", "maturity_score": ""})
                        continue
                    union = set(used["Early"]) | set(used["Late"])
                    ctrl_size = min(params["ctrl_size"], max(1, len(pool) - len(union)))
                    pieces = []
                    bytes_used = 0
                    for start in range(0, len(indexes), params["chunk_size"]):
                        piece = sparse.csr_matrix(matrix[indexes[start:start + params["chunk_size"]], :], dtype=np.float32)
                        bytes_used += piece.data.nbytes + piece.indices.nbytes + piece.indptr.nbytes
                        if bytes_used * 3 > params["max_matrix_bytes"]:
                            raise ValueError("Selected cell matrix exceeds maturity.max_matrix_bytes; select a smaller scientific cohort")
                        pieces.append(piece)
                    target_data = ad.AnnData(sparse.vstack(pieces, format="csr"),
                                             obs=metadata.obs.iloc[indexes].copy(), var=metadata.var.copy())
                    if np.any(np.asarray(target_data.X.sum(axis=1)).ravel() <= 0):
                        rows.append({**base, "status": "zero_library_target_cells", "maturity_score": ""})
                        continue
                    sc.pp.normalize_total(target_data, target_sum=params["target_sum"])
                    sc.pp.log1p(target_data)
                    try:
                        for phase in ("Early", "Late"):
                            sc.tl.score_genes(target_data, gene_list=used[phase], gene_pool=pool,
                                              ctrl_size=ctrl_size, n_bins=params["n_bins"],
                                              score_name=f"{phase.lower()}_score", random_state=params["random_state"],
                                              use_raw=False, ctrl_as_ref=True)
                    except RuntimeError as exc:
                        rows.append({**base, "status": "control_gene_selection_failed", "maturity_score": "", "message": str(exc)})
                        continue
                    early = target_data.obs["early_score"].to_numpy(dtype=np.float32)
                    late = target_data.obs["late_score"].to_numpy(dtype=np.float32)
                    delta = (late - early).astype(np.float32)
                    if not np.all(np.isfinite(delta)):
                        rows.append({**base, "status": "nonfinite_scores", "maturity_score": ""})
                        continue
                    signature = _digest({"programs": {phase: definition["sha256"] for phase, definition in definitions.items()},
                                         "used": used, "pool": pool,
                                         "params": {key: params[key] for key in ("target_sum", "n_bins", "random_state")},
                                         "scanpy": version("scanpy"), "ctrl_size": ctrl_size, "ctrl_as_ref": True})
                    audit["lineages"][target].update(effective_ctrl_size=ctrl_size, method_signature=signature)
                    rows.append({**base, "status": "ok", "early_score": float(np.mean(early)),
                                 "late_score": float(np.mean(late)), "maturity_score": float(np.median(delta)),
                                 "mean_maturity_score": float(np.mean(delta)), "std_maturity_score": float(np.std(delta)),
                                 "aggregation": "median_cell_delta", "endpoint": f"{target.lower()}_maturity",
                                 "method": "scanpy.score_genes.late_minus_early", "method_version": f"{METHOD_VERSION}:{signature}",
                                 "unit": "module_score_difference", "expression_source": source,
                                 "expression_scale": "log1p", "normalization": f"normalize_total_{params['target_sum']:g}_log1p",
                                 "cell_population": target, "n_early_genes": len(used["Early"]), "n_late_genes": len(used["Late"]),
                                 "evidence_refs": [{"path": definition["path"], "sha256": definition["sha256"]}
                                                   for definition in definitions.values()]})
                    for index, early_value, late_value, delta_value in zip(indexes, early, late, delta, strict=True):
                        cell_common = dict(common)
                        sample_column = normalized["sample_column"]
                        if not common["sample_id"] and sample_column in metadata.obs:
                            cell_common["sample_id"] = str(metadata.obs.iloc[index][sample_column])
                        writer.writerow({**cell_common, "cell_id": str(metadata.obs.index[index]), "cell_type": target,
                                         "early_score": float(early_value), "late_score": float(late_value), "maturity_score": float(delta_value)})
        audit["status"] = "ok" if rows and all(item["status"] == "ok" for item in rows) else "partial"
        return {"rows": rows, "audit": audit, "cell_scores_path": cell_scores_path}
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        return failure("maturity_analysis_failed", str(exc))
