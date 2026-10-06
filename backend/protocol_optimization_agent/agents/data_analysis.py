from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from protocol_optimization_agent.analysis.phenotype.external_tools import (
    build_external_tool_inventory,
    get_external_data_analysis_tools,
)
from protocol_optimization_agent.analysis.phenotype.h5ad_tools import (
    DATA_ANALYSIS_TOOL_REGISTRY,
    build_placeholder_composition_rows,
    build_placeholder_gene_program_rows,
    build_placeholder_marker_score_rows,
    build_placeholder_regulatory_rows,
    build_placeholder_trajectory_rows,
    compare_reference_similarity_placeholder,
    compute_cell_type_composition,
    compute_differential_expression,
    compute_marker_scores,
    inspect_h5ad_dataset,
    query_gene_expression,
    score_gene_program_maturity_placeholder,
    validate_data_analysis_inputs,
)
from protocol_optimization_agent.analysis.phenotype.representation import (
    CONTEXT_FIELDS,
    build_phenotype_ir,
    interpret_phenotypes,
    make_phenotype_interpreter,
)
from protocol_optimization_agent.runtime.archive import audited_agent_run, audited_operation
from protocol_optimization_agent.runtime.io import write_csv_dicts, write_json, write_jsonl
from protocol_optimization_agent.runtime.llm_client import LLMClient, build_llm_client


class DataAnalysisState(TypedDict, total=False):
    analysis_mode: str
    execution_plan: dict[str, Any]
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
    similarity_rows: list[dict[str, Any]]
    regulatory_rows: list[dict[str, Any]]
    trajectory_rows: list[dict[str, Any]]
    external_tool_inventory: list[dict[str, Any]]
    protocol_summary_rows: list[dict[str, Any]]
    quantitative_comparison_report: dict[str, Any]
    limitations: list[dict[str, Any]]
    outputs: dict[str, Path]
    step_log: list[dict[str, Any]]


