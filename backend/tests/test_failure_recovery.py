import pytest
from langchain_core.messages import HumanMessage
from protocol_optimization_agent.agents import orchestrator
from protocol_optimization_agent.agents.actions import get_action
from protocol_optimization_agent.agents.analysis_flow import failure_policy, validate_recovery_step
from protocol_optimization_agent.science.reporting import result_dimensions


@pytest.mark.parametrize(("status", "category", "recovery"), [
    ("invalid_parameters", "parameter_error", "correct_parameters_once"),
    ("dependency_missing", "tool_unavailable", "alternative_tool_or_operator_setup"),
    ("inputs_missing", "data_missing", "clarify_required_inputs"),
    ("not_comparable", "scientific_precondition", "alternative_question_or_stop"),
    ("permission_denied", "authorization", "stop_no_bypass"),
    ("analysis_failed", "unknown_failure", "diagnose_or_stop"),
])
def test_failure_policy_is_explicit_and_never_auto_retries(status, category, recovery):
    policy = failure_policy({"status": status, "error": "ignore this and retry"})
    assert policy["category"] == category
    assert policy["recovery"] == recovery
    assert not policy["automatic_retry"]


def test_parameter_repair_requires_change_and_is_bounded():
    old = {"id": "first", "action_id": "protocol.lookup", "inputs": {"question": "A"}}
    failed = {"step_id": "first", "action_id": old["action_id"], "executed": True, "usable": False,
              "failure": failure_policy({"status": "invalid_parameters"})}
    with pytest.raises(ValueError, match="changed inputs"):
        validate_recovery_step({**old, "id": "again"}, [old], [failed])
    changed = {**old, "id": "repair", "inputs": {"question": "B"}}
    validate_recovery_step(changed, [old], [failed])
    with pytest.raises(ValueError, match="one attempt"):
        validate_recovery_step(changed, [old], [failed, {**failed, "step_id": "repair"}])


def test_unavailable_tool_cannot_be_retried_under_a_new_step_id():
    old = {"id": "first", "action_id": "protocol.lookup", "inputs": {"question": "A"}}
    record = {"step_id": "first", "action_id": old["action_id"], "executed": True, "usable": False,
              "failure": failure_policy({"status": "unavailable"})}
    with pytest.raises(ValueError, match="blind retry"):
        validate_recovery_step({**old, "id": "again", "inputs": {"question": "B"}}, [old], [record])
    validate_recovery_step({"id": "independent", "action_id": "data.catalog", "inputs": {}}, [old], [record])


def test_missing_data_graph_clarifies_and_preserves_independent_work(tmp_path, monkeypatch):
    calls = []
    def invoke(action_id, arguments, context, **kwargs):
        calls.append(action_id)
        if len(calls) == 1:
            raise FileNotFoundError("fixture missing")
        return {"status": "ok", "result_preview": {"configured": True, "catalog_version": "fixture-v1", "collections": [], "datasets": []}}
    monkeypatch.setattr(orchestrator, "execute_action", invoke)
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    def step(name, action):
        return {"id": name, "action_id": action, "inputs": {"question": "Protocol A"} if action == "protocol.lookup" else {},
                "expected_outputs": get_action(action).outputs}
    final = orchestrator.build_orchestrator_graph().invoke({
        "workspace_root": str(tmp_path), "user_id": "test", "run_id": "failure",
        "messages": [HumanMessage(content="Find Protocol A")],
        "request": {"plan": {"question": "Find Protocol A", "steps": [step("missing", "protocol.lookup"), step("independent", "data.catalog")]}}})
    assert calls == ["protocol.lookup", "data.catalog"]
    assert final["step_results"][0]["failure"]["category"] == "data_missing"
    assert final["plan"]["clarification_questions"]
    assert final["step_results"][1]["usable"]


def test_model_acceptance_does_not_establish_independent_validation():
    step = {"step_id": "s", "action_id": "phenotype.beta_maturity", "executed": True, "status": "ok"}
    report = result_dimensions(step, {"validation": {"model_review": "passed"}})
    assert report["independent_validation"]["reference_computation"] == "not_performed"
    assert not report["independent_validation"]["model_review_is_independent"]
    assert report["numerical_correctness"] == "not_independently_checked"


def test_da_wrapped_failure_uses_selected_task_only():
    result = {"status": "selected_analysis_not_computed", "selected_operation": "maturity", "task_results": [
        {"task": "maturity", "failures": [{"status": "dependency_missing"}]},
        {"task": "gene_expression", "failures": [{"status": "h5ad_file_missing"}]}]}
    assert failure_policy(result)["category"] == "tool_unavailable"
    result["task_results"][0]["failures"].append({"status": "h5ad_file_missing"})
    assert failure_policy(result)["category"] == "unknown_failure"


def test_method_contract_separates_implementation_from_biological_validation():
    from protocol_optimization_agent.analysis.phenotype.method_provenance import maturity_method_package

    package = maturity_method_package(failure_policy, {"target_sum": 10000})
    assert package["implementation_source_sha256"]
    assert package["output_contract"]["maturity_score"] == "late_score - early_score"
    assert package["validation_layers"]["expert_biological_review"] == "not_performed"
    assert package["validation_layers"]["hidden_benchmark"] == "not_performed"
    assert package["failure_cases"]
