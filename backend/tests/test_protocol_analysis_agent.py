from __future__ import annotations

import csv
import json
from pathlib import Path

from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.llm_client import StaticMockLLMClient


def test_protocol_analysis_agent_builds_feature_matrix_and_filters_events(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 2},
            "stages": [
                {"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1", "day_start": 0, "day_end": 3},
                {"stage_id": "s2", "stage_index": 2, "stage_name": "Stage 2", "day_start": 3, "day_end": 7},
            ],
        },
        {
            "protocol_id": "p2",
            "paper_id": "paper_b",
            "comparison_ready": False,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p2", "protocol_name": "Protocol B", "number_of_stages": 1},
            "stages": [],
        },
    ]
    events = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "dataset_id": "d1",
            "stage_id": "s1",
            "stage_index": "1",
            "day_start": "0",
            "day_end": "3",
            "duration_days": "3",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "concentration_value": "100",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "100 ng/ml Activin A",
        },
        {
            "protocol_id": "p1",
            "stage_id": "s2",
            "stage_index": "2",
            "day_start": "3",
            "day_end": "7",
            "factor_type": "reagent",
            "factor_name": "CHIR99021",
            "standardized_name": "CHIR99021",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "CHIR99021",
        },
        {
            "protocol_id": "p2",
            "stage_id": "s1",
            "factor_type": "reagent",
            "factor_name": "KGF",
            "standardized_name": "KGF",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "KGF",
        },
    ]
    sources = [
        {
            "canonical_paper_id": "paper_a",
            "canonical_source_id": "source_a",
            "canonical_filename": "paper_a.pdf",
            "source_role": "primary_article",
        }
    ]

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
        },
    ).run(records, events, sources)

    assert outputs["protocol_feature_matrix"].exists()
    assert outputs["protocol_feature_matrix_raw"].exists()
    assert outputs["protocol_candidate_feature_matrix"].exists()
    assert outputs["base_feature_registry"].exists()
    assert outputs["llm_proposed_feature_registry"].exists()
    assert outputs["approved_feature_registry"].exists()
    assert outputs["predefined_feature_registry"].exists()
    assert outputs["feature_cell_provenance"].exists()
    assert outputs["feature_discovery_audit_report"].exists()
    assert outputs["feature_spec_validation_report"].exists()
    assert outputs["schema_validation_report"].exists()
    assert outputs["normalized_factor_events"].exists()
    assert outputs["semantic_stage_annotations"].exists()
    assert outputs["protocol_strategy_summary"].exists()
    assert outputs["llm_audit_report"].exists()
    assert outputs["standardized_protocol_representations"].exists()
    assert outputs["protocol_workflow_graphs"].exists()
    assert outputs["protocol_feature_provenance"].exists()
    assert outputs["quantification_coverage_report"].exists()
    assert outputs["lossless_protocol_representation_audit"].exists()
    assert outputs["protocol_embedding"].exists()
    assert outputs["protocol_cluster_report"].exists()

    matrix_rows = list(csv.DictReader(outputs["protocol_feature_matrix"].open(encoding="utf-8")))
    assert [row["protocol_id"] for row in matrix_rows] == ["p1"]
    p1 = matrix_rows[0]
    assert p1["activin_presence"] == "1"
    assert p1["chir_presence"] == "1"
    assert p1["all_reagent_activin_a_presence"] == "1"
    assert p1["factor_type_reagent_presence"] == "1"
    assert p1["action_category_add_reagent_or_factor_presence"] == "1"
    assert p1["all_reagent_transition_activin_a_to_chir99021"] == "1"
    assert p1["action_bigram_add_reagent_to_add_reagent"] == "1"
    assert p1["graph_n_action_nodes"] == "2"
    assert p1["graph_n_next_action_edges"] == "1"
    assert p1["motif_action_add_reagent_uses_reagent_activin_a"] == "1"
    assert p1["norm_family_tgf_beta_nodal_activation_family_presence"] == "1"
    assert p1["semantic_module_definitive_endoderm_induction_presence"] == "1"

    schema_report = json.loads(outputs["schema_validation_report"].read_text(encoding="utf-8"))
    assert schema_report["summary"]["n_findings"] == 0

    quality = json.loads(outputs["protocol_quality_filter_report"].read_text(encoding="utf-8"))
    assert quality["summary"]["n_passed_quality_filter"] == 1
    assert quality["event_filter_report"]["n_kept_events"] == 2
    assert quality["event_filter_report"]["rejection_reasons"]["protocol_failed_quality_filter"] == 1
    assert quality["execution_engine"] == "langgraph"
    assert [step["node"] for step in quality["step_log"]][:3] == [
        "protocol_input_quality_gate",
        "protocol_schema_validator",
        "stage_timeline_builder",
    ]
    assert "feature_discovery" in [step["node"] for step in quality["step_log"]]

    discovery_audit = json.loads(outputs["feature_discovery_audit_report"].read_text(encoding="utf-8"))
    assert discovery_audit["schema_version"] == "feature-discovery-audit-v1"

    graphs = json.loads(outputs["protocol_workflow_graphs"].read_text(encoding="utf-8"))
    assert graphs["schema_version"] == "protocol-workflow-graph-v1"
    assert graphs["protocols"]["p1"]["summary"]["n_stages"] == 2
    assert any(edge["type"] == "next_action" for edge in graphs["protocols"]["p1"]["edges"])

    provenance = json.loads(outputs["protocol_feature_provenance"].read_text(encoding="utf-8"))
    assert provenance["protocols"]["p1"]["activin_presence"][0]["quote"] == "100 ng/ml Activin A"
    assert provenance["protocols"]["p1"]["all_reagent_activin_a_presence"][0]["quote"] == "100 ng/ml Activin A"

    coverage = json.loads(outputs["quantification_coverage_report"].read_text(encoding="utf-8"))
    assert coverage["status"] == "pass"
    assert coverage["reagent_coverage"]["missing_reagents"] == []
    assert coverage["factor_type_coverage"]["missing_factor_types"] == []
    assert coverage["action_category_coverage"]["missing_action_categories"] == []
    assert coverage["protocol_detail_domain_coverage"]["culture_parameters"]["status"] in {
        "pass",
        "not_reported_in_agent1_structured_input",
    }

    lossless_audit = json.loads(outputs["lossless_protocol_representation_audit"].read_text(encoding="utf-8"))
    assert lossless_audit["status"] == "pass"
    assert lossless_audit["summary"]["n_missing_stage_graph_nodes"] == 0
    assert lossless_audit["summary"]["n_missing_event_graph_actions"] == 0
    assert lossless_audit["summary"]["n_missing_event_feature_provenance"] == 0

    predefined = json.loads(outputs["predefined_feature_registry"].read_text(encoding="utf-8"))
    assert predefined["schema_version"] == "predefined-feature-registry-v1"
    predefined_names = {feature["feature_name"] for feature in predefined["features"]}
    assert "all_reagent_activin_a_presence" in predefined_names
    assert "factor_type_reagent_presence" in predefined_names
    assert "action_category_add_reagent_or_factor_presence" in predefined_names

    embedding_rows = list(csv.DictReader(outputs["protocol_embedding"].open(encoding="utf-8")))
    assert embedding_rows[0]["protocol_id"] == "p1"
    assert embedding_rows[0]["protocol_cluster"]

    normalized = [
        json.loads(line) for line in outputs["normalized_factor_events"].read_text(encoding="utf-8").splitlines()
    ]
    assert normalized[0]["normalized_family"] == "TGF-beta/Nodal activation family"

    strategies = json.loads(outputs["protocol_strategy_summary"].read_text(encoding="utf-8"))
    assert strategies["protocols"][0]["protocol_id"] == "p1"

    audit = json.loads(outputs["llm_audit_report"].read_text(encoding="utf-8"))
    assert "summary" in audit


