"""Method provenance is metadata, not a second capability registry or paper-reproduction claim."""
from __future__ import annotations

import hashlib
import inspect
import platform
from importlib.metadata import version


def maturity_method_package(score_function, parameters: dict) -> dict:
    try:
        source = inspect.getsource(score_function)
    except (OSError, TypeError):
        source = ""
    return {"schema_version": "method-package-v1", "action_id": "phenotype.beta_maturity",
            "primitive": "scanpy.tl.score_genes", "implementation_version": version("scanpy"),
            "implementation_source_sha256": hashlib.sha256(source.encode()).hexdigest() if source else None,
            "implementation_commit": "not_available_in_installed_distribution; source_hash_recorded_instead",
            "documentation": "https://scanpy.readthedocs.io/en/stable/generated/scanpy.tl.score_genes.html",
            "method_reference": {"authors": "Tirosh et al.", "year": 2016,
                "title": "Dissecting the multicellular ecosystem of metastatic melanoma by single-cell RNA-seq",
                "scope": "gene-module scoring primitive, not organoid maturity validation"},
            "environment": {"python": platform.python_version(), "scanpy": version("scanpy"),
                            "anndata": version("anndata"), "numpy": version("numpy"), "scipy": version("scipy")},
            "parameters": {key: parameters.get(key) for key in ("target_sum", "n_bins", "ctrl_size", "random_state")},
            "fixed_parameters": {"ctrl_as_ref": True, "use_raw": False},
            "reference_tests": ["test_maturity_scoring.py::test_matches_notebook_kernel", "test_research_quality.py::test_module_score_independent_analytic_fixture"],
            "acceptance": {"cell_score_atol": 1e-7, "finite_values_required": True, "gene_program_hashes_required": True},
            "applicability": ["declared counts layer", "target-cell annotation", "adequate program/control gene coverage"],
            "input_contract": {"expression": "nonnegative finite raw counts", "programs": "versioned Early/Late CSV per selected cell type",
                               "unknown_scale_policy": "reject; do not infer counts from expression appearance"},
            "output_contract": {"early_score": "module score", "late_score": "module score",
                                "maturity_score": "late_score - early_score", "unit": "relative module-score difference; not age or probability"},
            "failure_cases": ["missing counts layer", "nonfinite or negative counts", "missing cell annotation",
                              "insufficient program/control genes", "missing scanpy dependency"],
            "validation_layers": {"implementation_regression": "notebook kernel",
                                  "independent_reference": "analytical module-score fixture",
                                  "hidden_benchmark": "not_performed", "expert_biological_review": "not_performed"},
            "status": "implementation_regression_and_analytic_primitive_checks_only",
            "paper_reference_data_reproduction": "not_performed", "organoid_maturity_validation": "not_established_by_the_scoring_paper"}
