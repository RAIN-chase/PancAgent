from __future__ import annotations

from typing import Any, Callable, TypedDict


class WorkflowState(TypedDict, total=False):
    """LangGraph state contract for ProtocolOptimizationAgent.

    Keep this state serializable so future checkpointing and resumable runs remain
    straightforward.
    """

    config_path: str
    run_id: str
    input_paths: dict[str, str]
    missing_inputs: list[str]
    protocol_records: list[dict[str, Any]]
    factor_events: list[dict[str, Any]]
    h5ad_manifest: list[dict[str, Any]]
    protocol_outputs: dict[str, str]
    h5ad_outputs: dict[str, str]
    integration_outputs: dict[str, str]
    warnings: list[str]
    step_log: list[dict[str, Any]]


WorkflowNode = Callable[[WorkflowState], WorkflowState]


WORKFLOW_NODE_ORDER = [
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

WORKFLOW_EDGES = list(zip(WORKFLOW_NODE_ORDER, WORKFLOW_NODE_ORDER[1:]))


def validate_inputs(state: WorkflowState) -> WorkflowState:
    return state


def load_protocol_artifacts(state: WorkflowState) -> WorkflowState:
    return state


def load_h5ad_manifest(state: WorkflowState) -> WorkflowState:
    return state


def analyze_protocol_factors(state: WorkflowState) -> WorkflowState:
    return state


def analyze_h5ad_profiles(state: WorkflowState) -> WorkflowState:
    return state


def integrate_protocol_and_h5ad_results(state: WorkflowState) -> WorkflowState:
    return state


def generate_improved_protocol(state: WorkflowState) -> WorkflowState:
    return state


def qc_final_recommendations(state: WorkflowState) -> WorkflowState:
    return state


def export_results(state: WorkflowState) -> WorkflowState:
    return state


WORKFLOW_NODES: dict[str, WorkflowNode] = {
    "validate_inputs": validate_inputs,
    "load_protocol_artifacts": load_protocol_artifacts,
    "load_h5ad_manifest": load_h5ad_manifest,
    "analyze_protocol_factors": analyze_protocol_factors,
    "analyze_h5ad_profiles": analyze_h5ad_profiles,
    "integrate_protocol_and_h5ad_results": integrate_protocol_and_h5ad_results,
    "generate_improved_protocol": generate_improved_protocol,
    "qc_final_recommendations": qc_final_recommendations,
    "export_results": export_results,
}


def build_langgraph_workflow(compile_graph: bool = True) -> Any:
    """Build the first LangGraph workflow skeleton.

    Node bodies are intentionally empty in this version. Future iterations should
    move the current orchestrator logic into these nodes one by one.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph is not installed. Install the optional dependency with "
            "`pip install -e .[langgraph]` before building this workflow."
        ) from exc

    graph = StateGraph(WorkflowState)
    for node_name in WORKFLOW_NODE_ORDER:
        graph.add_node(node_name, WORKFLOW_NODES[node_name])

    graph.add_edge(START, WORKFLOW_NODE_ORDER[0])
    for source, target in WORKFLOW_EDGES:
        graph.add_edge(source, target)
    graph.add_edge(WORKFLOW_NODE_ORDER[-1], END)

    if compile_graph:
        return graph.compile()
    return graph