def test_protocol_analysis_agent_compiles_stage_scoped_feature_specs(tmp_path: Path) -> None:
    registry = tmp_path / "run_feature_registry.json"
    registry.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "feature_name": "s1_activin_chir_same_stage",
                        "feature_type": "binary",
                        "level": "stage",
                        "biological_category": "stage_scoped_signal_combination",
                        "rationale": "Activin and CHIR are both present in stage 1.",
                        "trigger_patterns": ["Activin"],
                        "value_rule": "co_occurs_within_stage",
                        "source_fields": ["event.factor_name", "event.evidence_quote"],
                        "stage_filter": {"stage_index": 1},
                        "co_occurs_within_stage": ["Activin", "CHIR99021"],
                        "required_evidence": True,
                        "confidence": "high",
                        "proposed_by": "human",
                        "approved_status": "approved",
                    },
                    {
                        "feature_name": "chir_after_activin_count",
                        "feature_type": "count",
                        "level": "operation",
                        "biological_category": "ordered_signal_program",
                        "rationale": "Counts CHIR events that occur after Activin.",
                        "trigger_patterns": ["CHIR99021"],
                        "value_rule": "conditional_count",
                        "source_fields": ["event.factor_name", "event.evidence_quote"],
                        "after_patterns": ["Activin"],
                        "required_evidence": True,
                        "confidence": "high",
                        "proposed_by": "human",
                        "approved_status": "approved",
                    },
                    {
                        "feature_name": "activin_to_ra_gap_days",
                        "feature_type": "numeric_sum",
                        "level": "protocol",
                        "biological_category": "ordered_signal_timing",
                        "rationale": "Time from Activin exposure to RA exposure.",
                        "trigger_patterns": ["Activin"],
                        "value_rule": "duration_between_events",
                        "source_fields": ["event.factor_name", "event.evidence_quote"],
                        "before_patterns": ["Retinoic acid"],
                        "required_evidence": True,
                        "confidence": "high",
                        "proposed_by": "human",
                        "approved_status": "approved",
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 2},
            "stages": [
                {"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1", "day_start": 0, "day_end": 2},
                {"stage_id": "s2", "stage_index": 2, "stage_name": "Stage 2", "day_start": 3, "day_end": 5},
            ],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "day_start": "0",
            "day_end": "2",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Activin A and CHIR99021 were added.",
        },
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "day_start": "1",
            "day_end": "2",
            "factor_type": "reagent",
            "factor_name": "CHIR99021",
            "standardized_name": "CHIR99021",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Activin A and CHIR99021 were added.",
        },
        {
            "protocol_id": "p1",
            "stage_id": "s2",
            "stage_index": "2",
            "day_start": "3",
            "day_end": "5",
            "factor_type": "reagent",
            "factor_name": "Retinoic acid",
            "standardized_name": "Retinoic acid",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Retinoic acid was added.",
        },
    ]

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path / "out",
        quality_filters={
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
            "run_specific_feature_registry_path": str(registry),
        },
    ).run(records, events)
    row = next(csv.DictReader(outputs["protocol_feature_matrix"].open(encoding="utf-8")))

    assert row["s1_activin_chir_same_stage"] == "1"
    assert row["chir_after_activin_count"] == "1"
    assert row["activin_to_ra_gap_days"] == "3.0"


