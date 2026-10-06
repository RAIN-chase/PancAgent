from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage
from protocol_optimization_agent.agents import orchestrator
from protocol_optimization_agent.agents.actions import get_action


def test_scientific_migration_baseline(tmp_path: Path, monkeypatch) -> None:
    case = Path(__file__).resolve().parents[2] / "evals" / "architecture_baseline"
    runner = runpy.run_path(str(case / "run_baseline.py"))
    evaluate = runner["evaluate"]
    snapshot = evaluate(tmp_path / "pa")
    assert runner["legacy_snapshot"](snapshot) == json.loads((case / "expected.json").read_text(encoding="utf-8"))
    details = snapshot["comparison"]["event_step_diff"]
    assert details["protocol_a_events"] and details["protocol_b_events"] and details["source_refs"]
    hashes = json.loads((case / "environment_before.json").read_text(encoding="utf-8"))["input_sha256"]
    assert {name: hashlib.sha256((case / name).read_bytes()).hexdigest() for name in hashes} == hashes
    matrix = {row["protocol_id"]: row for row in snapshot["protocol_feature_matrix"]["rows"]}
    feature = "all_reagent_activin_a_max_concentration"
    assert matrix["baseline_a"][feature] == "0.1" and matrix["baseline_a"][f"{feature}_unit"] == "ug/ml"
    assert matrix["baseline_b"][feature] == "0.05" and matrix["baseline_b"][f"{feature}_unit"] == "ug/ml"
    assert matrix["baseline_unknown"][feature] == ""
    metrics = [p["metrics"][0] for p in snapshot["phenotype_ir"]["phenotypes"]]
    assert [m["value"] for m in metrics] == [0, 2.5, None]
    assert all(m["unit"] == "normalized_counts" and m["evidence_refs"] for m in metrics)

    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    protocol_dir = next((tmp_path / "pa").rglob("protocol_feature_matrix.csv")).parent
    state = orchestrator.build_orchestrator_graph().invoke({
        "messages": [HumanMessage(content="Compare baseline_a and baseline_b")],
        "request": {"plan": {"question": "Compare baseline_a and baseline_b", "steps": [{
            "id": "compare", "action_id": "protocol.compare",
            "inputs": {"protocol_id_a": "baseline_a", "protocol_id_b": "baseline_b"},
            "expected_outputs": get_action("protocol.compare").outputs
        }]}},
        "protocol_output_dir": str(protocol_dir), "data_analysis_output_dir": str(tmp_path / "missing_da"),
        "optimization_output_dir": str(tmp_path / "missing_po"), "workspace_root": str(tmp_path),
        "user_id": "baseline", "run_id": "comparison", "budget": {"max_tool_calls": 1}
    })
    assert state["stop_reason"] == "completed"
    assert len(state["step_results"]) == 1 and state["step_results"][0]["usable"]
    assert [message.name for message in state["messages"] if isinstance(message, ToolMessage)] == ["protocol_analysis_agent"]