class DataAnalysisAgent:
    """Build quantitative organoid-vs-reference dataset comparison outputs.

    The framework is executable today without scanpy/anndata. Numeric methods that
    require h5ad matrix access are represented as stable tool contracts with
    pending outputs, so downstream integration can depend on output schemas now.
    """

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
        graph.add_node("analysis_execution_planner", self._node_analysis_execution_planner)
        graph.add_node("dataset_inventory_builder", self._node_dataset_inventory_builder)
        graph.add_node("metadata_and_qc_summary", self._node_metadata_and_qc_summary)
        graph.add_node("cell_type_composition_analysis", self._node_cell_type_composition_analysis)
        graph.add_node("marker_and_gene_program_scoring", self._node_marker_and_gene_program_scoring)
        graph.add_node("maturity_scoring", self._node_maturity_scoring)
        graph.add_node("reference_similarity_analysis", self._node_reference_similarity_analysis)
        graph.add_node("regulatory_and_trajectory_placeholders", self._node_regulatory_and_trajectory_placeholders)
        graph.add_node("external_tool_inventory", self._node_external_tool_inventory)
        graph.add_node("aggregate_quantitative_comparison", self._node_aggregate_quantitative_comparison)
        graph.add_node("data_analysis_audit", self._node_data_analysis_audit)
        graph.add_node("phenotype_representation", self._node_phenotype_representation)
        graph.add_node("export_results", self._node_export_results)

        graph.add_edge(START, "data_input_gate")
        graph.add_edge("data_input_gate", "analysis_execution_planner")
        graph.add_edge("analysis_execution_planner", "dataset_inventory_builder")
        graph.add_edge("dataset_inventory_builder", "metadata_and_qc_summary")
        graph.add_edge("metadata_and_qc_summary", "cell_type_composition_analysis")
        graph.add_edge("cell_type_composition_analysis", "marker_and_gene_program_scoring")
        graph.add_edge("marker_and_gene_program_scoring", "maturity_scoring")
        graph.add_edge("maturity_scoring", "reference_similarity_analysis")
        graph.add_edge("reference_similarity_analysis", "regulatory_and_trajectory_placeholders")
        graph.add_edge("regulatory_and_trajectory_placeholders", "external_tool_inventory")
        graph.add_edge("external_tool_inventory", "aggregate_quantitative_comparison")
        graph.add_edge("aggregate_quantitative_comparison", "data_analysis_audit")
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
    def _node_analysis_execution_planner(self, state: DataAnalysisState) -> DataAnalysisState:
        requested_mode = str(state.get("analysis_mode") or "standard").lower()
        mode = requested_mode if requested_mode in {"standard", "exploratory"} else "standard"
        exploratory = state.get("h5ad_config", {}).get("exploratory_analysis", {})
        plan = {
            "schema_version": "data-analysis-execution-plan-v1",
            "requested_mode": requested_mode,
            "analysis_mode": mode,
            "planner_policy": "validated_tools_first_then_audited_exploration",
            "standard_tools": [
                "dataset_manifest_gate",
                "cell_type_composition",
                "query_gene_expression",
                "differential_expression",
                "marker_score_analysis",
                "gene_program_maturity_score",
                "reference_similarity",
            ],
            "exploratory_code_execution": "external_sandbox_required",
        }
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
            "step_log": _append_step(state, "analysis_execution_planner"),
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
            "limitations": limitations,
            "step_log": _append_step(state, "dataset_inventory_builder"),
        }

    @audited_operation
    def _node_metadata_and_qc_summary(self, state: DataAnalysisState) -> DataAnalysisState:
        qc_rows = [_build_qc_row(row) for row in state.get("dataset_summaries", [])]
        return {
            **state,
            "qc_rows": qc_rows,
            "step_log": _append_step(state, "metadata_and_qc_summary"),
        }

    @audited_operation
    def _node_cell_type_composition_analysis(self, state: DataAnalysisState) -> DataAnalysisState:
        rows: list[dict[str, Any]] = []
        for row in state.get("normalized_manifest_rows", []):
            computed = compute_cell_type_composition(row, state.get("h5ad_config", {}))
            rows.extend(computed if computed else build_placeholder_composition_rows(row))
        return {
            **state,
            "composition_rows": rows,
            "step_log": _append_step(state, "cell_type_composition_analysis"),
        }

    @audited_operation
    def _node_marker_and_gene_program_scoring(self, state: DataAnalysisState) -> DataAnalysisState:
        marker_rows: list[dict[str, Any]] = []
        gene_program_rows: list[dict[str, Any]] = []
        gene_expression_rows: list[dict[str, Any]] = []
        differential_expression_rows: list[dict[str, Any]] = []
        h5ad_config = state.get("h5ad_config", {})
        marker_sets = h5ad_config.get("marker_sets", {})
        query_genes = h5ad_config.get("query_genes", [])
        expression_groupby = h5ad_config.get("expression_groupby") or h5ad_config.get("groupby")
        de_config = h5ad_config.get("differential_expression", {})
        for row in state.get("normalized_manifest_rows", []):
            if marker_sets:
                marker_rows.extend(compute_marker_scores(row, h5ad_config, marker_sets, groupby=expression_groupby))
            else:
                marker_rows.extend(build_placeholder_marker_score_rows(row))
            gene_program_rows.extend(_compute_gene_program_scores(row, h5ad_config, state.get("gene_programs", {}), expression_groupby))
            if query_genes:
                gene_expression_rows.extend(query_gene_expression(row, h5ad_config, query_genes, groupby=expression_groupby))
            if de_config:
                differential_expression_rows.extend(
                    compute_differential_expression(
                        row,
                        h5ad_config,
                        groupby=str(de_config.get("groupby") or expression_groupby or ""),
                        group_a=str(de_config.get("group_a") or ""),
                        group_b=str(de_config.get("group_b") or ""),
                        genes=de_config.get("genes") or query_genes or None,
                    )
                )
        return {
            **state,
            "marker_rows": marker_rows,
            "gene_program_rows": gene_program_rows,
            "gene_expression_rows": gene_expression_rows,
            "differential_expression_rows": differential_expression_rows,
            "step_log": _append_step(state, "marker_and_gene_program_scoring"),
        }

    @audited_operation
    def _node_maturity_scoring(self, state: DataAnalysisState) -> DataAnalysisState:
        rows: list[dict[str, Any]] = []
        for row in state.get("normalized_manifest_rows", []):
            rows.extend(score_gene_program_maturity_placeholder(row, state.get("gene_programs", {})))
        return {
            **state,
            "maturity_rows": rows,
            "step_log": _append_step(state, "maturity_scoring"),
        }

    @audited_operation
    def _node_reference_similarity_analysis(self, state: DataAnalysisState) -> DataAnalysisState:
        rows: list[dict[str, Any]] = []
        for row in state.get("normalized_manifest_rows", []):
            rows.extend(compare_reference_similarity_placeholder(row, state.get("h5ad_config", {})))
        return {
            **state,
            "similarity_rows": rows,
            "step_log": _append_step(state, "reference_similarity_analysis"),
        }

    @audited_operation
    def _node_regulatory_and_trajectory_placeholders(self, state: DataAnalysisState) -> DataAnalysisState:
        regulatory_rows: list[dict[str, Any]] = []
        trajectory_rows: list[dict[str, Any]] = []
        for row in state.get("normalized_manifest_rows", []):
            regulatory_rows.extend(build_placeholder_regulatory_rows(row))
            trajectory_rows.extend(build_placeholder_trajectory_rows(row))
        return {
            **state,
            "regulatory_rows": regulatory_rows,
            "trajectory_rows": trajectory_rows,
            "step_log": _append_step(state, "regulatory_and_trajectory_placeholders"),
        }

    @audited_operation
    def _node_external_tool_inventory(self, state: DataAnalysisState) -> DataAnalysisState:
        return {
            **state,
            "external_tool_inventory": build_external_tool_inventory(self.project_root),
            "step_log": _append_step(state, "external_tool_inventory"),
        }

    @audited_operation
    def _node_aggregate_quantitative_comparison(self, state: DataAnalysisState) -> DataAnalysisState:
        protocol_summary_rows = _build_protocol_summary_rows(state.get("dataset_summaries", []))
        report = {
            "schema_version": "data-analysis-comparison-report-v1",
            "summary": {
                "n_datasets": len(state.get("dataset_summaries", [])),
                "n_protocols": len(
                    {row.get("protocol_id") for row in state.get("dataset_summaries", []) if row.get("protocol_id")}
                ),
                "n_gene_program_lineages": len(state.get("gene_programs", {})),
                "analysis_status": "framework_ready_numeric_tools_pending",
            },
            "tool_registry": DATA_ANALYSIS_TOOL_REGISTRY,
            "external_tool_inventory": state.get("external_tool_inventory", []),
            "callable_tools": sorted(self.callable_tools),
            "comparison_axes": [
                "cell_type_composition",
                "marker_expression",
                "gene_program_maturity",
                "organoid_reference_similarity",
                "gene_regulatory_program",
                "developmental_trajectory",
            ],
        }
        return {
            **state,
            "protocol_summary_rows": protocol_summary_rows,
            "quantitative_comparison_report": report,
            "step_log": _append_step(state, "aggregate_quantitative_comparison"),
        }

    @audited_operation
    def _node_data_analysis_audit(self, state: DataAnalysisState) -> DataAnalysisState:
        limitations = list(state.get("limitations", []))
        for finding in state.get("input_gate_report", {}).get("findings", []):
            limitations.append({"limitation": finding.get("issue_type", "input_gate_finding"), **finding})
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
        write_jsonl(outputs["h5ad_summary_by_protocol"], state.get("dataset_summaries", []))
        write_csv_dicts(outputs["maturity_scores"], state.get("maturity_rows", []))
        write_csv_dicts(outputs["similarity_scores"], state.get("similarity_rows", []))
        write_csv_dicts(outputs["cell_type_composition"], state.get("composition_rows", []))
        write_csv_dicts(outputs["marker_scores"], state.get("marker_rows", []))
        write_csv_dicts(outputs["gene_program_scores"], state.get("gene_program_rows", []))
        write_csv_dicts(outputs["gene_expression_summary"], state.get("gene_expression_rows", []))
        write_csv_dicts(outputs["differential_expression"], state.get("differential_expression_rows", []))
        write_csv_dicts(outputs["regulatory_program_scores"], state.get("regulatory_rows", []))
        write_csv_dicts(outputs["trajectory_scores"], state.get("trajectory_rows", []))
        write_csv_dicts(outputs["external_tool_inventory"], state.get("external_tool_inventory", []))
        write_csv_dicts(outputs["data_analysis_protocol_summary"], state.get("protocol_summary_rows", []))
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
        exploratory = state.get("exploratory_artifacts", {})
        outputs["exploratory_analysis_script"].parent.mkdir(parents=True, exist_ok=True)
        outputs["exploratory_analysis_script"].write_text(
            str(exploratory.get("script") or "# No exploratory analysis requested.\n"), encoding="utf-8"
        )
        write_json(outputs["exploratory_environment"], exploratory.get("environment", {}))
        write_json(outputs["exploratory_config"], exploratory.get("config", {}))
        write_json(outputs["exploratory_diagnostics"], exploratory.get("diagnostics", {}))
        write_json(
            outputs["data_analysis_tool_registry"],
            {
                "tools": DATA_ANALYSIS_TOOL_REGISTRY,
                "external_tool_inventory": state.get("external_tool_inventory", []),
                "callable_tools": sorted(self.callable_tools),
            },
        )
        write_json(outputs["data_analysis_comparison_report"], state.get("quantitative_comparison_report", {}))
        write_json(
            outputs["data_analysis_query_result"],
            {
                "schema_version": "data-analysis-query-result-v1",
                "status": "ready_for_orchestrator_dispatch",
                "analysis_mode": state.get("analysis_mode", "standard"),
                "validation_status": state.get("validation_report", {}).get("validation_status", "failed"),
                "task_validity": state.get("validation_report", {}).get("task_validity", {}),
                "supported_operations": [
                    "summary",
                    "cell_type_composition",
                    "marker_score",
                    "gene_expression",
                    "differential_expression",
                    "maturity",
                    "reference_similarity",
                ],
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
            "maturity_scores": self.output_dir / "maturity_scores.csv",
            "similarity_scores": self.output_dir / "similarity_scores.csv",
            "cell_type_composition": self.output_dir / "cell_type_composition.csv",
            "marker_scores": self.output_dir / "marker_scores.csv",
            "gene_program_scores": self.output_dir / "gene_program_scores.csv",
            "gene_expression_summary": self.output_dir / "gene_expression_summary.csv",
            "differential_expression": self.output_dir / "differential_expression.csv",
            "regulatory_program_scores": self.output_dir / "regulatory_program_scores.csv",
            "trajectory_scores": self.output_dir / "trajectory_scores.csv",
            "external_tool_inventory": self.output_dir / "external_tool_inventory.csv",
            "data_analysis_protocol_summary": self.output_dir / "data_analysis_protocol_summary.csv",
            "h5ad_qc_report": self.output_dir / "h5ad_qc_report.json",
            "h5ad_analysis_limitations": self.output_dir / "h5ad_analysis_limitations.json",
            "data_analysis_tool_registry": self.output_dir / "data_analysis_tool_registry.json",
            "data_analysis_comparison_report": self.output_dir / "data_analysis_comparison_report.json",
            "data_analysis_query_result": self.output_dir / "data_analysis_query_result.json",
            "data_analysis_execution_plan": self.output_dir / "data_analysis_execution_plan.json",
            "data_analysis_validation": self.output_dir / "data_analysis_validation.json",
            "phenotype_ir": self.output_dir / "phenotype_ir.json",
            "phenotype_interpretation": self.output_dir / "phenotype_interpretation.json",
            "exploratory_analysis_script": self.output_dir / "exploratory" / "analysis.py",
            "exploratory_environment": self.output_dir / "exploratory" / "environment.json",
            "exploratory_config": self.output_dir / "exploratory" / "config.json",
            "exploratory_diagnostics": self.output_dir / "exploratory" / "diagnostics.json",
        }


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
    }