def test_protocol_analysis_agent_returns_empty_matrix_when_all_protocols_fail_quality_gate(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": False,
            "confidence": {"extraction": "low"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [{"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1"}],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Activin A was added.",
        }
    ]

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
        },
    ).run(records, events, [])

    assert list(csv.DictReader(outputs["protocol_feature_matrix"].open(encoding="utf-8"))) == []
    quality = json.loads(outputs["protocol_quality_filter_report"].read_text(encoding="utf-8"))
    assert quality["summary"]["n_passed_quality_filter"] == 0
    assert quality["event_filter_report"]["n_kept_events"] == 0


def test_protocol_analysis_agent_standardizes_concentration_units_before_max(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [{"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1"}],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "Protein X",
            "standardized_name": "Protein X",
            "concentration_value": "1",
            "concentration_unit": "mg/mL",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Protein X 1 mg/mL was added.",
        },
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "Protein X",
            "standardized_name": "Protein X",
            "concentration_value": "100",
            "concentration_unit": "ug/mL",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Protein X 100 ug/mL was added.",
        },
    ]

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
        },
    ).run(records, events, [])
    row = next(csv.DictReader(outputs["protocol_feature_matrix"].open(encoding="utf-8")))

    assert row["all_reagent_protein_x_max_concentration"] == "1000.0"
    assert row["all_reagent_protein_x_max_concentration_unit"] == "ug/ml"
    assert row["all_reagent_protein_x_concentration_comparability"] == "standardized"


