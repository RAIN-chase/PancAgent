"""Sample-preserving PA/DA evidence, endpoint cohorts and identity boundaries."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from protocol_optimization_agent.analysis.optimization._shared import _mean
from protocol_optimization_agent.analysis.optimization._values import _known, _number
from protocol_optimization_agent.analysis.optimization.comparability import scientific_preconditions
from protocol_optimization_agent.analysis.optimization.contracts import ProtocolOptimizingState
from protocol_optimization_agent.science.identity import stable_id


def comparison_phenotypes(phenotypes: list[dict[str, Any]], endpoints: list[str]) -> list[dict[str, Any]]:
    """Resolve family-qualified targets without changing the source Phenotype IR."""
    if not endpoints:
        return phenotypes
    selected = []
    for phenotype in phenotypes:
        metrics = []
        for metric in phenotype["metrics"]:
            matches = [endpoint for endpoint in endpoints if endpoint in {
                metric["endpoint"], f"{metric['family']}:{metric['endpoint']}"}]
            if len(matches) > 1:
                raise ValueError("A metric cannot be requested twice through overlapping raw and qualified endpoint names.")
            if matches:
                metrics.append({**metric, "endpoint": matches[0]})
        selected.append({**phenotype, "metrics": metrics})
    return selected


def endpoint_preconditions(protocols: list[dict[str, Any]], phenotypes: list[dict[str, Any]],
                           comparison: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id = {row["protocol_id"]: row for row in protocols}
    identity_filtered = [{**phenotype, "metrics": [metric for metric in phenotype["metrics"]
        if _identity(by_id.get(phenotype["protocol_id"]), phenotype,
            {**phenotype.get("metadata", {}), **metric.get("context", {})})["status"] not in {
                "version_mismatch", "protocol_not_found", "sample_identity_missing"}]}
        for phenotype in phenotypes]
    return {endpoint: scientific_preconditions(protocols, comparison_phenotypes(identity_filtered, [endpoint]),
                comparison={**comparison, "endpoints": [endpoint]})
            for endpoint in comparison.get("endpoints", [])}


def _identity(protocol: dict[str, Any] | None, phenotype: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    snapshot = stable_id("protocol_snapshot", protocol) if protocol else ""
    declared_snapshot = context.get("protocol_snapshot_id")
    declared_version = context.get("protocol_version")
    expected_version = (protocol or {}).get("protocol_version")
    status = "protocol_id_only_version_unverified"
    if not protocol:
        status = "protocol_not_found"
    elif not phenotype.get("sample_id"):
        status = "sample_identity_missing"
    elif _known(declared_snapshot):
        status = "version_verified" if declared_snapshot == snapshot else "version_mismatch"
        if _known(declared_version) and _known(expected_version) and str(declared_version) != str(expected_version):
            status = "version_mismatch"
    elif _known(declared_version) and _known(expected_version):
        status = "version_verified" if str(declared_version) == str(expected_version) else "version_mismatch"
    return {"status": status, "pa_snapshot_id": snapshot,
            "pa_declared_version": expected_version or "unknown",
            "da_declared_version": declared_version or "unknown",
            "da_declared_snapshot_id": declared_snapshot or "unknown"}


def build_joint_evidence(state: ProtocolOptimizingState) -> dict[str, Any]:
    dataset = state["analysis_dataset"]
    protocols = {row["protocol_id"]: row for row in dataset["protocol_rows"]}
    endpoint_gates = state.get("endpoint_preconditions", {})
    observations = []
    for p_index, phenotype in enumerate(dataset["phenotypes"]):
        for m_index, metric in enumerate(phenotype["metrics"]):
            context = {**phenotype.get("metadata", {}), **metric.get("context", {})}
            identity = _identity(protocols.get(phenotype["protocol_id"]), phenotype, context)
            qualified = f"{metric['family']}:{metric['endpoint']}"
            endpoint = qualified if qualified in endpoint_gates else metric["endpoint"]
            gate = endpoint_gates.get(endpoint, {})
            eligible = (gate.get("scientific_ready", False) and
                metric["id"] in gate.get("cohort", {}).get("included_metric_ids", []) and
                identity["status"] not in {"version_mismatch", "protocol_not_found", "sample_identity_missing"})
            observations.append({
                "observation_id": stable_id("joint_observation", [dataset["id"], phenotype["id"], metric["id"]]),
                "analysis_dataset_id": dataset["id"], "phenotype_id": phenotype["id"], "metric_id": metric["id"],
                "protocol_id": phenotype["protocol_id"], "dataset_id": phenotype["dataset_id"],
                "sample_id": phenotype.get("sample_id", ""), "endpoint": endpoint,
                "raw_endpoint": metric["endpoint"], "endpoint_family": metric["family"],
                "raw_value": metric["value"], "unit": metric["unit"], "method": metric["method"],
                "context": context, "identity_link": identity, "association_eligible": bool(eligible),
                "uncertainty": {key: context.get(key, "unknown") for key in (
                    "standard_error", "confidence_interval", "n_cells", "biological_replicate_id")},
                "result_ref": {"artifact": "analysis_dataset.json", "field_path": f"/phenotypes/{p_index}/metrics/{m_index}/value",
                               "input_version": dataset["source_fingerprint"]},
                "evidence_refs": metric["evidence_refs"],
                "exclusion_reasons": [] if eligible else [
                    "identity_or_endpoint_comparability_unresolved", *[check["name"] for check in gate.get("checks", [])
                        if check["status"] not in {"matched", "passed"}]],
            })
    identities = Counter(row["identity_link"]["status"] for row in observations)
    target_observations = [row for row in observations if row["endpoint"] in endpoint_gates]
    package = {
        "schema_version": "joint-protocol-phenotype-evidence-v1", "analysis_dataset_id": dataset["id"],
        "input_version": dataset["source_fingerprint"], "objective": state.get("objective", {}),
        "protocol_snapshots": [{"protocol_id": pid, "snapshot_id": stable_id("protocol_snapshot", row), "fields": row}
                               for pid, row in sorted(protocols.items())],
        "interventions": state.get("stage_event_rows", []),
        "step_annotations": state.get("step_annotations", []),
        "observations": observations, "endpoint_comparisons": endpoint_gates,
        "identity_summary": dict(identities),
        "identity_versions_verified": bool(target_observations) and all(
            row["identity_link"]["status"] == "version_verified" for row in target_observations),
        "limitations": ["A protocol_id join alone does not verify the executed protocol version.",
            "Cell counts are not independent biological replicates.",
            "Protocol use is not mechanism evidence; observational associations are not intervention effects."],
    }
    package["id"] = stable_id("joint_evidence", package)
    return package


def summarize_endpoints(package: dict[str, Any]) -> list[dict[str, Any]]:
    # Average repeated measurements within a biological replicate before aggregating protocols.
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in package["observations"]:
        if row["association_eligible"] and _number(row["raw_value"]) is not None:
            study_value = row["context"].get("study_id")
            study = str(study_value) if _known(study_value) else "unknown"
            groups[(row["protocol_id"], row["endpoint"], study)].append(row)
    results = []
    for (pid, endpoint, study), rows in sorted(groups.items()):
        replicates: dict[tuple[str, str], list[float]] = defaultdict(list)
        for row in rows:
            context = row["context"]
            # Unknown studies cannot identify shared biological samples across datasets.
            scope = study if study != "unknown" else row["dataset_id"]
            replicate = str(context.get("biological_replicate_id") or "")
            if not replicate:
                continue
            replicates[(scope, replicate)].append(float(row["raw_value"]))
        means = [mean for values in replicates.values() if (mean := _mean(values)) is not None]
        results.append({"protocol_id": pid, "endpoint": endpoint, "study_id": study,
            "raw_mean": _mean(means), "unit": rows[0]["unit"], "method": rows[0]["method"],
            "n_biological_replicates": len(replicates),
            "n_samples": len({(row["dataset_id"], row["sample_id"]) for row in rows}),
            "replicate_range": [min(means), max(means)] if means else [],
            "uncertainty_policy": "observed_replicate_range_not_confidence_interval",
            "metric_ids": [row["metric_id"] for row in rows],
            "observation_ids": [row["observation_id"] for row in rows],
            "aggregation": "equal_replicate_weight_within_study; equal_study_weight_for_protocol_screen"})
    return results
