"""Reports bind measurements to immutable saved results instead of model-authored numbers."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from protocol_optimization_agent.runtime.archive import atomic_json, file_hash


def numeric_bindings(value: Any, ref: dict[str, Any], pointer: str = "") -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [
            binding
            for key, item in value.items()
            for binding in numeric_bindings(item, ref, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"))
        ]
    if isinstance(value, list):
        return [
            binding
            for index, item in enumerate(value)
            for binding in numeric_bindings(item, ref, pointer + f"/{index}")
        ]
    numeric = isinstance(value, (float, int)) and not isinstance(value, bool)
    if isinstance(value, str) and value.strip():
        try:
            numeric = math.isfinite(float(value))
        except ValueError:
            pass
    if not numeric:
        return []
    if isinstance(value, (int, float)) and not math.isfinite(value):
        raise ValueError("Nonfinite report values are not allowed")
    return [{**ref, "field_path": pointer}]


def build_final_report(state: dict[str, Any], root: Path) -> dict[str, Any]:
    sections: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in ("facts", "descriptive_differences", "exploratory_associations", "hypotheses", "undetermined")
    }
    bindings, evidence, caveats = [], [], []
    used_tools: set[str] = set()
    used_features: set[str] = set()
    for step in state.get("step_results", []):
        if not step.get("result_ref"):
            sections["undetermined"].append({"step_id": step["step_id"], "reason": step["status"]})
            continue
        ref = step["result_ref"]
        result = saved_result(ref, root)
        if step.get("executed"):
            used_tools.add(step["tool_name"])
        used_tools.update(result.get("used_tools", []))
        used_features.update(result.get("used_features", []))
        bindings.extend(numeric_bindings(result, ref))
        action = step["action_id"]
        section = (
            "hypotheses"
            if action in {"hypothesis.generate", "experiment.propose"}
            else "exploratory_associations"
            if action == "phenotype.differential_expression"
            and result.get("validation", {}).get("statistical_applicability", {}).get("differential_expression")
            == "exploratory_replicate_level"
            else "descriptive_differences"
            if action in {"protocol.compare", "protocol.phenotype_compare", "phenotype.differential_expression"}
            else "facts"
        )
        if not step["usable"]:
            section = "undetermined"
        sections[section].append(
            {
                "step_id": step["step_id"],
                "action_id": action,
                "result_ref": ref,
                "status": step["status"],
                "evidence_gaps": step.get("evidence_gaps", []),
            }
        )
        evidence.extend(result.get("evidence_refs", []))
        caveats.extend(result.get("confidence_caveats", []))
        for row in result.get("result_preview", []) if action.startswith("phenotype.") else []:
            raw = row.get("statistical_caveats", [])
            try:
                warnings = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                warnings = ["statistical_caveats_unparseable"]
            if isinstance(warnings, list):
                caveats.extend(str(warning) for warning in warnings)
        sources = {
            item.get("study_id") or item.get("paper_id") or item.get("source_doc_id")
            for item in result.get("evidence_refs", [])
            if isinstance(item, dict)
        } - {None, ""}
        if len(sources) == 1:
            caveats.append("single_study_driven:" + str(next(iter(sources))))
        elif not sources and action.startswith("phenotype."):
            caveats.append("study_identity_unknown; independent_study_support_not_established")
    report = {
        "schema_version": "scientific-analysis-report-v2",
        "run_id": state["run_id"],
        "trace_id": state["trace_id"],
        "question": state["plan"].get("question", ""),
        "stop_reason": state["stop_reason"],
        "plan": state["plan"],
        "step_results": state.get("step_results", []),
        "evidence_gaps": state["plan"].get("evidence_gaps", []),
        "budget": state["budget"],
        "sections": sections,
        "quantitative_bindings": bindings,
        "evidence_refs": evidence,
        "used_tools": sorted(used_tools),
        "used_features": sorted(used_features),
        "confidence_caveats": sorted(set(caveats)),
        "alternative_explanations": state["plan"].get("alternative_explanations", []),
        "numeric_policy": "Values loaded from saved result JSON; model explanations cannot author measurements.",
    }
    atomic_json(root / "final_report.json", report)
    # Read back the authority used by every presentation layer.
    return json.loads((root / "final_report.json").read_text(encoding="utf-8"))


def saved_result(ref: dict[str, Any], root: Path) -> Any:
    path = root / ref["path"]
    if not path.resolve().is_relative_to(root.resolve()) or file_hash(path) != ref["sha256"]:
        raise ValueError("Saved result reference is unauthorized or changed")
    return json.loads(path.read_text(encoding="utf-8"))


def render_report(report: dict[str, Any], root: Path) -> str:
    lines = ["# Scientific Analysis", "", "Stop reason: " + report["stop_reason"], "", "## Results"]
    for section, records in report["sections"].items():
        if records:
            lines.append("\n### " + section.replace("_", " ").title())
            for record in records:
                lines.append(
                    f"- {record.get('action_id', record.get('step_id'))}: {record.get('status', record.get('reason'))}"
                )
                result = saved_result(record["result_ref"], root) if record.get("result_ref") else {}
                preview = result.get("result_preview", {})
                payload = preview if isinstance(preview, dict) else {}
                view = {"summary": payload.get("answer", ""),
                        "feature_differences": payload.get("tool_result", {}).get("feature_differences", []),
                        "hypotheses": payload.get("hypotheses", payload.get("next_experiment_candidates", {}).get("hypotheses", []))}
                if view.get("summary"):
                    lines.append(str(view["summary"]))
                if view.get("feature_differences"):
                    lines.extend(["", "| Feature | Protocol A | Protocol B |", "| --- | --- | --- |"])
                    for diff in view["feature_differences"]:
                        values = [
                            str(diff[key]).replace("|", "\\|").replace("\n", " ")
                            for key in ("feature", "protocol_a_value", "protocol_b_value")
                        ]
                        lines.append("| " + " | ".join(value or "unreported" for value in values) + " |")
                for hypothesis in view.get("hypotheses", []):
                    lines.append("- Hypothesis (not validated): " + hypothesis["statement"])
    titles = {
        "protocol_analysis_agent": "Protocol Analysis Agent",
        "data_analysis_agent": "Data Analysis Agent",
        "protocol_optimizing_agent": "Protocol Optimizing Agent",
    }
    lines.extend(
        [
            "",
            "Tools: " + ", ".join(titles.get(name, name) for name in report["used_tools"]),
            "",
            "## Quantitative Results",
        ]
    )
    for binding in report["quantitative_bindings"][:25]:
        value = saved_result(binding, root)
        for key in binding["field_path"].split("/")[1:]:
            key = key.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        lines.append(f"- `{binding['result_id']}{binding['field_path']}`: {value}")
    if report["evidence_gaps"]:
        lines.extend(["", "## Evidence Gaps", *["- " + str(gap) for gap in report["evidence_gaps"]]])
    lines.extend(["", "## Caveats", *["- " + text for text in report["confidence_caveats"]]])
    return "\n".join(lines)