def _compute_gene_program_scores(
    row: dict[str, Any],
    h5ad_config: dict[str, Any],
    gene_programs: dict[str, Any],
    groupby: str | None,
) -> list[dict[str, Any]]:
    if not gene_programs:
        return build_placeholder_gene_program_rows(row, gene_programs)
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
                "analysis_status": "framework_ready_numeric_tools_pending",
            }
        )
    return rows


def _append_step(state: DataAnalysisState, node: str) -> list[dict[str, Any]]:
    return [*state.get("step_log", []), {"node": node, "status": "completed"}]


def _build_validation_report(
    state: DataAnalysisState, limitations: list[dict[str, Any]]
) -> dict[str, Any]:
    config = state.get("h5ad_config", {})
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
            requested=bool(config.get("query_genes") or config.get("differential_expression")),
            blocked_statuses={"gene_not_found"},
            detail="Every requested gene must resolve against the selected expression source.",
        ),
        _rows_validation_check(
            "expression_source",
            expression_dependent_rows,
            requested=bool(
                config.get("query_genes")
                or config.get("marker_sets")
                or state.get("gene_programs")
                or config.get("differential_expression")
            ),
            blocked_statuses={"expression_source_missing", "expression_source_conflict"},
            detail="The configured X, layer, or raw.X source must exist and be unambiguous.",
        ),
        _rows_validation_check(
            "comparison_groups",
            de_rows,
            requested=bool(config.get("differential_expression")),
            blocked_statuses={"insufficient_groups_for_de", "obs_column_missing"},
            detail="Both differential-expression groups must contain selected cells.",
        ),
        _validation_check(
            "expression_scale",
            not config.get("differential_expression")
            or str(config.get("expression_scale") or "").lower()
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
        _numeric_rows_validation_check(
            "maturity_scoring",
            state.get("maturity_rows", []),
            ("maturity_score",),
            detail="Maturity rows must contain validated numeric results before protocol optimization.",
        ),
        _numeric_rows_validation_check(
            "reference_similarity",
            state.get("similarity_rows", []),
            ("cosine_similarity", "euclidean_distance"),
            detail="Reference-similarity rows must contain validated numeric results before protocol optimization.",
        ),
    ]

    exploratory_check = _exploratory_validation_check(state)
    checks.append(exploratory_check)
    technical_checks = [check for check in checks if check["check_id"] in {"manifest_identity", "cell_selection", "exploratory_artifacts"}]
    technical_ok = all(check["passed"] for check in technical_checks)
    metric_specs = {
        "cell_type_composition": (state.get("composition_rows", []), ("n_cells",)),
        "gene_expression": (expression_rows, ("mean_expression",)),
        "marker_score": (state.get("marker_rows", []), ("mean_score",)),
        "differential_expression": (de_rows, ("mean_diff",)),
        "maturity": (state.get("maturity_rows", []), ("maturity_score",)),
        "reference_similarity": (state.get("similarity_rows", []), ("cosine_similarity", "euclidean_distance")),
    }
    metric_status = {name: _numeric_rows_validation_check(name, rows, fields, detail="Task-specific numeric validity.")
                     for name, (rows, fields) in metric_specs.items()}
    if not next(check["passed"] for check in checks if check["check_id"] == "expression_scale"):
        metric_status["differential_expression"]["passed"] = False
        metric_status["differential_expression"]["status"] = "failed"
    requested_tasks = config.get("requested_tasks") or ["cell_type_composition"]
    if not isinstance(requested_tasks, list) or any(not isinstance(task, str) or task not in metric_specs for task in requested_tasks):
        raise ValueError("requested_tasks must be a list of supported analysis tasks")
    if config.get("query_genes"):
        requested_tasks = [*requested_tasks, "gene_expression"]
    if config.get("differential_expression"):
        requested_tasks = [*requested_tasks, "differential_expression"]
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
            "differential_expression": "exploratory_replicate_level" if de_rows and all(
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
