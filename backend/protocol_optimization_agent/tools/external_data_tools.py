from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import Any

DATA_ANALYSIS_EXTERNAL_TOOL_DIR = Path("external_tools") / "data_analysis"


ExternalToolCallable = Callable[[dict[str, Any]], dict[str, Any]]


EXTERNAL_DATA_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "tool_id": "scanpy_h5ad_profile_tool",
        "package_name": "scanpy",
        "local_dir": "scanpy",
        "source_url": "https://github.com/scverse/scanpy",
        "category": "transcriptome_profile",
        "callable": "run_scanpy_h5ad_profile",
    },
    {
        "tool_id": "scvi_reference_mapping_tool",
        "package_name": "scvi",
        "local_dir": "scvi-tools",
        "source_url": "https://github.com/scverse/scvi-tools",
        "category": "reference_mapping",
        "callable": "run_scvi_reference_mapping",
    },
    {
        "tool_id": "single_cell_integration_benchmark_tool",
        "package_name": "scib_metrics",
        "local_dir": "scib-metrics",
        "source_url": "https://github.com/yoseflab/scib-metrics",
        "category": "integration_quality",
        "callable": "run_single_cell_integration_benchmark",
    },
    {
        "tool_id": "celltypist_label_transfer_tool",
        "package_name": "celltypist",
        "local_dir": "celltypist",
        "source_url": "https://github.com/Teichlab/celltypist",
        "category": "annotation_validation",
        "callable": "run_celltypist_label_transfer",
    },
    {
        "tool_id": "gene_program_enrichment_tool",
        "package_name": "gseapy",
        "local_dir": "gseapy",
        "source_url": "https://github.com/zqfang/GSEApy",
        "category": "pathway_and_program_activity",
        "callable": "run_gene_program_enrichment",
    },
    {
        "tool_id": "regulatory_network_comparison_tool",
        "package_name": "pyscenic",
        "local_dir": "pyscenic",
        "source_url": "https://github.com/aertslab/pySCENIC",
        "category": "gene_regulatory_network",
        "callable": "run_regulatory_network_comparison",
    },
    {
        "tool_id": "celloracle_grn_simulation_tool",
        "package_name": "celloracle",
        "local_dir": "celloracle",
        "source_url": "https://github.com/morris-lab/CellOracle",
        "category": "gene_regulatory_network",
        "callable": "run_celloracle_grn_simulation",
    },
    {
        "tool_id": "cellrank_fate_mapping_tool",
        "package_name": "cellrank",
        "local_dir": "cellrank",
        "source_url": "https://github.com/scverse/cellrank",
        "category": "developmental_dynamics",
        "callable": "run_cellrank_fate_mapping",
    },
    {
        "tool_id": "scvelo_velocity_tool",
        "package_name": "scvelo",
        "local_dir": "scvelo",
        "source_url": "https://github.com/theislab/scvelo",
        "category": "developmental_dynamics",
        "callable": "run_scvelo_velocity",
    },
]


def get_external_data_analysis_tools(project_root: Path | None = None) -> dict[str, ExternalToolCallable]:
    return {
        "scanpy_h5ad_profile_tool": run_scanpy_h5ad_profile,
        "scvi_reference_mapping_tool": run_scvi_reference_mapping,
        "single_cell_integration_benchmark_tool": run_single_cell_integration_benchmark,
        "celltypist_label_transfer_tool": run_celltypist_label_transfer,
        "gene_program_enrichment_tool": run_gene_program_enrichment,
        "regulatory_network_comparison_tool": run_regulatory_network_comparison,
        "celloracle_grn_simulation_tool": run_celloracle_grn_simulation,
        "cellrank_fate_mapping_tool": run_cellrank_fate_mapping,
        "scvelo_velocity_tool": run_scvelo_velocity,
    }


def build_external_tool_inventory(project_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in EXTERNAL_DATA_TOOL_SPECS:
        local_path = project_root / DATA_ANALYSIS_EXTERNAL_TOOL_DIR / spec["local_dir"]
        rows.append(
            {
                **spec,
                "local_path": str(local_path),
                "source_downloaded": local_path.exists(),
                "python_dependency_available": importlib.util.find_spec(str(spec["package_name"])) is not None,
                "status": _tool_status(local_path, str(spec["package_name"])),
            }
        )
    return rows


def run_scanpy_h5ad_profile(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("scanpy_h5ad_profile_tool", config)


def run_scvi_reference_mapping(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("scvi_reference_mapping_tool", config)


def run_single_cell_integration_benchmark(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("single_cell_integration_benchmark_tool", config)


def run_celltypist_label_transfer(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("celltypist_label_transfer_tool", config)


def run_gene_program_enrichment(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("gene_program_enrichment_tool", config)


def run_regulatory_network_comparison(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("regulatory_network_comparison_tool", config)


def run_celloracle_grn_simulation(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("celloracle_grn_simulation_tool", config)


def run_cellrank_fate_mapping(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("cellrank_fate_mapping_tool", config)


def run_scvelo_velocity(config: dict[str, Any]) -> dict[str, Any]:
    return _pending_external_result("scvelo_velocity_tool", config)


def _pending_external_result(tool_id: str, config: dict[str, Any]) -> dict[str, Any]:
    project_root = Path(config.get("project_root") or Path.cwd())
    inventory = {row["tool_id"]: row for row in build_external_tool_inventory(project_root)}
    tool_inventory = inventory.get(tool_id, {})
    return {
        "tool_id": tool_id,
        "status": "callable_wrapper_ready_numeric_execution_pending",
        "source_downloaded": tool_inventory.get("source_downloaded", False),
        "python_dependency_available": tool_inventory.get("python_dependency_available", False),
        "local_path": tool_inventory.get("local_path", ""),
        "message": "Wrapper is registered. Install/runtime wiring is required before numeric execution.",
    }


def _tool_status(local_path: Path, package_name: str) -> str:
    if importlib.util.find_spec(package_name) is not None:
        return "python_dependency_available"
    if local_path.exists():
        return "source_downloaded_dependency_not_installed"
    return "source_missing"