def test_feature_spec_co_occurrence_requires_all_trigger_patterns(tmp_path: Path) -> None:
    registry = tmp_path / "approved_feature_registry.json"
    registry.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "feature_name": "activin_chir_co_occurrence",
                        "feature_type": "binary",
                        "level": "protocol",
                        "biological_category": "signal_combination",
                        "rationale": "Requires both Activin and CHIR.",
                        "trigger_patterns": ["Activin", "CHIR99021"],
                        "value_rule": "co_occurrence",
                        "source_fields": ["event.factor_name", "event.evidence_quote"],
                        "required_evidence": True,
                        "confidence": "high",
                        "proposed_by": "human",
                        "approved_status": "approved",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Only Activin", "number_of_stages": 1},
            "stages": [{"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1"}],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Activin A was added.",
        }
    ]

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path / "out",
        quality_filters={
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
            "approved_feature_registry_path": str(registry),
        },
    ).run(records, events, [])
    row = next(csv.DictReader(outputs["protocol_feature_matrix"].open(encoding="utf-8")))

    assert row["activin_chir_co_occurrence"] == "0"


def test_protocol_analysis_agent_accepts_mock_llm_for_unknown_factor(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [
                {"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1", "evidence_quote": "Novel factor X"}
            ],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "Novel factor X",
            "standardized_name": "Novel factor X",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "Novel factor X was added.",
        }
    ]
    llm_client = StaticMockLLMClient(
        {
            "factor_event_normalizer": {
                "normalized_name": "Novel factor X",
                "normalized_family": "Experimental signaling family",
                "factor_type": "reagent",
                "pathway": "experimental",
                "action_type": "add_reagent",
                "confidence": "medium",
            }
        }
    )

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
            "max_llm_normalization_events": 1,
        },
        llm_client=llm_client,
    ).run(records, events, [])

    normalized = [
        json.loads(line) for line in outputs["normalized_factor_events"].read_text(encoding="utf-8").splitlines()
    ]
    assert normalized[0]["normalization_source"] == "llm"
    assert normalized[0]["normalized_family"] == "Experimental signaling family"
    assert llm_client.calls[0]["task"] == "factor_event_normalizer"


def test_llm_feature_discovery_adds_candidate_column_without_approval(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [
                {
                    "stage_id": "s1",
                    "stage_index": 1,
                    "stage_name": "Stage 1",
                    "evidence_quote": "Cells underwent custom protocol step X.",
                }
            ],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "operation",
            "factor_name": "custom protocol step X",
            "standardized_name": "custom protocol step X",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "Cells underwent custom protocol step X before aggregate handling.",
        }
    ]
    llm_client = StaticMockLLMClient(
        {
            "feature_discovery": {
                "protocol_id": "p1",
                "proposed_features": [
                    {
                        "feature_name": "has_custom_llm_only_step",
                        "feature_type": "binary",
                        "level": "protocol",
                        "biological_category": "novel_strategy",
                        "rationale": "Custom step is explicitly described.",
                        "trigger_patterns": ["custom protocol step x"],
                        "value_rule": "presence",
                        "source_fields": ["evidence_quote"],
                        "required_evidence": True,
                        "confidence": "medium",
                        "proposed_by": "llm",
                    },
                ],
            }
        }
    )

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
            "max_llm_feature_discovery_protocols": 1,
        },
        llm_client=llm_client,
    ).run(records, events, [])

    final_rows = list(csv.DictReader(outputs["protocol_feature_matrix"].open(encoding="utf-8")))
    candidate_rows = list(csv.DictReader(outputs["protocol_candidate_feature_matrix"].open(encoding="utf-8")))
    assert "has_custom_llm_only_step" not in final_rows[0]
    assert candidate_rows[0]["has_custom_llm_only_step"] == "1"
    proposed = json.loads(outputs["llm_proposed_feature_registry"].read_text(encoding="utf-8"))
    assert proposed["summary"]["n_features"] == 1


