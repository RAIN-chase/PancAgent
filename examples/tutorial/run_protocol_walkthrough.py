"""Run the existing PA graph on synthetic data without an LLM or API key."""

from math import isclose
from pathlib import Path

from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.analysis.protocol.input_loader import load_protocol_analysis_inputs
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.io import read_csv_dicts, read_json


def main() -> None:
    example_dir = Path(__file__).resolve().parent
    repository = example_dir.parents[1]

    # The loader projects the formal JSONL into the PA's internal records/events.
    loaded = load_protocol_analysis_inputs(example_dir)
    print(f"1. Source: {loaded['protocol_record_source']}")
    print(f"   Protocols: {len(loaded['protocol_records'])}; events: {len(loaded['factor_events'])}")

    # No client is supplied: ProtocolAnalysisAgent uses OfflineLLMClient.
    # The existing run decorator creates a fresh archive beneath this directory.
    outputs = ProtocolAnalysisAgent(
        repository / "runs" / "tutorial",
        {"min_protocol_confidence": 0.65, "require_comparison_ready": True},
    ).run(loaded["protocol_records"], loaded["factor_events"], loaded["source_identity_rows"])

    matrix = {row["protocol_id"]: row for row in read_csv_dicts(outputs["protocol_feature_matrix"])}
    feature = "all_reagent_activin_a_max_concentration"
    assert set(matrix) == {"tutorial_a", "tutorial_b"}, "Both teaching protocols must survive filtering."
    assert isclose(float(matrix["tutorial_a"][feature]), 0.1)
    assert isclose(float(matrix["tutorial_b"][feature]), 0.05)
    assert all(row[f"{feature}_unit"] == "ug/ml" for row in matrix.values())
    print(f"2. Deterministic feature: {feature}; A=0.1, B=0.05 ug/ml (standardized)")

    engine = ProtocolQueryEngine(outputs["protocol_feature_matrix"].parent)
    comparison = engine.compare_protocols("tutorial_a", "tutorial_b")
    assert comparison["status"] == "ok" and comparison["feature_differences"]
    evidence = engine.get_evidence_for_feature("tutorial_a", feature)
    assert "Synthetic example A" in str(evidence), "The computed dose must retain its source evidence."
    print(f"3. Comparison: {comparison['status']}; differences: {len(comparison['feature_differences'])}")
    print(f"4. Evidence: {evidence}")

    gate = read_json(outputs["matrix_release_gate_report"])
    print(f"5. Release gate: {gate}")
    print("   Synthetic fixtures demonstrate software behavior, not biological validation.")
    print(f"OUTPUT_DIR={outputs['protocol_feature_matrix'].parent}")
    print(f"MATRIX={outputs['protocol_feature_matrix']}")


if __name__ == "__main__":
    main()
