"""Freeze or verify deterministic scientific artifacts, without an LLM or h5ad."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.analysis.phenotype.representation import build_phenotype_ir
from protocol_optimization_agent.analysis.protocol.input_loader import load_protocol_analysis_inputs
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine, _feature_importance_group

CASE = Path(__file__).resolve().parent
ARTIFACTS = (
    "protocol_feature_matrix", "protocol_feature_matrix_raw", "normalized_factor_events",
    "standardized_protocol_representations", "protocol_workflow_graphs",
    "protocol_feature_provenance", "feature_cell_provenance",
    "schema_validation_report", "matrix_release_gate_report",
)


def _read(path: Path) -> Any:
    if path.suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            return {"columns": reader.fieldnames, "rows": rows}
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(output_dir: Path) -> dict[str, Any]:
    config = _read(CASE / "config.json")
    loaded = load_protocol_analysis_inputs(CASE)
    # A stable repository-relative source locator keeps evidence IDs portable.
    for event in loaded["factor_events"]:
        event["source_file"] = "evals/architecture_baseline/protocols.jsonl"
    outputs = ProtocolAnalysisAgent(output_dir, config["quality_filters"]).run(
        loaded["protocol_records"], loaded["factor_events"], loaded["source_identity_rows"]
    )
    snapshot = {name: _read(outputs[name]) for name in ARTIFACTS}
    engine = ProtocolQueryEngine(outputs["protocol_feature_matrix"].parent)
    snapshot["comparison"] = engine.compare_protocols("baseline_a", "baseline_b", max_features=1000)
    snapshot["dose_evidence"] = engine.get_evidence_for_feature(
        "baseline_a", "all_reagent_activin_a_max_concentration"
    )
    snapshot["phenotype_ir"] = build_phenotype_ir(
        {"gene_expression_summary": _read(CASE / "gene_expression_summary.csv")["rows"]},
        [], {"validation_status": "synthetic_fixture"}, config["phenotype_source"],
    )
    serialized = json.dumps(snapshot, ensure_ascii=True)
    # Only storage roots vary; scientific evidence fields and IDs remain intact.
    for root, token in ((str(outputs["protocol_feature_matrix"].parent), "$PA"), (str(CASE), "$CASE")):
        serialized = serialized.replace(json.dumps(root)[1:-1], token)
        serialized = serialized.replace(root.replace("\\", "/"), token)
    return json.loads(serialized)


def legacy_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project only the explicitly additive source-detail field out of the frozen contract."""
    return {**snapshot, "comparison": {key: value for key, value in snapshot["comparison"].items()
                                      if key != "event_step_diff"}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="Create the pre-migration baseline once.")
    parser.add_argument("--output-dir", type=Path, default=CASE.parents[1] / "runs" / "architecture_baseline")
    args = parser.parse_args()
    expected_path = CASE / "expected.json"
    if args.record and expected_path.exists():
        raise FileExistsError("The baseline is frozen; do not overwrite it during migration.")
    snapshot = evaluate(args.output_dir)
    if args.record:
        from protocol_optimization_agent.analysis.protocol.feature_groups import feature_group as _feature_group

        columns = snapshot["protocol_feature_matrix"]["columns"]
        classification = {name: {"pa": _feature_group(name), "query": _feature_importance_group(name)}
                          for name in columns}
        packages = {}
        for name in ("pytest", "langgraph", "pydantic", "numpy", "anndata"):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = "not_installed"
        metadata = {"python": platform.python_version(), "packages": packages,
                    "input_sha256": {name: hashlib.sha256((CASE / name).read_bytes()).hexdigest()
                                     for name in ("protocols.jsonl", "gene_expression_summary.csv", "config.json")}}
        for path, value in ((expected_path, snapshot), (CASE / "classification_before.json", classification),
                            (CASE / "environment_before.json", metadata)):
            path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print("Recorded pre-migration scientific baseline.")
    else:
        if legacy_snapshot(snapshot) != _read(expected_path):
            raise AssertionError("Scientific baseline changed; inspect values, units, missingness and evidence.")
        print("Scientific baseline unchanged (matrices, units, missingness, evidence, query and Phenotype IR).")


if __name__ == "__main__":
    main()
