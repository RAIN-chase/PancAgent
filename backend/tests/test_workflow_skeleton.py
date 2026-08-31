from __future__ import annotations

from protocol_optimization_agent.workflow import WORKFLOW_EDGES, WORKFLOW_NODE_ORDER, WORKFLOW_NODES


def test_workflow_skeleton_contains_expected_nodes() -> None:
    assert WORKFLOW_NODE_ORDER == [
        "validate_inputs",
        "load_protocol_artifacts",
        "load_h5ad_manifest",
        "analyze_protocol_factors",
        "analyze_h5ad_profiles",
        "integrate_protocol_and_h5ad_results",
        "generate_improved_protocol",
        "qc_final_recommendations",
        "export_results",
    ]
    assert set(WORKFLOW_NODES) == set(WORKFLOW_NODE_ORDER)


def test_workflow_skeleton_is_linear_dag() -> None:
    assert WORKFLOW_EDGES == list(zip(WORKFLOW_NODE_ORDER, WORKFLOW_NODE_ORDER[1:]))
