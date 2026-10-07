from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from protocol_optimization_agent.agents.actions import get_action
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent, execute_protocol_action
from protocol_optimization_agent.analysis.protocol.assessment import detailed_comparison
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.analysis.protocol.step_semantics import align_steps, annotate_steps
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.runtime.io import read_jsonl
from protocol_optimization_agent.runtime.llm_client import StaticMockLLMClient


def record(pid="p", text="Wash cells with PBS; culture until PDX1 is detected.", **fields):
    return {"protocol_id": pid, "paper_id": "test", "comparison_ready": True,
            "protocol": {"protocol_name": pid},
            "stages": [{"stage_id": f"{pid}:s", "stage_index": 1, "key_operations": text,
                        "evidence_quote": text, **fields}]}


def test_source_conditions_states_parameter_scopes_and_unknowns_are_preserved():
    source = record(text="Wash cells; fluxify organoids when compact; do not add X.",
                    input_cell_state="spheres", output_cell_state="clusters", duration_days=None,
                    optional=True, concentration_value=5)
    original = copy.deepcopy(source)
    rows, audit = annotate_steps([source], {"p"})
    row = rows[0]
    assert "".join(action["text"] for action in row["actions"]) == row["source_text"]
    assert [action["operation"] for action in row["actions"]] == ["wash", "unknown", "add"]
    assert {guard["kind"] for guard in row["constraints"]} >= {"condition", "explicit_negative", "optional"}
    assert row["reported_states"]["before"] == "spheres"
    assert row["reported_states"]["scope"] == "stage"
    assert row["dependencies"]["source_order_only"]
    assert row["parameters"][0]["scope"] == "stage"
    assert "parameter_unit_missing" in row["validation_notes"]
    assert all("day_start" not in action for action in row["actions"])
    assert audit["n_unknown_actions"] == 1
    assert source == original


def test_unrelated_quote_is_not_claimed_as_operation_support():
    rows, audit = annotate_steps([record(evidence_quote="Day 1: RPMI with activin A.")], {"p"})
    assert audit["n_evidence_supported_actions"] == 0
    assert all(action["source_support"] == "structured_only_unverified" for action in rows[0]["actions"])
    assert all(not action["evidence_quote"] for action in rows[0]["actions"])


def test_parent_source_excerpt_can_support_operation_without_changing_unit_quote():
    source = record(evidence_quote="Day 1: RPMI.")
    source["evidence_spans"] = [{"context_text": source["stages"][0]["key_operations"]}]
    rows, audit = annotate_steps([source], {"p"})
    assert audit["n_evidence_supported_actions"] == 2
    assert rows[0]["evidence_quote"] == "Day 1: RPMI."


@pytest.mark.parametrize("proposal", [
    {"actions": [{"operation": "wash", "start": 0, "end": 999}]},
    {"actions": [{"operation": "wash", "start": 1, "end": 10}]},
    {"actions": [{"operation": "wash", "start": 0, "end": 10, "target": "INS"}]},
    {"actions": [{"operation": "wash", "start": 0, "end": 10, "dose": 100}]},
    {"actions": [{"operation": "culture", "start": 0, "end": 10}]},
    {"actions": []},
])
def test_model_omissions_fabrications_and_invalid_spans_fall_back(proposal):
    client = StaticMockLLMClient({"protocol_step_interpreter": proposal})
    rows, audit = annotate_steps([record(text="Wash cells")], {"p"}, client, 1)
    assert rows[0]["actions"][0]["operation"] == "wash"
    assert rows[0]["actions"][0]["interpretation_source"] == "rule"
    assert any(note.startswith("llm_rejected") for note in rows[0]["validation_notes"])
    assert audit["llm_calls"] == 1


def test_valid_model_parsing_is_still_an_interpretation_and_budget_is_bounded():
    client = StaticMockLLMClient({"protocol_step_interpreter": {
        "actions": [{"operation": "wash", "start": 0, "end": 10, "target": "cells"}]}})
    rows, audit = annotate_steps([record("a", "Wash cells"), record("b", "Wash cells")], {"a", "b"}, client, 1)
    assert len(client.calls) == audit["llm_calls"] == 1
    assert rows[0]["actions"][0]["claim_type"] == "model_interpretation"
    assert rows[0]["actions"][0]["target"] == "cells"
    assert rows[1]["actions"][0]["interpretation_source"] == "rule"


def test_nested_steps_and_preparation_have_stable_source_paths():
    source = record(steps=[{"text": "Wash cells", "duration_days": 1}, "Fluxify organoids"])
    source["preparation"] = [{"operation": "Seed cells", "evidence_quote": "Seed cells"}]
    rows, _ = annotate_steps([source], {"p"})
    assert [row["source_ref"]["path"] for row in rows] == [
        "/preparation/0/operation", "/stages/0/steps/0/text", "/stages/0/steps/1", "/stages/0/key_operations"]
    assert rows[1]["parameters"][0]["scope"] == "source_unit"
    assert rows[2]["actions"][0]["operation"] == "unknown"
    assert len({row["step_id"] for row in rows}) == len(rows)