def test_llm_feature_discovery_rejects_columns_without_source_evidence(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [
                {
                    "stage_id": "s1",
                    "stage_index": 1,
                    "stage_name": "Stage 1",
                    "evidence_quote": "Cells were cultured in standard medium.",
                }
            ],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "operation",
            "factor_name": "standard culture",
            "standardized_name": "standard culture",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "Cells were cultured in standard medium.",
        }
    ]
    llm_client = StaticMockLLMClient(
        {
            "feature_discovery": {
                "protocol_id": "p1",
                "proposed_features": [
                    {
                        "feature_name": "has_unsupported_llm_claim",
                        "feature_type": "binary",
                        "level": "protocol",
                        "biological_category": "unsupported",
                        "rationale": "This should be rejected because no source field contains it.",
                        "trigger_patterns": ["unsupported protocol claim"],
                        "value_rule": "presence",
                        "source_fields": ["event.evidence_quote", "stage.evidence_quote"],
                        "required_evidence": True,
                        "confidence": "medium",
                        "proposed_by": "llm",
                    },
                ],
            }
        }
    )

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
            "max_llm_feature_discovery_protocols": 1,
        },
        llm_client=llm_client,
    ).run(records, events, [])

    candidate_rows = list(csv.DictReader(outputs["protocol_candidate_feature_matrix"].open(encoding="utf-8")))
    assert "has_unsupported_llm_claim" not in candidate_rows[0]
    proposed = json.loads(outputs["llm_proposed_feature_registry"].read_text(encoding="utf-8"))
    assert proposed["summary"]["n_features"] == 1
    assert proposed["features"][0]["validation_status"] == "fail"
    audit = json.loads(outputs["feature_discovery_audit_report"].read_text(encoding="utf-8"))
    assert audit["findings"][0]["issue_type"] == "llm_feature_spec_has_no_source_evidence"


def test_llm_feature_spec_validation_executes_relation_cases_and_matrix_recomputation(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [{"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1"}],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": name,
            "standardized_name": name,
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": f"{name} was added.",
        }
        for name in ("Activin A", "CHIR99021")
    ]
    llm_client = StaticMockLLMClient(
        {
            "feature_discovery": {
                "protocol_id": "p1",
                "proposed_features": [
                    {
                        "feature_name": "llm_activin_chir_same_stage",
                        "feature_type": "binary",
                        "level": "stage",
                        "biological_category": "signal_combination",
                        "rationale": "Activin and CHIR must occur in the same stage.",
                        "trigger_patterns": ["Activin", "CHIR99021"],
                        "value_rule": "co_occurs_within_stage",
                        "source_fields": ["event.factor_name", "event.evidence_quote"],
                        "co_occurs_within_stage": ["Activin", "CHIR99021"],
                        "required_evidence": True,
                        "confidence": "medium",
                        "proposed_by": "llm",
                    },
                    {
                        "feature_name": "llm_invalid_rule",
                        "feature_type": "binary",
                        "level": "protocol",
                        "biological_category": "invalid_test",
                        "rationale": "An invalid DSL rule must fail validation without aborting the run.",
                        "trigger_patterns": ["Activin"],
                        "value_rule": "llm_decides_value",
                        "source_fields": ["event.factor_name"],
                        "required_evidence": True,
                        "confidence": "medium",
                        "proposed_by": "llm",
                    },
                ],
            }
        }
    )

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
            "max_llm_feature_discovery_protocols": 1,
        },
        llm_client=llm_client,
    ).run(records, events, [])

    report = json.loads(outputs["feature_spec_validation_report"].read_text(encoding="utf-8"))
    validation = report["features"][0]
    assert validation["status"] == "pass"
    assert validation["source_fields"] == ["event.factor_name", "event.evidence_quote"]
    assert validation["dimensions"] == {
        "source_detail_preservation": "pass",
        "quantification_coverage": "pass",
        "matrix_recomputation_consistency": "pass",
    }
    relation_cases = {case["case_id"]: case for case in validation["relation_cases"]}
    assert set(relation_cases) == {
        "controlled:only_a",
        "controlled:only_b",
        "controlled:same_stage_a_b",
        "controlled:cross_stage_a_b",
        "controlled:neither_reported",
    }
    assert relation_cases["controlled:same_stage_a_b"]["actual_value"] == 1
    assert relation_cases["controlled:cross_stage_a_b"]["actual_value"] == 0
    assert all(case["passed"] for case in relation_cases.values())
    assert validation["controlled_negative_case"]["passed"] is True
    assert "does not prove biological absence" in validation["missing_value_policy"]
    invalid = report["features"][1]
    assert invalid["status"] == "fail"
    assert "not executable" in invalid["execution_errors"][0]
    candidate_row = next(csv.DictReader(outputs["protocol_candidate_feature_matrix"].open(encoding="utf-8")))
    assert "llm_invalid_rule" not in candidate_row


