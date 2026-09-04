from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from protocol_optimization_agent.io import write_csv_dicts, write_json, write_jsonl
from protocol_optimization_agent.tools.external_data_tools import (
    build_external_tool_inventory,
    get_external_data_analysis_tools,
)
from protocol_optimization_agent.tools.h5ad_tools import (
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


class DataAnalysisState(TypedDict, total=False):
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
    ) -> None:
        self.output_dir = output_dir
        self.h5ad_config = h5ad_config or {}
        self.reference_atlas_path = Path(reference_atlas_path) if reference_atlas_path else None
        self.gene_programs = gene_programs or self.h5ad_config.get("gene_programs", {}) or {}
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.callable_tools = get_external_data_analysis_tools(self.project_root)

    def run(
        self,
        manifest_rows: list[dict[str, Any]],
        reference_atlas_path: Path | str | None = None,
        gene_programs: dict[str, Any] | None = None,
    ) -> dict[str, Path]:
        effective_reference = Path(reference_atlas_path) if reference_atlas_path else self.reference_atlas_path
        effective_gene_programs = gene_programs if gene_programs is not None else self.gene_programs
        initial_state: DataAnalysisState = {
            "manifest_rows": manifest_rows,
            "reference_atlas_path": str(effective_reference) if effective_reference else "",
            "gene_programs": effective_gene_programs or {},
            "h5ad_config": {**self.h5ad_config, "gene_programs": effective_gene_programs or {}},
            "step_log": [],
        }
        final_state = self.build_langgraph().invoke(initial_state)
        return final_state["outputs"]

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
        graph.add_node("export_results", self._node_export_results)

        graph.add_edge(START, "data_input_gate")
        graph.add_edge("data_input_gate", "dataset_inventory_builder")
        graph.add_edge("dataset_inventory_builder", "metadata_and_qc_summary")
        graph.add_edge("metadata_and_qc_summary", "cell_type_composition_analysis")
        graph.add_edge("cell_type_composition_analysis", "marker_and_gene_program_scoring")
        graph.add_edge("marker_and_gene_program_scoring", "maturity_scoring")
        graph.add_edge("maturity_scoring", "reference_similarity_analysis")
        graph.add_edge("reference_similarity_analysis", "regulatory_and_trajectory_placeholders")
        graph.add_edge("regulatory_and_trajectory_placeholders", "external_tool_inventory")
        graph.add_edge("external_tool_inventory", "aggregate_quantitative_comparison")
        graph.add_edge("aggregate_quantitative_comparison", "data_analysis_audit")
        graph.add_edge("data_analysis_audit", "export_results")
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

    def _node_dataset_inventory_builder(self, state: DataAnalysisState) -> DataAnalysisState:
        summaries: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = []
        for row in state.get("normalized_manifest_rows", []):
            result = inspect_h5ad_dataset(row, state.get("h5ad_config", {}))
            summaries.append(_dataset_summary_from_inspection(row, result))
            limitations.extend(result.get("limitations", []))
        return {
            **state,
            "dataset_summaries": summaries,
            "limitations": limitations,
            "step_log": _append_step(state, "dataset_inventory_builder"),
        }

    def _node_metadata_and_qc_summary(self, state: DataAnalysisState) -> DataAnalysisState:
        qc_rows = [_build_qc_row(row) for row in state.get("dataset_summaries", [])]
        return {
            **state,
            "qc_rows": qc_rows,
            "step_log": _append_step(state, "metadata_and_qc_summary"),
        }

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

    def _node_maturity_scoring(self, state: DataAnalysisState) -> DataAnalysisState:
        rows: list[dict[str, Any]] = []
        for row in state.get("normalized_manifest_rows", []):
            rows.extend(score_gene_program_maturity_placeholder(row, state.get("gene_programs", {})))
        return {
            **state,
            "maturity_rows": rows,
            "step_log": _append_step(state, "maturity_scoring"),
        }

    def _node_reference_similarity_analysis(self, state: DataAnalysisState) -> DataAnalysisState:
        rows: list[dict[str, Any]] = []
        for row in state.get("normalized_manifest_rows", []):
            rows.extend(compare_reference_similarity_placeholder(row, state.get("h5ad_config", {})))
        return {
            **state,
            "similarity_rows": rows,
            "step_log": _append_step(state, "reference_similarity_analysis"),
        }

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

    def _node_external_tool_inventory(self, state: DataAnalysisState) -> DataAnalysisState:
        return {
            **state,
            "external_tool_inventory": build_external_tool_inventory(self.project_root),
            "step_log": _append_step(state, "external_tool_inventory"),
        }

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

    def _node_data_analysis_audit(self, state: DataAnalysisState) -> DataAnalysisState:
        limitations = list(state.get("limitations", []))
        for finding in state.get("input_gate_report", {}).get("findings", []):
            limitations.append({"limitation": finding.get("issue_type", "input_gate_finding"), **finding})
        return {
            **state,
            "limitations": limitations,
            "step_log": _append_step(state, "data_analysis_audit"),
        }

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