def test_alignment_retains_guard_dose_context_and_order_changes():
    left, _ = annotate_steps([record("a", "Wash cells; culture until PDX1 is detected.", duration_days=3)], {"a"})
    right, _ = annotate_steps([record("b", "Culture for 2 days; rinse cells.", duration_days=2)], {"b"})
    diff = align_steps(left, right)
    assert len(diff["aligned_actions"]) == 2
    assert [pair["operation"] for pair in diff["aligned_actions"]] == ["wash", "culture"]
    assert len(diff["order_inversions"]) == 1
    assert all(pair["status"] == "candidate_alignment_not_equivalence" for pair in diff["aligned_actions"])
    assert all(not pair["target_known"] for pair in diff["aligned_actions"])
    assert "parameters" in diff["aligned_actions"][0]["differences"]
    assert "constraints" in diff["aligned_actions"][0]["differences"]


def test_repeated_actions_are_not_forced_into_matches_or_called_absent():
    left, _ = annotate_steps([record("a", "Wash cells; wash cells; fluxify organoids.")], {"a"})
    right, _ = annotate_steps([record("b", "Wash cells; seed cells.")], {"b"})
    diff = align_steps(left, right)
    assert not diff["aligned_actions"]
    assert {item["reason"] for item in diff["unresolved"]} == {
        "ambiguous_repetition", "unparsed", "no_match_not_proof_of_absence"}


def test_scoped_model_parameters_are_copied_not_generated_or_compiled():
    text = "Add X at 5 ng/ml"
    client = StaticMockLLMClient({"protocol_step_interpreter": {"actions": [{
        "operation": "add", "start": 0, "end": len(text), "target": "X",
        "parameters": [{"start": 9, "end": len(text)}]}]}})
    rows, _ = annotate_steps([record(text=text)], {"p"}, client, 1)
    parameter = rows[0]["actions"][0]["parameters"][0]
    assert parameter["text"] == "5 ng/ml" and parameter["scope"] == "action"
    assert parameter["claim_type"] == "model_interpretation"
    assert not rows[0]["parameters"]
    assert "concentration_value" not in rows[0]["actions"][0]


def test_model_cannot_merge_two_reported_operations_or_move_parameters():
    for text, action in [
        ("Wash cells; wash cells", {"operation": "wash", "start": 0, "end": 22}),
        ("Add X at 5 ng/ml", {"operation": "add", "start": 0, "end": 16,
                             "parameters": [{"start": 9, "end": 99}]}),
    ]:
        action["end"] = len(text)
        client = StaticMockLLMClient({"protocol_step_interpreter": {"actions": [action]}})
        rows, _ = annotate_steps([record(text=text)], {"p"}, client, 1)
        assert rows[0]["actions"][0]["interpretation_source"] == "rule"


def test_conflicting_time_optional_and_unknown_unit_are_audited():
    rows, _ = annotate_steps([record(text="Optionally wash cells.", optional=False,
        day_start=5, day_end=3, concentration_value=1, unit="bananas")], {"p"})
    assert set(rows[0]["validation_notes"]) >= {
        "contradictory_time_bounds", "optional_field_text_conflict", "parameter_unit_unsupported"}


def test_old_outputs_do_not_trigger_llm_parsing(tmp_path):
    engine = ProtocolQueryEngine(tmp_path)
    assert engine.get_protocol_steps("p")["status"] == "unavailable"
    assert detailed_comparison(engine, "a", "b")["semantic_alignment"]["status"] == "semantic_artifact_missing"


def test_pa_exports_and_registered_step_query_reuse_persisted_results(tmp_path):
    outputs = ProtocolAnalysisAgent(tmp_path / "pa", {}).run([record()], [])
    annotations = read_jsonl(outputs["semantic_step_annotations"])
    assert annotations and outputs["step_semantic_audit"].exists()
    query = execute_protocol_action(get_action("protocol.steps"), {"protocol_id": "p"},
        RunContext.from_values(user_id="test", run_id="step-query", workspace_root=str(tmp_path)),
        str(outputs["semantic_step_annotations"].parent), 40)
    assert query["status"] == "ok"
    assert query["result_preview"]["semantic_steps"] == annotations
    assert query["evidence_refs"] and query["output_files"]


def test_curated_language_cases():
    cases = json.loads((Path(__file__).resolve().parents[2] / "evals/protocol_language/cases.json").read_text())
    for case in cases:
        rows, audit = annotate_steps([record(text=case["text"])], {"p"})
        assert [action["operation"] for action in rows[0]["actions"]] == case["operations"], case["id"]
        assert {guard["kind"] for guard in rows[0]["constraints"]} == set(case["guards"]), case["id"]
        assert "".join(action["text"] for action in rows[0]["actions"]) == case["text"]
        assert audit["n_evidence_supported_actions"] == audit["n_actions"]