def test_approved_feature_specs_are_compiled_by_explicit_dsl(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_feature_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "feature-registry-v1",
                "features": [
                    {
                        "feature_name": "custom_activin_max_concentration",
                        "feature_type": "numeric_max",
                        "level": "protocol",
                        "biological_category": "reagent_dose",
                        "rationale": "Maximum concentration should be calculated from structured events.",
                        "trigger_patterns": ["activin"],
                        "value_rule": "max(concentration_value)",
                        "source_fields": ["event.normalized_name", "event.normalized_family", "event.evidence_quote"],
                        "required_evidence": True,
                        "confidence": "high",
                        "proposed_by": "human",
                        "approved_status": "approved",
                    },
                    {
                        "feature_name": "custom_activin_factor_types",
                        "feature_type": "categorical",
                        "level": "protocol",
                        "biological_category": "factor_category",
                        "rationale": "Factor types should be emitted as a category set.",
                        "trigger_patterns": ["activin"],
                        "value_rule": "category_set(factor_type)",
                        "source_fields": ["event.normalized_name", "event.normalized_family", "event.evidence_quote"],
                        "required_evidence": True,
                        "confidence": "high",
                        "proposed_by": "human",
                        "approved_status": "approved",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [
                {"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1", "evidence_quote": "Activin A was used."}
            ],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "day_start": "0",
            "day_end": "1",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "concentration_value": "100",
            "concentration_unit": "ng/ml",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Activin A 100 ng/ml was used.",
        },
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "day_start": "1",
            "day_end": "2",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "concentration_value": "50",
            "concentration_unit": "ng/ml",
            "validation_status": "evidence_supported",
            "parse_status": "single_value",
            "concentration_parse_status": "single_value",
            "evidence_quote": "Activin A 50 ng/ml was used.",
        },
    ]

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
            "approved_feature_registry_path": str(registry_path),
        },
    ).run(records, events, [])

    matrix_rows = list(csv.DictReader(outputs["protocol_feature_matrix"].open(encoding="utf-8")))
    assert float(matrix_rows[0]["custom_activin_max_concentration"]) == 0.1
    assert matrix_rows[0]["custom_activin_factor_types"] == "reagent"
    provenance = json.loads(outputs["feature_cell_provenance"].read_text(encoding="utf-8"))
    cell = provenance["final_matrix"]["protocols"]["p1"]["custom_activin_max_concentration"]
    assert cell["n_provenance_refs"] == 2


def test_protocol_analysis_agent_does_not_reagent_normalize_non_reagent_from_quote(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [
                {"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1", "evidence_quote": "Activin was added."}
            ],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "qc_marker",
            "factor_name": "qPCR for SOX17",
            "standardized_name": "qPCR for SOX17",
            "value_raw": "Activin was added before qPCR.",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "Activin was added before qPCR.",
        }
    ]

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
        },
    ).run(records, events, [])

    normalized = [
        json.loads(line) for line in outputs["normalized_factor_events"].read_text(encoding="utf-8").splitlines()
    ]
    assert normalized[0]["factor_type"] == "qc_marker"
    assert normalized[0]["normalized_family"] == "qc_marker"


def test_protocol_query_layer_compares_generated_outputs(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [
                {
                    "stage_id": "s1",
                    "stage_index": 1,
                    "stage_name": "DE",
                    "day_start": 0,
                    "day_end": 3,
                    "evidence_quote": "Activin A",
                }
            ],
        },
        {
            "protocol_id": "p2",
            "paper_id": "paper_b",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p2", "protocol_name": "Protocol B", "number_of_stages": 1},
            "stages": [
                {
                    "stage_id": "s2",
                    "stage_index": 1,
                    "stage_name": "Maturation",
                    "day_start": 10,
                    "day_end": 20,
                    "evidence_quote": "T3",
                }
            ],
        },
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "Activin A was added.",
        },
        {
            "protocol_id": "p2",
            "stage_id": "s2",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "T3",
            "standardized_name": "T3",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "T3 was added.",
        },
    ]
    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
        },
    ).run(records, events, [])

    engine = ProtocolQueryEngine(outputs["protocol_feature_matrix"].parent)
    assert engine.find_protocols("Protocol A")[0]["protocol_id"] == "p1"
    comparison = engine.compare_protocols("p1", "p2")
    assert comparison["status"] == "ok"
    assert "grouped_feature_differences" in comparison
    assert "TGF-beta/Nodal activation family" in comparison["factor_diff"]["protocol_a_specific_normalized_families"]
    assert "Beta-cell maturation support family" in comparison["factor_diff"]["protocol_b_specific_normalized_families"]


