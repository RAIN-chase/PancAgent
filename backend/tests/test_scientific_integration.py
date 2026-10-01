from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from protocol_optimization_agent.action_space import action_registry
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.orchestrator_agent import (
    _optimization_cache_key,
    build_orchestrator_graph,
    execute_scientific_action,
)
from protocol_optimization_agent.runtime import RunContext
from protocol_optimization_agent.scientific_models import DecisionAssessment, ExperimentCandidate, Hypothesis
from protocol_optimization_agent.scientific_state import ScientificWorldModel


def test_orchestrator_action_compares_protocols_and_persists_state(tmp_path: Path) -> None:
    protocol_dir = tmp_path / "pa"
    protocol_dir.mkdir()
    matrix = "protocol_id,paper_id,protocol_name,reagent_x,cell_line,stage\np1,a,A,1,line1,S6\np2,b,B,0,line1,S6\n"
    (protocol_dir / "protocol_feature_matrix.csv").write_text(matrix, encoding="utf-8")

    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, tools):
            return self

    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute_scientific_action",
                        "args": {
                            "action_id": "protocol.compare",
                            "arguments": {"protocol_id_a": "p1", "protocol_id_b": "p2"},
                        },
                        "id": "comparison",
                    }
                ],
            ),
            AIMessage(content="Evidence-grounded comparison returned."),
        ]
    )
    result = build_orchestrator_graph(model).invoke(
        {
            "messages": [HumanMessage(content="Compare p1 and p2")],
            "protocol_output_dir": str(protocol_dir),
            "workspace_root": str(tmp_path),
            "user_id": "alice",
            "run_id": "run1",
            "project_id": "pancreas",
        }
    )
    response = json.loads(next(m.content for m in result["messages"] if isinstance(m, ToolMessage)))
    assert response["status"] == "ok"
    assert response["result"]["tool_result"]["feature_differences"]
    assert response["scientific_preconditions"]["status"] == "descriptive_only"
    store = ScientificWorldModel(
        RunContext.from_values(user_id="alice", run_id="run2", workspace_root=tmp_path), "pancreas"
    )
    assert store.snapshot()["counts"]["protocol"] == 2
    assert store.snapshot()["counts"]["claim"] == 1
    assert (protocol_dir / "protocol_feature_matrix.csv").read_text(encoding="utf-8") == matrix


def test_action_registry_deferred_extraction_and_runtime_argument_gate(tmp_path: Path) -> None:
    injected = {
        "protocol_output_dir": str(tmp_path / "pa"),
        "data_analysis_output_dir": str(tmp_path / "da"),
        "optimization_output_dir": str(tmp_path / "po"),
        "max_features": 10,
        "workspace_root": str(tmp_path),
    }
    result = execute_scientific_action.invoke(
        {"action_id": "protocol.extract", "arguments": {"source_artifact_id": "paper1"}, **injected}
    )
    assert result["status"] == "deferred"
    assert result["executed"] is False
    assert result["action"]["implementation"] == "Protocol_extraction"
    with pytest.raises(ValueError, match="undeclared"):
        execute_scientific_action.invoke(
            {
                "action_id": "protocol.lookup",
                "arguments": {"question": "A", "workspace_root": "/another-user"},
                **injected,
            }
        )
    ids = {a["id"] for a in action_registry()["actions"]}
    assert {"protocol.compare", "experiment.propose", "scientific.state"}.issubset(ids)


def test_da_exports_separate_numeric_ir_and_interpretation(tmp_path: Path) -> None:
    outputs = DataAnalysisAgent(output_dir=tmp_path / "da").run(
        [{"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(tmp_path / "missing.h5ad")}]
    )
    ir = json.loads(outputs["phenotype_ir"].read_text(encoding="utf-8"))
    interpretation = json.loads(outputs["phenotype_interpretation"].read_text(encoding="utf-8"))
    assert ir["schema_version"] == "phenotype-ir-v1"
    assert interpretation["status"] == "not_requested"
    assert all(m["value"] is None for p in ir["phenotypes"] for m in p["metrics"])


def test_experiment_review_invalidates_po_cache(tmp_path: Path) -> None:
    store = ScientificWorldModel(RunContext.from_values(user_id="alice", workspace_root=tmp_path), "study")
    store.put(
        [
            Hypothesis(id="h1", statement="Test X", feature="x", objective="y"),
            ExperimentCandidate(
                id="exp1",
                hypothesis_ids=["h1"],
                intervention={},
                eligible=True,
                utility=0.5,
                evidence_refs=[{"artifact_id": "synthetic_fixture"}],
                scientific_preconditions={"scientific_ready": True},
                assessment=DecisionAssessment(
                    expected_improvement=0.5,
                    information_gain=0.2,
                    experimental_cost=0.1,
                    risk=0.1,
                    source="synthetic_fixture",
                    rationale="Simulated matched experiment",
                ),
            ),
        ],
        "S5",
    )
    key1 = _optimization_cache_key(tmp_path / "pa", tmp_path / "da", {}, {}, store.feedback_fingerprint())
    store.review("exp1", True, "Alice", "reviewed")
    key2 = _optimization_cache_key(tmp_path / "pa", tmp_path / "da", {}, {}, store.feedback_fingerprint())
    assert key1 != key2
    data_dir = tmp_path / "da"
    data_dir.mkdir()
    (data_dir / "phenotype_interpretation.json").write_text('{"status":"human_review_required"}', encoding="utf-8")
    key3 = _optimization_cache_key(tmp_path / "pa", data_dir, {}, {}, store.feedback_fingerprint())
    assert key2 != key3


def test_da_configured_interpreter_receives_numeric_snapshot_without_recomputing(tmp_path: Path) -> None:
    class InterpretationClient:
        def __init__(self):
            self.calls = []

        def complete_json(self, task, payload, schema_name):
            self.calls.append(payload)
            return {
                "claims": [
                    {
                        "claim_id": "c1",
                        "statement": "Beta markers are expressed",
                        "metric_ids": [payload["metrics"][0]["id"]],
                        "conclusion": "supports",
                    }
                ]
            }

    client = InterpretationClient()
    agent = DataAnalysisAgent(
        output_dir=tmp_path / "da",
        h5ad_config={
            "interpretation": {
                "enabled": True,
                "trajectories": 2,
                "claims": [{"claim_id": "c1", "statement": "Beta markers are expressed"}],
            }
        },
        interpretation_llm_client=client,
    )
    rows = [{"protocol_id": "p1", "dataset_id": "d1", "gene": "INS", "mean_expression": 0.6, "status": "ok"}]
    result = agent._node_phenotype_representation(
        {"gene_expression_rows": rows, "validation_report": {"validation_status": "passed"}}
    )
    assert len(client.calls) == 2
    assert client.calls[0]["metrics"] == client.calls[1]["metrics"]
    assert result["interpretation_report"]["status"] == "agreement"
    assert rows[0]["mean_expression"] == 0.6
