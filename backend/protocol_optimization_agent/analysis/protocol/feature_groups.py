"""Shared importance groups for PA reports and protocol queries, not heatmap layers."""

FEATURE_GROUP_ORDER = (
    "strategy_module",
    "culture_system",
    "core_signaling_family",
    "workflow",
    "timing_or_exposure",
    "concentration",
    "audit_or_confidence",
    "other",
)
_GROUP_RANKS = {group: rank for rank, group in enumerate(FEATURE_GROUP_ORDER)}


def feature_group(feature: str) -> str:
    if feature.startswith("semantic_module_") or feature == "n_strategy_modules":
        return "strategy_module"
    if (
        feature in {"has_2d_adherent_culture", "has_suspension_culture", "has_shaking", "has_aggregation"}
        or "culture" in feature
    ):
        return "culture_system"
    if feature.startswith("norm_family_") or feature.endswith("_presence"):
        return "core_signaling_family"
    if feature.startswith(("num_", "requires_", "transition_")) or feature in {
        "ordered_reagent_program",
        "number_of_stages",
        "stage_transition_number",
    }:
        return "workflow"
    if any(token in feature for token in ("day", "duration", "exposure")):
        return "timing_or_exposure"
    if "concentration" in feature:
        return "concentration"
    if any(token in feature for token in ("confidence", "fallback", "schema", "audit")):
        return "audit_or_confidence"
    return "other"


def feature_group_rank(group: str) -> int:
    return _GROUP_RANKS.get(group, 99)


def feature_importance_rank(feature: str) -> int:
    return feature_group_rank(feature_group(feature))
