"""DA action adaptation; scientific execution stays in data_analysis and analysis/phenotype."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from protocol_optimization_agent.agents.actions import select_action
from protocol_optimization_agent.agents.data_analysis import TASK_OUTPUTS, DataAnalysisAgent
from protocol_optimization_agent.analysis.phenotype.endpoint_comparison import compare_endpoint
from protocol_optimization_agent.analysis.phenotype.h5ad_tools import (
    DATA_ANALYSIS_TOOL_REGISTRY,
)
from protocol_optimization_agent.analysis.phenotype.maturity_scoring import (
    CAVEAT as MATURITY_CAVEAT,
)
from protocol_optimization_agent.runtime.access_control import forbid_client_paths, resolve_manifest_artifacts
from protocol_optimization_agent.runtime.archive import (
    audited_tool,
    current_archive,
)
from protocol_optimization_agent.runtime.context import (
    RunContext,
    evidence_response_envelope,
    record_agent_call,
    resolve_domain_output_dir,
)
from protocol_optimization_agent.runtime.io import (
    read_csv_dicts,
    read_json,
    read_jsonl,
)
from protocol_optimization_agent.science.ingestion import ingest_phenotype_outputs
from protocol_optimization_agent.science.state import ScientificWorldModel


@tool
@audited_tool
def data_analysis_agent(
    question: str,
    data_analysis_output_dir: Annotated[str, InjectedState("data_analysis_output_dir")],
    request: dict[str, Any] | None = None,
    h5ad_manifest_rows: list[dict[str, Any]] | None = None,
    h5ad_config: dict[str, Any] | None = None,
    force_rerun: bool = False,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
    project_id: Annotated[str, InjectedState("project_id")] = "default",
) -> dict[str, Any]:
    """Delegate h5ad, marker, composition, maturity, reference similarity, trajectory, or QC questions to Data Analysis Agent."""

    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root, project_id=project_id)
    forbid_client_paths(request or {})
    forbid_client_paths(h5ad_config or {})
    h5ad_manifest_rows = resolve_manifest_artifacts(h5ad_manifest_rows or [], context.user_id, project_id)
    if not h5ad_manifest_rows and (request or {}).get("dataset_id"):
        import os
        if os.getenv("PROTOCOL_DATA_CATALOG"):
            from protocol_optimization_agent.runtime.data_catalog import catalog_manifest

            h5ad_manifest_rows = catalog_manifest(str(request["dataset_id"]), context.user_id, project_id,
                                                  str(request.get("dataset_version") or ""))
    output_dir = Path(resolve_domain_output_dir("data_analysis", data_analysis_output_dir, context))
    structured_request = _build_data_analysis_request(question, request or {})
    selected_operation = str(structured_request["operation"])
    effective_h5ad_config = _merge_h5ad_query_config(h5ad_config or {}, structured_request)
    archive = current_archive()
    if archive:
        archive.record("effective_config", {"request": structured_request, "h5ad": effective_h5ad_config,
                       "manifest_rows": h5ad_manifest_rows})
        archive.inputs({f"h5ad_{index}": str(row["h5ad_path"]) for index, row in enumerate(h5ad_manifest_rows)
                        if row.get("h5ad_path")}, {"manifest_rows": h5ad_manifest_rows})
    ran_now = bool(force_rerun or (h5ad_manifest_rows and _data_query_requires_execution(selected_operation)))
    if ran_now:
        DataAnalysisAgent(output_dir=output_dir, h5ad_config=effective_h5ad_config).run(h5ad_manifest_rows or [])
    comparison_report = read_json(output_dir / "data_analysis_comparison_report.json")
    limitations = read_json(output_dir / "h5ad_analysis_limitations.json")
    validation = read_json(output_dir / "data_analysis_validation.json")
    tool_registry = read_json(output_dir / "data_analysis_tool_registry.json")
    summaries = read_jsonl(output_dir / "h5ad_summary_by_protocol.jsonl")
    protocol_summary_rows = read_csv_dicts(output_dir / "data_analysis_protocol_summary.csv")
    existing_outputs = (
        sorted(path.name for path in output_dir.glob("*") if path.is_file()) if output_dir.exists() else []
    )
    selected_output = _data_operation_output_file(selected_operation)
    cached_rows = read_csv_dicts(output_dir / selected_output)
    result_preview = _filter_data_result_rows(cached_rows, structured_request)[:50]
    status = _data_tool_status(existing_outputs, selected_output, cached_rows, result_preview, structured_request)
    task_results = read_json(output_dir / "data_analysis_task_results.json").get("tasks", [])
    if ran_now and status == "ok_cached_result":
        status = "ok"
    if ran_now:
        selected_result: dict[str, Any] = next((item for item in task_results if item["task"] == selected_operation), {})
        if selected_result.get("status") in {"unavailable", "inputs_missing"}:
            status = selected_result["status"]
            result_preview = []
    world_update = ingest_phenotype_outputs(ScientificWorldModel(context, project_id), output_dir)
    output_files = {
        "selected": str(output_dir / selected_output),
        "summary": str(output_dir / "data_analysis_comparison_report.json"),
        "limitations": str(output_dir / "h5ad_analysis_limitations.json"),
        "execution_plan": str(output_dir / "data_analysis_execution_plan.json"),
        "task_results": str(output_dir / "data_analysis_task_results.json"),
    }
    maturity_audit = read_json(output_dir / "maturity_audit.json") if selected_operation == "maturity" else {}
    maturity_evidence = [ref for row in result_preview for ref in [{
        "path": str(output_dir / selected_output), "column": "maturity_score",
        "dataset_id": row.get("dataset_id"), "cell_type": row.get("cell_type"),
        "method_version": row.get("method_version"),
    }]] if selected_operation == "maturity" else []
    if selected_operation == "maturity":
        output_files["audit"] = str(output_dir / "maturity_audit.json")
        for analysis in maturity_audit.get("analyses", []):
            maturity_evidence.extend({"evidence_type": "gene_program", "path": definition["path"],
                                      "sha256": definition["sha256"]}
                                     for programs in analysis.get("programs", {}).values() for definition in programs.values())
    record_agent_call(
        context,
        subagent="data_analysis_agent",
        operation=selected_operation,
        status=status,
        question=question,
        output_files=output_files,
        output_dirs={"data_analysis": str(output_dir)},
        inputs={"data_analysis_output_dir": str(output_dir)},
        extra={
            "entrypoint": "orchestrator",
            "last_subagent": "data_analysis_agent",
            "request": structured_request,
            "force_rerun": force_rerun,
        },
    )
    return evidence_response_envelope(
        context=context,
        subagent="data_analysis_agent",
        status=status,
        question=question,
        selected_operation=selected_operation,
        used_tools=([item["tool"] for item in task_results if item.get("executed") and item.get("tool")]
                    if ran_now and selected_operation != "summary" else ["inspect_h5ad_dataset"] if ran_now
                    else [_data_operation_tool_name(selected_operation)]),
        used_features=_data_used_features(selected_operation, result_preview),
        evidence_refs=[*_data_evidence_refs(summaries[:50]), *maturity_evidence],
        confidence_caveats=_data_confidence_caveats(
            status, limitations.get("limitations", []), validation, selected_operation
        ),
        limitations=limitations.get("limitations", [])[:50],
        provenance=_data_analysis_provenance(summaries[:50]),
        result_preview=result_preview,
        output_files=output_files,
        extra={
            "structured_request": structured_request,
            "execution_mode": "executed" if ran_now else "cached_result_read" if result_preview else "unavailable",
            "output_dir": str(output_dir),
            "summary": comparison_report.get("summary", {}),
            "validation": validation,
            "execution_plan": read_json(output_dir / "data_analysis_execution_plan.json"),
            "task_results": task_results,
            "statistical_applicability": validation.get("statistical_applicability", {}),
            "protocol_summaries": protocol_summary_rows[:50],
            "dataset_summaries": summaries[:50],
            "tool_registry": tool_registry.get("tools", DATA_ANALYSIS_TOOL_REGISTRY),
            "external_tool_inventory": tool_registry.get("external_tool_inventory", []),
            "callable_tools": tool_registry.get("callable_tools", []),
            "available_output_files": existing_outputs,
            "scientific_state": world_update,
            "phenotype_ir": read_json(output_dir / "phenotype_ir.json"),
            "phenotype_interpretation": read_json(output_dir / "phenotype_interpretation.json"),
        },
    )


def _build_data_analysis_request(question: str, request: dict[str, Any]) -> dict[str, Any]:
    operation = str(request.get("operation") or select_action(question).operation or "summary")
    genes = _string_list(request.get("genes") or request.get("target_genes") or _extract_gene_symbols(question))
    return {
        "operation": operation,
        "genes": genes,
        "protocol_id": str(request.get("protocol_id") or ""),
        "dataset_id": str(request.get("dataset_id") or ""),
        "dataset_version": str(request.get("dataset_version") or ""),
        "sample_id": str(request.get("sample_id") or ""),
        "condition_id": str(request.get("condition_id") or ""),
        "groupby": str(request.get("groupby") or request.get("obs_column") or ""),
        "group_a": str(request.get("group_a") or request.get("case_group") or ""),
        "group_b": str(request.get("group_b") or request.get("control_group") or ""),
        "marker_set": str(request.get("marker_set") or ""),
        "cell_type": str(request.get("cell_type") or next((label for label in ("Alpha", "Beta", "Delta")
                          if re.search(r"\b" + label + r"\b", question, re.IGNORECASE)), "")),
    }


def _merge_h5ad_query_config(config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    merged["requested_tasks"] = [request["operation"]]
    if request["operation"] == "maturity":
        merged["maturity"] = {**merged.get("maturity", {})}
        if request["cell_type"]:
            merged["maturity"]["cell_types"] = [request["cell_type"]]
    if request["genes"] and request["operation"] in {"gene_expression", "differential_expression"}:
        merged["query_genes"] = request["genes"]
    if request["groupby"]:
        merged["expression_groupby"] = request["groupby"]
        merged["groupby"] = request["groupby"]
    if request["operation"] == "differential_expression":
        de = dict(merged.get("differential_expression", {}))
        merged["differential_expression"] = {
            **de,
            "groupby": request["groupby"] or de.get("groupby", ""),
            "group_a": request["group_a"] or de.get("group_a", ""),
            "group_b": request["group_b"] or de.get("group_b", ""),
            "genes": request["genes"] or de.get("genes", []) or merged.get("query_genes", []),
        }
    return merged


def _data_query_requires_execution(operation: str) -> bool:
    return operation == "summary" or operation in TASK_OUTPUTS


def _filter_data_result_rows(rows: list[dict[str, str]], request: dict[str, Any]) -> list[dict[str, str]]:
    filtered = rows
    for key in ("protocol_id", "dataset_id", "sample_id", "condition_id"):
        value = request.get(key)
        if value:
            filtered = [row for row in filtered if str(row.get(key) or "") == str(value)]
    genes = {str(gene).upper() for gene in request.get("genes", [])}
    if genes and any("gene" in row for row in filtered):
        filtered = [row for row in filtered if str(row.get("gene") or "").upper() in genes]
    for request_key, row_key in (("groupby", "groupby"), ("group_a", "group_a"), ("group_b", "group_b")):
        value = request.get(request_key)
        if value and any(row.get(row_key) for row in filtered):
            filtered = [row for row in filtered if str(row.get(row_key) or "") == str(value)]
    if request.get("marker_set") and any("marker_set" in row for row in filtered):
        filtered = [row for row in filtered if str(row.get("marker_set") or "") == str(request["marker_set"])]
    if request.get("cell_type") and any("cell_type" in row for row in filtered):
        filtered = [row for row in filtered if str(row.get("cell_type") or "").lower() == str(request["cell_type"]).lower()]
    return filtered


def _data_tool_status(
    existing_outputs: list[str],
    selected_output: str,
    cached_rows: list[dict[str, str]],
    result_preview: list[dict[str, str]],
    request: dict[str, Any],
) -> str:
    if not existing_outputs:
        return "no_prior_data_analysis_outputs"
    if selected_output not in existing_outputs:
        return "selected_analysis_output_missing"
    if cached_rows and not result_preview and _request_has_filters(request):
        return "no_matching_cached_result"
    if not cached_rows and _data_query_requires_execution(str(request.get("operation") or "")):
        return "selected_analysis_not_computed"
    if result_preview and any(row.get("status") and row["status"] != "ok" for row in result_preview):
        return "partial" if any(row.get("status") == "ok" for row in result_preview) else "selected_analysis_not_computed"
    return "ok_cached_result"


def _request_has_filters(request: dict[str, Any]) -> bool:
    return any(
        bool(request.get(key))
        for key in (
            "genes",
            "protocol_id",
            "dataset_id",
            "sample_id",
            "condition_id",
            "groupby",
            "group_a",
            "group_b",
            "marker_set",
            "cell_type",
        )
    )


def _extract_gene_symbols(question: str) -> list[str]:
    candidates = re.findall(r"\b[A-Z][A-Z0-9-]{1,12}\b", question)
    stopwords = {"DE", "RNA", "H5AD", "QC", "UMAP", "PCA"}
    return [candidate for candidate in candidates if candidate not in stopwords][:50]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;|\s]+", value) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _data_operation_output_file(operation: str) -> str:
    if operation in TASK_OUTPUTS:
        return f"{TASK_OUTPUTS[operation][1]}.csv"
    return "data_analysis_protocol_summary.csv" if operation == "summary" else "unavailable_analysis.csv"


def _data_analysis_provenance(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "protocol_id": row.get("protocol_id", ""),
            "dataset_id": row.get("dataset_id", ""),
            "sample_id": row.get("sample_id", ""),
            "condition_id": row.get("condition_id", ""),
            "h5ad_path": row.get("h5ad_path", ""),
            "cell_type_column": row.get("cell_type_column", ""),
            "batch_column": row.get("batch_column", ""),
            "lineage_column": row.get("lineage_column", ""),
        }
        for row in summaries
    ]


def _data_operation_tool_name(operation: str) -> str:
    return TASK_OUTPUTS[operation][3] if operation in TASK_OUTPUTS else operation


def _data_used_features(operation: str, rows: list[dict[str, str]]) -> list[str]:
    if operation in {"gene_expression", "differential_expression"}:
        return sorted({row.get("gene", "") for row in rows if row.get("gene")})[:50]
    if operation == "marker_score":
        return sorted({row.get("marker_set", "") for row in rows if row.get("marker_set")})[:50]
    if operation == "gene_program_score":
        return sorted({row.get("gene_program", "") for row in rows if row.get("gene_program")})[:50]
    if operation == "cell_type_composition":
        return sorted({row.get("cell_type", "") for row in rows if row.get("cell_type")})[:50]
    if operation == "maturity":
        return sorted({row.get("lineage", "") for row in rows if row.get("lineage")})[:50]
    return []


def _data_evidence_refs(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_type": "h5ad_dataset_summary",
            "study_id": study_id,
            "protocol_id": row.get("protocol_id", ""),
            "dataset_id": row.get("dataset_id", ""),
            "sample_id": row.get("sample_id", ""),
            "condition_id": row.get("condition_id", ""),
            "h5ad_path": row.get("h5ad_path", ""),
        }
        for row in summaries
        for study_id in ([row["study_id"]] if row.get("study_id") else
                         row.get("design_metadata", {}).get("fields", {}).get("study", {}).get("values", []) or [""])
    ]


def _data_confidence_caveats(
    status: str, limitations: list[dict[str, Any]], validation: dict[str, Any] | None = None, operation: str = "summary"
) -> list[str]:
    caveats: list[str] = []
    if operation == "maturity":
        caveats.append(MATURITY_CAVEAT)
    if status != "ok":
        caveats.append(f"Data Analysis Agent returned status={status}.")
    if limitations:
        caveats.append("Data Analysis Agent reported limitations; inspect h5ad_analysis_limitations.json.")
    if validation and validation.get("task_validity", {}).get(operation, {}).get("valid") is not True:
        caveats.append("The selected task has no valid numeric result; unrelated metrics do not determine its validity.")
    if validation:
        caveats.append("Any LLM-authored quality score is qualitative commentary, not a statistical confidence probability.")
    return caveats



def execute_data_action(action, arguments: dict[str, Any], context: RunContext,
                        output_dir: str) -> dict[str, Any]:
    if action.id == "data.exploratory_sensitivity":
        from protocol_optimization_agent.agents.data_exploration import execute_sensitivity
        return execute_sensitivity(arguments, context)
    if action.id == "protocol.phenotype_compare":
        path = Path(resolve_domain_output_dir("data_analysis", output_dir, context)) / "phenotype_ir.json"
        result = compare_endpoint(read_json(path), arguments["protocol_ids"], arguments["endpoint"])
        evidence = [ref for row in result.get("observations", []) for ref in row["metric"].get("evidence_refs", [])]
        return evidence_response_envelope(context=context, subagent=action.agent, status=result["status"],
            question=str(arguments.get("question", "")), selected_operation=action.id, result_preview=result,
            used_tools=[action.implementation], evidence_refs=evidence,
            validation={"evidence_gaps": result.get("evidence_gaps", [])},
            confidence_caveats=["Descriptive assessment or unvalidated hypotheses, not causal effects."],
            extra={"action_id": action.id})
    return data_analysis_agent.invoke({**arguments, "question": arguments.get("question", action.id),
        "request": {**arguments.get("request", {}), "operation": action.operation},
        "force_rerun": bool(action.id == "data.assess" and arguments.get("h5ad_manifest_rows")),
        "data_analysis_output_dir": output_dir, **context.tool_inputs()})
