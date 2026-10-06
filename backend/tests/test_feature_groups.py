from __future__ import annotations

import json
from pathlib import Path

import pytest
from protocol_optimization_agent.analysis.protocol.comparison import (
    _group_protocol_feature_differences,
    _ranked_feature_differences,
)
from protocol_optimization_agent.analysis.protocol.feature_groups import (
    FEATURE_GROUP_ORDER,
    feature_group,
    feature_group_rank,
    feature_importance_rank,
)
from protocol_optimization_agent.analysis.protocol.feature_groups import feature_group as _feature_group
from protocol_optimization_agent.analysis.protocol.query_engine import (
    _feature_differences,
    _feature_importance_group,
    _group_feature_differences,
)


@pytest.mark.parametrize("feature,group", [
    ("semantic_module_exposure_days", "strategy_module"),
    ("n_strategy_modules", "strategy_module"),
    ("has_aggregation", "culture_system"),
    ("culture_duration", "culture_system"),
    ("norm_family_activin_presence", "core_signaling_family"),
    ("activin_presence", "core_signaling_family"),
    ("ordered_reagent_program", "workflow"),
    ("number_of_stages", "workflow"),
    ("stage_transition_number", "workflow"),
    ("num_days", "workflow"),
    ("requires_sorting", "workflow"),
    ("transition_count", "workflow"),
    ("exposure_concentration", "timing_or_exposure"),
    ("max_concentration", "concentration"),
    ("confidence_score", "audit_or_confidence"),
    ("unrecognized_feature", "other"),
])
def test_rule_precedence_is_shared(feature: str, group: str) -> None:
    assert _feature_group(feature) == _feature_importance_group(feature) == feature_group(feature) == group
    assert feature_importance_rank(feature) == FEATURE_GROUP_ORDER.index(group)
    assert feature_group_rank(group) == FEATURE_GROUP_ORDER.index(group)
    assert feature_group_rank("unrecognized_group") == 99


def test_frozen_rule_inventory_has_only_the_documented_pa_change() -> None:
    path = Path(__file__).resolve().parents[2] / "evals/architecture_baseline/classification_before.json"
    before = json.loads(path.read_text(encoding="utf-8"))
    changed = []
    for feature, groups in before.items():
        assert feature_group(feature) == groups["query"]
        if feature_group(feature) != groups["pa"]:
            changed.append(feature)
    assert changed == ["ordered_reagent_program"]


def test_pa_and_query_keep_their_response_contracts() -> None:
    left = {"ordered_reagent_program": "activin", "num_stages": 1, "exposure_days": 3, "max_concentration": 0.1}
    right = {"ordered_reagent_program": "chir", "num_stages": 2, "exposure_days": 7, "max_concentration": 0.05}
    pa = _ranked_feature_differences(left, right, 100)
    query = _feature_differences({k: str(v) for k, v in left.items()}, {k: str(v) for k, v in right.items()}, 100)
    assert [row["feature"] for row in pa] == [row["feature"] for row in query]
    assert all(row["feature"] != "ordered_reagent_program" for row in pa)
    assert set(pa[0]) == {"feature", "protocol_a_value", "protocol_b_value", "difference_group", "rank"}
    assert set(query[0]) == {"feature", "protocol_a_value", "protocol_b_value", "difference_type",
                             "importance_group", "importance_rank"}
    assert pa[0]["difference_group"] == query[0]["importance_group"] == "workflow"
    assert pa[1]["protocol_a_value"] == 3 and query[1]["protocol_a_value"] == "3"
    assert list(_group_protocol_feature_differences(pa)) == list(_group_feature_differences(query)) == [
        "workflow", "timing_or_exposure", "concentration",
    ]
    assert "difference_group" not in _group_protocol_feature_differences(pa)["workflow"][0]
    assert "importance_group" in _group_feature_differences(query)["workflow"][0]
