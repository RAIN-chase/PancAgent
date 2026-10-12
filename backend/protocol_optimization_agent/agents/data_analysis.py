from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from protocol_optimization_agent.agents.actions import action_registry, get_action, select_action
from protocol_optimization_agent.analysis.phenotype.endpoint_comparison import compare_endpoint
from protocol_optimization_agent.analysis.phenotype.external_tools import (
    get_external_data_analysis_tools,
)
from protocol_optimization_agent.analysis.phenotype.h5ad_tools import (
    DATA_ANALYSIS_TOOL_REGISTRY,
    compute_cell_type_composition,
    compute_differential_expression,
    compute_marker_scores,
    inspect_h5ad_dataset,
    query_gene_expression,
    validate_data_analysis_inputs,
)
from protocol_optimization_agent.analysis.phenotype.maturity_scoring import (
    CAVEAT as MATURITY_CAVEAT,
)
from protocol_optimization_agent.analysis.phenotype.maturity_scoring import (
    compute_maturity_scores,
    load_maturity_programs,
)
from protocol_optimization_agent.analysis.phenotype.representation import (
    CONTEXT_FIELDS,
    build_phenotype_ir,
    interpret_phenotypes,
    make_phenotype_interpreter,
)
from protocol_optimization_agent.runtime.access_control import forbid_client_paths, resolve_manifest_artifacts
from protocol_optimization_agent.runtime.archive import (
    audited_agent_run,
    audited_operation,
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
    write_csv_dicts,
    write_json,
    write_jsonl,
)
from protocol_optimization_agent.runtime.llm_client import LLMClient, build_llm_client
from protocol_optimization_agent.science.ingestion import ingest_phenotype_outputs
from protocol_optimization_agent.science.state import ScientificWorldModel

# Output contracts, not a second capability registry: availability comes from actions.py.
TASK_OUTPUTS = {
    "cell_type_composition": ("composition_rows", "cell_type_composition", ("n_cells",), "cell_type_composition"),
    "gene_expression": ("gene_expression_rows", "gene_expression_summary", ("mean_expression",), "query_gene_expression"),
    "differential_expression": ("differential_expression_rows", "differential_expression", ("mean_diff",), "differential_expression"),
    "marker_score": ("marker_rows", "marker_scores", ("score",), "marker_score_analysis"),
    "gene_program_score": ("gene_program_rows", "gene_program_scores", ("program_score",), "gene_program_score"),
    "maturity": ("maturity_rows", "maturity_scores", ("maturity_score",), "gene_program_maturity_score"),
}


class DataAnalysisState(TypedDict, total=False):
    analysis_mode: str
    execution_plan: dict[str, Any]
    task_results: list[dict[str, Any]]
    exploratory_artifacts: dict[str, Any]
    validation_report: dict[str, Any]
    phenotype_ir: dict[str, Any]
    interpretation_report: dict[str, Any]
    manifest_rows: list[dict[str, Any]]
    reference_atlas_path: str
    gene_programs: dict[str, Any]
    h5ad_config: dict[str, Any]
    normalized_manifest_rows: list[dict[str, Any]]
    input_gate_report: dict[str, Any]
    dataset_summaries: list[dict[str, Any]]
    qc_rows: list[dict[str, Any]]
    composition_rows: list[dict[str, Any]]
    marker_rows: list[dict[str, Any]]
    gene_program_rows: list[dict[str, Any]]
    gene_expression_rows: list[dict[str, Any]]
    differential_expression_rows: list[dict[str, Any]]
    maturity_rows: list[dict[str, Any]]
    maturity_audits: list[dict[str, Any]]
    maturity_cell_files: dict[str, Path]
    similarity_rows: list[dict[str, Any]]
    regulatory_rows: list[dict[str, Any]]
    trajectory_rows: list[dict[str, Any]]
    limitations: list[dict[str, Any]]
    outputs: dict[str, Path]
    step_log: list[dict[str, Any]]