def test_schema_invalid_key_factor_is_rejected_by_default(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [{"stage_id": "s1", "stage_index": 1, "stage_name": "Stage 1", "day_start": 0, "day_end": 1}],
        }
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "",
        }
    ]

    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
        },
    ).run(records, events, [])

    schema = json.loads(outputs["schema_validation_report"].read_text(encoding="utf-8"))
    assert schema["summary"]["n_rejected_factor_events"] == 1
    normalized = outputs["normalized_factor_events"].read_text(encoding="utf-8").splitlines()
    assert normalized == []


def test_protocol_query_layer_finds_author_year_with_normalized_tokens(tmp_path: Path) -> None:
    matrix_path = tmp_path / "protocol_feature_matrix.csv"
    matrix_path.write_text(
        "protocol_id,paper_id,dataset_id,protocol_name,confidence_score,comparison_ready\n"
        "p1,akgün_canan_2024,GSE1,Example protocol,0.9,True\n",
        encoding="utf-8",
    )

    hits = ProtocolQueryEngine(tmp_path).find_protocols("Akgün 2024")
    assert hits[0]["protocol_id"] == "p1"
    assert hits[0]["matched_tokens"] == ["2024", "akgun"]


def test_protocol_query_layer_answers_collection_count_without_text_search(tmp_path: Path) -> None:
    matrix_path = tmp_path / "protocol_feature_matrix.csv"
    matrix_path.write_text(
        "protocol_id,paper_id,dataset_id,protocol_name,confidence_score,comparison_ready,activin_presence\n"
        "p1,paper_a,GSE1,Protocol A,0.9,True,1\n"
        "p2,paper_b,GSE2,Protocol B,0.8,True,0\n"
        "p3,paper_c,GSE3,No matching English keyword,0.7,False,1\n",
        encoding="utf-8",
    )

    result = ProtocolQueryEngine(tmp_path).answer_question("一共有多少protocols可供比较")

    assert result["intent"] == "protocol_collection_summary"
    assert result["tool_result"]["n_protocols"] == 3
    assert result["tool_result"]["n_comparison_ready_protocols"] == 2
    assert "3 protocols" in result["answer"]


def test_protocol_query_layer_generates_feature_heatmap(tmp_path: Path) -> None:
    matrix_path = tmp_path / "protocol_feature_matrix.csv"
    matrix_path.write_text(
        "protocol_id,paper_id,dataset_id,protocol_name,confidence_score,comparison_ready,activin_presence,t3_presence,total_days\n"
        "p1,paper_a,d1,Protocol A,0.9,True,1,0,7\n"
        "p2,paper_b,d2,Protocol B,0.9,True,0,1,20\n",
        encoding="utf-8",
    )

    result = ProtocolQueryEngine(tmp_path).plot_feature_heatmap(
        top_n_features=3,
        filename="test_heatmap.svg",
        cluster_rows=True,
        cluster_features=True,
    )

    assert result["status"] == "ok"
    assert result["n_protocols"] == 2
    assert result["n_features"] == 3
    assert result["cluster_rows"] is True
    assert result["cluster_features"] is True
    output_path = Path(result["path"])
    assert output_path.exists()
    assert "<svg" in output_path.read_text(encoding="utf-8")


def test_protocol_query_layer_generates_layered_feature_heatmaps(tmp_path: Path) -> None:
    matrix_path = tmp_path / "protocol_feature_matrix.csv"
    matrix_path.write_text(
        "protocol_id,paper_id,dataset_id,protocol_name,confidence_score,comparison_ready,"
        "semantic_module_beta_cell_maturation_presence,has_aggregation,activin_presence,"
        "norm_family_wnt_activation_family_presence,total_days,transition_activin_to_chir,"
        "action_category_wash_presence,factor_type_reagent_presence\n"
        "p1,paper_a,d1,Protocol A,0.9,True,1,1,1,1,7,1,1,1\n"
        "p2,paper_b,d2,Protocol B,0.9,True,0,0,0,0,20,0,1,1\n",
        encoding="utf-8",
    )

    result = ProtocolQueryEngine(tmp_path).plot_layered_feature_heatmaps(
        top_n_features_per_layer=2,
        include_sequence_layer=True,
        filename_prefix="layered_test",
    )

    assert result["status"] == "ok"
    assert result["n_layers"] == 8
    assert Path(result["index_path"]).exists()
    assert "Layered protocol feature heatmaps" in Path(result["index_path"]).read_text(encoding="utf-8")