class DataAnalysisAgent:
    """Execute explicitly requested analyses within fixed validation/archive safeguards."""

    def __init__(
        self,
        output_dir: Path,
        h5ad_config: dict[str, Any] | None = None,
        reference_atlas_path: Path | str | None = None,
        gene_programs: dict[str, Any] | None = None,
        project_root: Path | str | None = None,
        interpreter: Callable[[dict[str, Any], int], list[dict[str, Any]]] | None = None,
        interpretation_llm_client: LLMClient | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.h5ad_config = h5ad_config or {}
        self.reference_atlas_path = Path(reference_atlas_path) if reference_atlas_path else None
        self.gene_programs = gene_programs or self.h5ad_config.get("gene_programs", {}) or {}
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.callable_tools = get_external_data_analysis_tools(self.project_root)
        self.interpreter = interpreter
        interpretation = self.h5ad_config.get("interpretation", {})
        if self.interpreter is None and interpretation.get("enabled"):
            client = interpretation_llm_client or build_llm_client(interpretation.get("llm"))
            self.interpreter = make_phenotype_interpreter(client, interpretation.get("claims", []),
                                                        int(interpretation.get("max_metrics", 200)))

    @audited_agent_run
    def run(
        self,
        manifest_rows: list[dict[str, Any]],
        reference_atlas_path: Path | str | None = None,
        gene_programs: dict[str, Any] | None = None,
    ) -> dict[str, Path]:
        effective_reference = Path(reference_atlas_path) if reference_atlas_path else self.reference_atlas_path
        effective_gene_programs = gene_programs if gene_programs is not None else self.gene_programs
        initial_state: DataAnalysisState = {
            "analysis_mode": str(self.h5ad_config.get("analysis_mode") or "standard"),
            "manifest_rows": manifest_rows,
            "reference_atlas_path": str(effective_reference) if effective_reference else "",
            "gene_programs": effective_gene_programs or {},
            "h5ad_config": {**self.h5ad_config, "gene_programs": effective_gene_programs or {}},
            "step_log": [],
        }
        final_state = self.build_langgraph().invoke(initial_state)
        return final_state["outputs"]

    @audited_operation
    def call_tool(self, tool_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call one registered external data-analysis tool wrapper by id."""
        if tool_id not in self.callable_tools:
            return {
                "tool_id": tool_id,
                "status": "unknown_tool",
                "available_tools": sorted(self.callable_tools),
            }
        return self.callable_tools[tool_id](
            {
                "project_root": str(self.project_root),
                **(config or {}),
            }
        )

    def build_langgraph(self, compile_graph: bool = True) -> Any:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise RuntimeError("LangGraph is required to run DataAnalysisAgent.") from exc

        graph = StateGraph(DataAnalysisState)
        graph.add_node("data_input_gate", self._node_data_input_gate)
        graph.add_node("resolve_tasks", self._node_resolve_tasks)
        graph.add_node("dataset_inventory_builder", self._node_dataset_inventory_builder)
        graph.add_node("execute_tasks", self._node_execute_tasks)
        graph.add_node("data_analysis_audit", self._node_data_analysis_audit)
        graph.add_node("phenotype_representation", self._node_phenotype_representation)
        graph.add_node("export_results", self._node_export_results)

        graph.add_edge(START, "data_input_gate")
        graph.add_edge("data_input_gate", "resolve_tasks")
        graph.add_edge("resolve_tasks", "dataset_inventory_builder")
        graph.add_edge("dataset_inventory_builder", "execute_tasks")
        graph.add_edge("execute_tasks", "data_analysis_audit")
        graph.add_edge("data_analysis_audit", "phenotype_representation")
        graph.add_edge("phenotype_representation", "export_results")
        graph.add_edge("export_results", END)
        return graph.compile() if compile_graph else graph

    def _node_data_input_gate(self, state: DataAnalysisState) -> DataAnalysisState:
        report = validate_data_analysis_inputs(
            state.get("manifest_rows", []),
            state.get("reference_atlas_path", "") or None,
            state.get("gene_programs", {}),
            state.get("h5ad_config", {}),
        )
        return {
            **state,
            "input_gate_report": {key: value for key, value in report.items() if key != "normalized_manifest_rows"},
            "normalized_manifest_rows": report["normalized_manifest_rows"],
            "step_log": _append_step(state, "data_input_gate"),
        }

    @audited_operation
    def _node_resolve_tasks(self, state: DataAnalysisState) -> DataAnalysisState:
        requested_mode = str(state.get("analysis_mode") or "standard").lower()
        mode = requested_mode if requested_mode in {"standard", "exploratory"} else "standard"
        exploratory = state.get("h5ad_config", {}).get("exploratory_analysis", {})
        plan = resolve_data_tasks(state.get("h5ad_config", {}))
        plan.update(requested_mode=requested_mode, analysis_mode=mode,
                    exploratory_code_execution="external_sandbox_required")
        artifacts = {
            "script": str(exploratory.get("script") or "# No exploratory analysis requested.\n"),
            "environment": exploratory.get("environment", {}),
            "config": exploratory.get("config", {}),
            "diagnostics": exploratory.get("diagnostics", {}),
            "validation_checks": exploratory.get("validation_checks", []),
        }
        return {
            **state,
            "analysis_mode": mode,
            "execution_plan": plan,
            "exploratory_artifacts": artifacts,
            "step_log": _append_step(state, "resolve_tasks"),
        }

    @audited_operation
    def _node_dataset_inventory_builder(self, state: DataAnalysisState) -> DataAnalysisState:
        summaries: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = []
        for row in state.get("normalized_manifest_rows", []):
            result = inspect_h5ad_dataset(row, state.get("h5ad_config", {}))
            summaries.append(_dataset_summary_from_inspection(row, result))
            original = next((r for r in state.get("manifest_rows", []) if all(
                str(r.get(field) or "") == str(row.get(field) or "")
                for field in ("protocol_id", "dataset_id", "sample_id", "condition_id")
            )), {})
            summaries[-1].update({field: original[field] for field in CONTEXT_FIELDS if original.get(field) not in (None, "")})
            limitations.extend(result.get("limitations", []))
        return {
            **state,
            "dataset_summaries": summaries,
            "qc_rows": [_build_qc_row(row) for row in summaries],
            "limitations": limitations,
            "step_log": _append_step(state, "dataset_inventory_builder"),
        }

    @audited_operation
    def _node_execute_tasks(self, state: DataAnalysisState) -> dict[str, Any]:
        handlers = {
            "cell_type_composition": self._execute_composition,
            "gene_expression": self._execute_expression,
            "differential_expression": self._execute_de,
            "marker_score": self._execute_markers,
            "gene_program_score": self._execute_gene_programs,
            "maturity": self._execute_maturity,
        }
        updated: dict[str, Any] = {**state, **{spec[0]: [] for spec in TASK_OUTPUTS.values()}}
        results = []
        for step in state["execution_plan"]["steps"]:
            task = step["task"]
            error = step.get("error", "")
            if task == "summary" and not error:
                results.append({"task": task, "action_id": step["action_id"], "status": "inspected", "executed": False})
                continue
            if task not in handlers or error:
                result = {"task": task, "action_id": step["action_id"], "status": step.get("status", "unavailable"),
                          "executed": False, "message": error or "No implemented handler"}
                if task in TASK_OUTPUTS:
                    updated[TASK_OUTPUTS[task][0]] = [_task_failure(row, result["status"], result["message"])
                                                    for row in state.get("normalized_manifest_rows", [])]
                results.append(result)
                continue
            value = handlers[task](state, step["parameters"])
            updated.update(value)
            rows = value[TASK_OUTPUTS[task][0]]
            failures = [row for row in rows if row.get("status") != "ok"]
            executed = any(row.get("status") not in {"dependency_failed", "program_input_invalid"} for row in rows)
            results.append({"task": task, "action_id": step["action_id"], "tool": TASK_OUTPUTS[task][3],
                            "executed": executed, "status": "ok" if rows and not failures else "partial" if len(failures) < len(rows) else "failed",
                            "row_count": len(rows), "failures": failures,
                            "statistical_caveats": [row.get("statistical_caveats", []) for row in rows if row.get("statistical_caveats")]})
        updated.update(task_results=results, step_log=_append_step(state, "execute_tasks"))
        return updated

    def _execute_rows(self, state: DataAnalysisState, task: str, operation: Callable) -> list[dict[str, Any]]:
        results = []
        gate_failed = any(item.get("severity") == "high" for item in state.get("input_gate_report", {}).get("findings", []))
        for row, summary in zip(state.get("normalized_manifest_rows", []), state.get("dataset_summaries", []), strict=True):
            if gate_failed or summary.get("cell_filter_status") != "ok":
                results.append(_task_failure(row, "dependency_failed", summary.get("analysis_status") or "manifest_gate_failed"))
                continue
            try:
                results.extend(operation(row))
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                results.append(_task_failure(row, "analysis_failed", str(exc)))
                archive = current_archive()
                if archive:
                    archive.record("error", {"task": task, "dataset_id": row.get("dataset_id"),
                                              "type": type(exc).__name__, "message": str(exc)})
        return results

    @audited_operation
    def _execute_composition(self, state: DataAnalysisState, parameters: dict[str, Any]) -> dict[str, Any]:
        return {"composition_rows": self._execute_rows(state, "cell_type_composition",
                lambda row: compute_cell_type_composition(row, parameters))}

    @audited_operation
    def _execute_expression(self, state: DataAnalysisState, parameters: dict[str, Any]) -> dict[str, Any]:
        return {"gene_expression_rows": self._execute_rows(state, "gene_expression",
                lambda row: query_gene_expression(row, parameters, parameters["query_genes"], groupby=_expression_groupby(parameters)))}

    @audited_operation
    def _execute_de(self, state: DataAnalysisState, parameters: dict[str, Any]) -> dict[str, Any]:
        de = parameters["differential_expression"]
        return {"differential_expression_rows": self._execute_rows(state, "differential_expression",
                lambda row: compute_differential_expression(row, parameters, groupby=de["groupby"],
                    group_a=de["group_a"], group_b=de["group_b"], genes=de.get("genes") or parameters.get("query_genes") or None))}

    @audited_operation
    def _execute_markers(self, state: DataAnalysisState, parameters: dict[str, Any]) -> dict[str, Any]:
        return {"marker_rows": self._execute_rows(state, "marker_score",
                lambda row: compute_marker_scores(row, parameters, parameters["marker_sets"], groupby=_expression_groupby(parameters)))}

    @audited_operation
    def _execute_gene_programs(self, state: DataAnalysisState, parameters: dict[str, Any]) -> dict[str, Any]:
        return {"gene_program_rows": self._execute_rows(state, "gene_program_score",
                lambda row: _compute_gene_program_scores(row, parameters, parameters["gene_programs"], _expression_groupby(parameters)))}

    @audited_operation
    def _execute_maturity(self, state: DataAnalysisState, parameters: dict[str, Any]) -> dict[str, Any]:
        config = parameters.get("maturity", {})
        audits, files = [], {}
        if any(item.get("severity") == "high" for item in state.get("input_gate_report", {}).get("findings", [])) or not any(
            summary.get("cell_filter_status") == "ok" for summary in state.get("dataset_summaries", [])
        ):
            return {"maturity_rows": [_task_failure(row, "dependency_failed", "No valid input dataset")
                                      for row in state.get("normalized_manifest_rows", [])]}
        try:
            programs = load_maturity_programs(config.get("program_dir"), config.get("cell_types"))
        except (OSError, ValueError, TypeError) as exc:
            return {"maturity_rows": [_task_failure(row, "program_input_invalid", str(exc))
                                      for row in state.get("normalized_manifest_rows", [])],
                    "maturity_audits": [{"status": "program_input_invalid", "message": str(exc)}]}

        def score(row):
            index = state["normalized_manifest_rows"].index(row)
            result = compute_maturity_scores(row, parameters, programs, self.output_dir / f"maturity_cells_{index:03d}.csv")
            audits.append(result["audit"])
            if result.get("cell_scores_path"):
                files[f"maturity_cells_{index:03d}"] = result["cell_scores_path"]
            return result["rows"]

        rows = self._execute_rows(state, "maturity", score)
        return {"maturity_rows": rows, "maturity_audits": audits, "maturity_cell_files": files}

    @audited_operation
    def _node_data_analysis_audit(self, state: DataAnalysisState) -> DataAnalysisState:
        limitations = list(state.get("limitations", []))
        for finding in state.get("input_gate_report", {}).get("findings", []):
            limitations.append({"limitation": finding.get("issue_type", "input_gate_finding"), **finding})
        limitations.extend({"limitation": "task_failed", **result} for result in state.get("task_results", [])
                           if result["status"] not in {"ok", "inspected"})
        validation_report = _build_validation_report(state, limitations)
        return {
            **state,
            "limitations": limitations,
            "validation_report": validation_report,
            "step_log": _append_step(state, "data_analysis_audit"),
        }

    @audited_operation
    def _node_phenotype_representation(self, state: DataAnalysisState) -> DataAnalysisState:
        tables = {
            "cell_type_composition": state.get("composition_rows", []),
            "marker_scores": state.get("marker_rows", []),
            "gene_program_scores": state.get("gene_program_rows", []),
            "gene_expression_summary": state.get("gene_expression_rows", []),
            "differential_expression": state.get("differential_expression_rows", []),
            "maturity_scores": state.get("maturity_rows", []),
            "similarity_scores": state.get("similarity_rows", []),
            "regulatory_program_scores": state.get("regulatory_rows", []),
            "trajectory_scores": state.get("trajectory_rows", []),
        }
        phenotype_ir = build_phenotype_ir(tables, state.get("dataset_summaries", []), state.get("validation_report", {}), str(self.output_dir))
        interpretation = interpret_phenotypes(
            phenotype_ir, self.interpreter, int(self.h5ad_config.get("interpretation", {}).get("trajectories", 3)),
        )
        return {**state, "phenotype_ir": phenotype_ir, "interpretation_report": interpretation,
                "step_log": _append_step(state, "phenotype_representation")}

    @audited_operation
    def _node_export_results(self, state: DataAnalysisState) -> DataAnalysisState:
        outputs = self._output_paths()
        result_state: Mapping[str, Any] = state
        selected = state.get("execution_plan", {}).get("requested_tasks", [])
        for task in selected:
            if task in TASK_OUTPUTS:
                state_key, table, _, _ = TASK_OUTPUTS[task]
                outputs[table] = self.output_dir / f"{table}.csv"
                write_csv_dicts(outputs[table], result_state.get(state_key, []))
        if "maturity" in selected:
            outputs.update(maturity_audit=self.output_dir / "maturity_audit.json",
                           **state.get("maturity_cell_files", {}))
            write_json(outputs["maturity_audit"], {"analyses": state.get("maturity_audits", [])})
        write_jsonl(outputs["h5ad_summary_by_protocol"], state.get("dataset_summaries", []))
        write_csv_dicts(outputs["data_analysis_protocol_summary"], _build_protocol_summary_rows(state.get("dataset_summaries", [])))
        write_json(
            outputs["h5ad_qc_report"],
            {
                "schema_version": "h5ad-qc-report-v1",
                "input_gate_report": state.get("input_gate_report", {}),
                "qc_rows": state.get("qc_rows", []),
                "step_log": state.get("step_log", []),
            },
        )
        write_json(outputs["h5ad_analysis_limitations"], {"limitations": state.get("limitations", [])})
        write_json(outputs["data_analysis_execution_plan"], state.get("execution_plan", {}))
        write_json(outputs["data_analysis_validation"], state.get("validation_report", {}))
        write_json(outputs["phenotype_ir"], state.get("phenotype_ir", {}))
        write_json(outputs["phenotype_interpretation"], state.get("interpretation_report", {}))
        if state.get("analysis_mode") == "exploratory":
            exploratory = state.get("exploratory_artifacts", {})
            outputs.update({
                "exploratory_analysis_script": self.output_dir / "exploratory" / "analysis.py",
                **{f"exploratory_{name}": self.output_dir / "exploratory" / f"{name}.json"
                   for name in ("environment", "config", "diagnostics")},
            })
            outputs["exploratory_analysis_script"].parent.mkdir(parents=True, exist_ok=True)
            outputs["exploratory_analysis_script"].write_text(str(exploratory.get("script") or ""), encoding="utf-8")
            for name in ("environment", "config", "diagnostics"):
                write_json(outputs[f"exploratory_{name}"], exploratory.get(name, {}))
        write_json(
            outputs["data_analysis_tool_registry"],
            {
                "tools": DATA_ANALYSIS_TOOL_REGISTRY,
                "callable_tools": sorted(self.callable_tools),
            },
        )
        write_json(outputs["data_analysis_comparison_report"], {
            "schema_version": "data-analysis-comparison-report-v2",
            "summary": {"n_datasets": len(state.get("dataset_summaries", [])),
                        "analysis_status": state.get("validation_report", {}).get("validation_status", "failed")},
            "comparison_axes": selected, "task_results": state.get("task_results", []),
        })
        write_json(outputs["data_analysis_task_results"], {"tasks": state.get("task_results", [])})
        write_json(
            outputs["data_analysis_query_result"],
            {
                "schema_version": "data-analysis-query-result-v1",
                "status": state.get("validation_report", {}).get("validation_status", "failed"),
                "analysis_mode": state.get("analysis_mode", "standard"),
                "validation_status": state.get("validation_report", {}).get("validation_status", "failed"),
                "task_validity": state.get("validation_report", {}).get("task_validity", {}),
                "requested_tasks": selected,
                "task_results": state.get("task_results", []),
                "supported_operations": [action["operation"] for action in action_registry()["actions"]
                                         if action["agent"] == "DA" and action["availability"] == "available" and action["operation"]],
                "output_files": {key: str(path) for key, path in outputs.items()},
            },
        )
        return {
            **state,
            "outputs": outputs,
            "step_log": _append_step(state, "export_results"),
        }

    def _output_paths(self) -> dict[str, Path]:
        return {
            "h5ad_summary_by_protocol": self.output_dir / "h5ad_summary_by_protocol.jsonl",
            "data_analysis_protocol_summary": self.output_dir / "data_analysis_protocol_summary.csv",
            "h5ad_qc_report": self.output_dir / "h5ad_qc_report.json",
            "h5ad_analysis_limitations": self.output_dir / "h5ad_analysis_limitations.json",
            "data_analysis_tool_registry": self.output_dir / "data_analysis_tool_registry.json",
            "data_analysis_comparison_report": self.output_dir / "data_analysis_comparison_report.json",
            "data_analysis_query_result": self.output_dir / "data_analysis_query_result.json",
            "data_analysis_execution_plan": self.output_dir / "data_analysis_execution_plan.json",
            "data_analysis_task_results": self.output_dir / "data_analysis_task_results.json",
            "data_analysis_validation": self.output_dir / "data_analysis_validation.json",
            "phenotype_ir": self.output_dir / "phenotype_ir.json",
            "phenotype_interpretation": self.output_dir / "phenotype_interpretation.json",
        }


def resolve_data_tasks(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve only requested operations; analysis options never implicitly schedule work."""
    requested = config.get("requested_tasks", ["summary"])
    if not isinstance(requested, list) or any(not isinstance(task, str) or not task for task in requested):
        raise ValueError("requested_tasks must be a list of nonempty task names")
    actions = {item["operation"]: item for item in action_registry()["actions"]
               if item["agent"] == "DA" and item["operation"]}
    requested = list(dict.fromkeys(requested))
    steps = []
    for task in requested:
        action = actions.get(task)
        parameters = _task_parameters(config, task)
        error = ""
        status = "pending"
        if not action or action["availability"] != "available":
            error, status = "Unknown or unavailable analysis method; no fallback was executed", "unavailable"
        else:
            error = _task_input_error(task, parameters)
            if error:
                status = "inputs_missing"
        steps.append({"task": task, "action_id": action["id"] if action else "",
                      "depends_on": ["dataset_inventory_builder"], "parameters": parameters,
                      "scientific_preconditions": action["scientific_preconditions"] if action else [],
                      "status": status, "error": error})
    return {"schema_version": "data-analysis-execution-plan-v2", "policy": "explicit_tasks_deterministic_resolution",
            "requested_tasks": requested,
            "prerequisites": [{"id": "data_input_gate", "depends_on": []},
                              {"id": "dataset_inventory_builder", "depends_on": ["data_input_gate"]}],
            "steps": steps}


def _task_parameters(config: dict[str, Any], task: str) -> dict[str, Any]:
    options = {"gene_expression": ("query_genes",),
               "differential_expression": ("differential_expression", "query_genes"),
               "marker_score": ("marker_sets",), "gene_program_score": ("gene_programs",),
               "maturity": ("maturity",)}
    excluded = {"requested_tasks", "query_genes", "differential_expression", "marker_sets", "gene_programs",
                "maturity", "exploratory_analysis", "interpretation", "analysis_mode"}
    parameters = {key: value for key, value in config.items() if key not in excluded}
    parameters.update({key: config[key] for key in options.get(task, ()) if key in config})
    if task == "differential_expression" and isinstance(parameters.get("differential_expression"), dict):
        parameters["differential_expression"] = dict(parameters["differential_expression"])
        parameters["differential_expression"].setdefault("groupby", _expression_groupby(parameters) or "")
    return deepcopy(parameters)


def _task_input_error(task: str, parameters: dict[str, Any]) -> str:
    if task == "gene_expression":
        genes = parameters.get("query_genes")
        if not isinstance(genes, list) or not genes or any(not isinstance(gene, str) or not gene.strip() for gene in genes):
            return "gene_expression requires nonempty query_genes"
    if task == "differential_expression":
        de = parameters.get("differential_expression")
        if not isinstance(de, dict) or any(not isinstance(de.get(key), str) or not de[key].strip()
                                           for key in ("groupby", "group_a", "group_b")):
            return "differential_expression requires groupby, group_a and group_b"
        if de["group_a"] == de["group_b"]:
            return "Differential expression groups must differ"
    if task in {"marker_score", "gene_program_score"}:
        key = "marker_sets" if task == "marker_score" else "gene_programs"
        if not isinstance(parameters.get(key), dict) or not parameters[key]:
            return f"{task} requires a nonempty {key} mapping"
        if task == "marker_score" and any(not isinstance(genes, list) or not genes
                or any(not isinstance(gene, str) or not gene.strip() for gene in genes)
                for genes in parameters[key].values()):
            return "marker_sets must map names to nonempty gene lists"
    if task == "maturity" and not isinstance(parameters.get("maturity", {}), dict):
        return "maturity configuration must be an object"
    return ""


def _expression_groupby(parameters: dict[str, Any]) -> str | None:
    return parameters.get("expression_groupby") or parameters.get("groupby")


def _task_failure(row: dict[str, Any], status: str, message: str) -> dict[str, Any]:
    return {**{key: row.get(key, "") for key in ("protocol_id", "dataset_id", "sample_id", "condition_id")},
            "status": status, "message": message}


def _build_qc_row(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_id": summary.get("protocol_id", ""),
        "dataset_id": summary.get("dataset_id", ""),
        "h5ad_file_exists": summary.get("h5ad_file_exists", False),
        "dataset_kind": summary.get("dataset_kind", ""),
        "cell_type_column": summary.get("cell_type_column", ""),
        "batch_column": summary.get("batch_column", ""),
        "lineage_column": summary.get("lineage_column", ""),
        "n_cells_total": summary.get("n_cells_total", ""),
        "n_cells_selected": summary.get("n_cells_selected", ""),
        "cell_filter": summary.get("cell_filter", ""),
        "cell_filter_status": summary.get("cell_filter_status", ""),
        "analysis_status": summary.get("analysis_status", ""),
    }


def _dataset_summary_from_inspection(row: dict[str, Any], inspection: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_id": row.get("protocol_id", ""),
        "dataset_id": row.get("dataset_id", ""),
        "condition_id": row.get("condition_id", ""),
        "sample_id": row.get("sample_id", ""),
        "h5ad_path": row.get("h5ad_path", ""),
        "h5ad_file_exists": Path(str(row.get("h5ad_path", ""))).exists() if row.get("h5ad_path") else False,
        "dataset_kind": row.get("dataset_kind", ""),
        "analysis_status": inspection.get("status", ""),
        "n_obs": inspection.get("n_obs", ""),
        "n_vars": inspection.get("n_vars", ""),
        "n_cells_total": inspection.get("n_cells_total", ""),
        "n_cells_selected": inspection.get("n_cells_selected", ""),
        "cell_filter": inspection.get("cell_filter", ""),
        "cell_filter_status": inspection.get("cell_filter_status", ""),
        "cell_type_column": row.get("cell_type_column", ""),
        "batch_column": row.get("batch_column", ""),
        "lineage_column": row.get("lineage_column", ""),
        "missing_metadata_columns": "|".join(inspection.get("missing_metadata_columns", [])),
        "design_metadata": inspection.get("design_metadata", {}),
    }


def _compute_gene_program_scores(
    row: dict[str, Any],
    h5ad_config: dict[str, Any],
    gene_programs: dict[str, Any],
    groupby: str | None,
) -> list[dict[str, Any]]:
    if not gene_programs:
        return []
    marker_sets: dict[str, list[str]] = {}
    program_lineages: dict[str, str] = {}
    for lineage, programs in gene_programs.items():
        if isinstance(programs, dict):
            for program_name, genes in programs.items():
                key = f"{lineage}:{program_name}"
                marker_sets[key] = _program_genes(genes)
                program_lineages[key] = str(lineage)
        elif isinstance(programs, list):
            key = f"{lineage}:default_program"
            marker_sets[key] = [str(gene) for gene in programs]
            program_lineages[key] = str(lineage)
    score_rows = compute_marker_scores(row, h5ad_config, marker_sets, groupby=groupby)
    rows: list[dict[str, Any]] = []
    for score in score_rows:
        marker_set = str(score.get("marker_set", ""))
        _, _, program_name = marker_set.partition(":")
        rows.append(
            {
                "protocol_id": score.get("protocol_id", ""),
                "dataset_id": score.get("dataset_id", ""),
                "condition_id": score.get("condition_id", ""),
                "sample_id": score.get("sample_id", ""),
                "lineage": program_lineages.get(marker_set, ""),
                "gene_program": program_name or marker_set,
                "groupby": score.get("groupby", ""),
                "group": score.get("group", ""),
                "n_genes_input": score.get("n_genes_input", ""),
                "n_genes_found": score.get("n_genes_found", ""),
                "program_score": score.get("score", ""),
                "expression_source": score.get("expression_source", ""),
                "message": score.get("message", ""),
                "status": score.get("status", ""),
            }
        )
    return rows


def _program_genes(program: Any) -> list[str]:
    if isinstance(program, dict) and isinstance(program.get("genes"), list):
        return [str(gene) for gene in program["genes"]]
    if isinstance(program, list):
        return [str(gene) for gene in program]
    return []


def _build_protocol_summary_rows(dataset_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in dataset_summaries:
        grouped.setdefault(str(row.get("protocol_id") or ""), []).append(row)
    rows: list[dict[str, Any]] = []
    for protocol_id, summaries in sorted(grouped.items()):
        if not protocol_id:
            continue
        rows.append(
            {
                "protocol_id": protocol_id,
                "n_datasets": len(summaries),
                "n_existing_h5ad_files": sum(1 for row in summaries if row.get("h5ad_file_exists")),
                "dataset_ids": "|".join(str(row.get("dataset_id") or "") for row in summaries),
                "analysis_status": "metadata_inspected",
            }
        )
    return rows


def _append_step(state: DataAnalysisState, node: str) -> list[dict[str, Any]]:
    return [*state.get("step_log", []), {"node": node, "status": "completed"}]


def _build_validation_report(
    state: DataAnalysisState, limitations: list[dict[str, Any]]
) -> dict[str, Any]:
    de_parameters: dict[str, Any] = next((step["parameters"] for step in state.get("execution_plan", {}).get("steps", [])
                          if step["task"] == "differential_expression"), {})
    result_state: Mapping[str, Any] = state
    requested_tasks = state.get("execution_plan", {}).get("requested_tasks", [])
    summaries = state.get("dataset_summaries", [])
    findings = state.get("input_gate_report", {}).get("findings", [])
    expression_rows = state.get("gene_expression_rows", [])
    expression_dependent_rows = [
        *expression_rows,
        *state.get("marker_rows", []),
        *state.get("gene_program_rows", []),
        *state.get("differential_expression_rows", []),
    ]
    de_rows = state.get("differential_expression_rows", [])

    checks = [
        _validation_check(
            "manifest_identity",
            bool(summaries) and not any(finding.get("severity") == "high" for finding in findings),
            "Manifest rows must identify a dataset and h5ad path.",
        ),
        _validation_check(
            "cell_selection",
            bool(summaries)
            and all(
                row.get("cell_filter_status") == "ok" and int(row.get("n_cells_selected") or 0) > 0
                for row in summaries
            ),
            "Every manifest row must select at least one cell using its declared obs filters.",
        ),
        _rows_validation_check(
            "gene_resolution",
            [*expression_rows, *de_rows],
            requested=bool({"gene_expression", "differential_expression"} & set(requested_tasks)),
            blocked_statuses={"gene_not_found"},
            detail="Every requested gene must resolve against the selected expression source.",
        ),
        _rows_validation_check(
            "expression_source",
            expression_dependent_rows,
            requested=bool({"gene_expression", "differential_expression", "marker_score", "gene_program_score"} & set(requested_tasks)),
            blocked_statuses={"expression_source_missing", "expression_source_conflict"},
            detail="The configured X, layer, or raw.X source must exist and be unambiguous.",
        ),
        _rows_validation_check(
            "comparison_groups",
            de_rows,
            requested="differential_expression" in requested_tasks,
            blocked_statuses={"insufficient_groups_for_de", "obs_column_missing"},
            detail="Both differential-expression groups must contain selected cells.",
        ),
        _validation_check(
            "expression_scale",
            "differential_expression" not in requested_tasks
            or str(de_parameters.get("expression_scale") or "").lower()
            in {
                "counts",
                "raw_counts",
                "normalized",
                "normalized_counts",
                "cpm",
                "tpm",
                "log",
                "log1p",
                "scaled",
                "zscore",
            },
            "Differential expression requires an explicit counts, normalized, or log expression_scale.",
        ),
    ]

    exploratory_check = _exploratory_validation_check(state)
    checks.append(exploratory_check)
    technical_checks = [check for check in checks if check["check_id"] in {"manifest_identity", "cell_selection", "exploratory_artifacts"}]
    technical_ok = all(check["passed"] for check in technical_checks)
    metric_specs = {task: (result_state.get(spec[0], []), spec[2]) for task, spec in TASK_OUTPUTS.items()}
    metric_status = {name: _numeric_rows_validation_check(name, rows, fields, detail="Task-specific numeric validity.")
                     for name, (rows, fields) in metric_specs.items()}
    metric_status["reference_similarity"] = {"check_id": "reference_similarity", "passed": False, "status": "unavailable",
                                             "availability": get_action("phenotype.reference_similarity").availability}
    metric_status["summary"] = {"check_id": "summary", "passed": technical_ok, "status": "passed" if technical_ok else "failed"}
    outcomes = {result["task"]: result for result in state.get("task_results", [])}
    for task in requested_tasks:
        outcome = outcomes.get(task, {})
        if task not in metric_status or outcome.get("status") in {"unavailable", "inputs_missing"}:
            metric_status[task] = {"check_id": task, "passed": False, "status": outcome.get("status", "failed"),
                                   "detail": outcome.get("message", "No numeric result")}
    for task, result in metric_status.items():
        if task not in requested_tasks and result["status"] != "unavailable":
            result.update(passed=False, status="not_requested")
    if not next(check["passed"] for check in checks if check["check_id"] == "expression_scale"):
        metric_status["differential_expression"]["passed"] = False
        metric_status["differential_expression"]["status"] = "failed"
    checks.extend(metric_status.values())
    passed = technical_ok and all(metric_status.get(task, {}).get("passed") is True for task in requested_tasks)
    return {
        "schema_version": "data-analysis-validation-v2",
        "analysis_mode": state.get("analysis_mode", "standard"),
        "validation_status": "passed" if passed else "failed",
        "requested_tasks": sorted(set(requested_tasks)),
        "technical_qc": {"passed": technical_ok, "checks": technical_checks},
        "metric_status": metric_status,
        "task_validity": {task: {"valid": technical_ok and value["passed"], "checks": value}
                          for task, value in metric_status.items()},
        "statistical_applicability": {
            "differential_expression": "not_requested" if "differential_expression" not in requested_tasks
                else "exploratory_replicate_level" if de_rows and all(
                    str(row.get("inference_eligible")).lower() == "true" for row in de_rows) else "descriptive_only",
            "policy": "Technical success is not independent-replicate inference or protocol comparability.",
        },
        "checks": checks,
        "n_limitations": len(limitations),
        "quality_interpretation": (
            "Deterministic readiness gate only. Any LLM-authored qualitative score is commentary, "
            "not a statistical confidence probability."
        ),
    }


def _validation_check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check_id": check_id, "passed": bool(passed), "detail": detail}


def _rows_validation_check(
    check_id: str,
    rows: list[dict[str, Any]],
    *,
    requested: bool,
    blocked_statuses: set[str],
    detail: str,
) -> dict[str, Any]:
    if not requested:
        return {"check_id": check_id, "passed": True, "status": "not_requested", "detail": detail}
    statuses = {str(row.get("status") or "") for row in rows}
    passed = bool(rows) and not statuses.intersection(blocked_statuses)
    return {
        "check_id": check_id,
        "passed": passed,
        "status": "passed" if passed else "failed",
        "observed_statuses": sorted(statuses),
        "detail": detail,
    }


def _numeric_rows_validation_check(
    check_id: str,
    rows: list[dict[str, Any]],
    numeric_fields: tuple[str, ...],
    *,
    detail: str,
) -> dict[str, Any]:
    passed = bool(rows) and all(
        str(row.get("status") or "") in {"ok", "numeric", "computed", "passed"}
        and all(_is_finite_number(row.get(field)) for field in numeric_fields)
        for row in rows
    )
    return {
        "check_id": check_id,
        "passed": passed,
        "status": "passed" if passed else "failed",
        "numeric_fields": list(numeric_fields),
        "detail": detail,
    }


def _is_finite_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in {float("inf"), float("-inf")}


def _exploratory_validation_check(state: DataAnalysisState) -> dict[str, Any]:
    if state.get("analysis_mode") != "exploratory":
        return {
            "check_id": "exploratory_artifacts",
            "passed": True,
            "status": "not_requested",
            "detail": "Exploratory analysis was not requested.",
        }
    artifacts = state.get("exploratory_artifacts", {})
    diagnostics = artifacts.get("diagnostics", {})
    declared_checks = artifacts.get("validation_checks", [])
    passed = bool(
        artifacts.get("script")
        and artifacts.get("environment")
        and artifacts.get("config")
        and diagnostics.get("execution_succeeded") is True
        and declared_checks
        and all(isinstance(check, dict) and check.get("passed") is True for check in declared_checks)
    )
    return {
        "check_id": "exploratory_artifacts",
        "passed": passed,
        "status": "passed" if passed else "failed",
        "detail": (
            "Exploratory code must run in an external sandbox and preserve its script, environment, "
            "configuration, diagnostics, and deterministic validation checks."
        ),
    }


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
