"""Reports bind measurements to immutable saved results instead of model-authored numbers."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from protocol_optimization_agent.runtime.archive import atomic_json, file_hash


def pointer_value(value: Any, pointer: str) -> Any:
    if pointer and not pointer.startswith("/"):
        raise ValueError("Expected a JSON pointer")
    for part in pointer.split("/")[1:]:
        if re.search(r"~(?![01])", part):
            raise ValueError("Invalid JSON pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("Invalid JSON array index")
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError("JSON pointer crosses a scalar")
    return value


def _scope(value: Any) -> tuple[set[str], set[str]]:
    objects: set[str] = set()
    metrics: set[str] = set()
    identities = {"protocol_id", "protocol_ids", "protocol_name", "protocol_id_a", "protocol_id_b", "gene", "genes",
                  "cell_type", "cell_types", "lineage", "feature", "candidate_feature", "dataset_id", "sample_id"}
    endpoints = {"endpoint", "raw_endpoint", "target_endpoint", "gene", "genes", "cell_type", "lineage", "marker_set", "gene_program"}
    def walk(item: Any):
        if isinstance(item, dict):
            for key, child in item.items():
                values = child if isinstance(child, list) else [child]
                strings = {str(v).lower() for v in values if isinstance(v, str)}
                if key in identities:
                    objects.update(strings)
                if key in endpoints:
                    metrics.update(strings)
                if isinstance(child, (float, int)) and not isinstance(child, bool):
                    metrics.add(key.lower())
                elif isinstance(child, str):
                    try:
                        if math.isfinite(float(child)):
                            metrics.add(key.lower())
                    except ValueError:
                        pass
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)
    return objects, metrics


def assess_question(plan: dict[str, Any], records: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    from protocol_optimization_agent.agents.actions import get_action
    from protocol_optimization_agent.agents.analysis_flow import inspect_result
    required = {
        "lookup": {"protocol_candidates"}, "protocol_comparison": {"feature_diff", "event_step_diff"},
        "endpoint_analysis": {"numeric_results"}, "endpoint_comparison": {"cohorts", "observations"},
        "hypothesis": {"hypotheses"}, "quality": {"dataset_summaries"}, "state": {"versioned_world_model"},
    }
    kind = plan.get("goal_type", "lookup")
    candidates, objects, metrics = [], set(), set()
    for record in records:
        if not record.get("usable") or not record.get("result_ref"):
            continue
        action = get_action(record["action_id"])
        if not required[kind].issubset(action.outputs) and not (kind == "lookup" and "semantic_steps" in action.outputs):
            continue
        result = saved_result(record["result_ref"], root)
        outputs = {"semantic_steps"} if kind == "lookup" and "semantic_steps" in action.outputs else required[kind]
        usable, _, _ = inspect_result(result, action.id, list(outputs))
        if not usable:
            continue
        preview = result.get("result_preview", {})
        if kind == "endpoint_comparison" and (not isinstance(preview, dict) or len(preview.get("cohorts", [])) < 2):
            continue
        if kind == "hypothesis" and not (result.get("hypotheses") or preview.get("hypotheses")
                or preview.get("next_experiment_candidates", {}).get("hypotheses") or preview.get("records", {}).get("hypothesis")):
            continue
        seen_objects, seen_metrics = _scope(preview)
        objects.update(seen_objects)
        metrics.update(seen_metrics)
        candidates.append(record["result_ref"]["result_id"])
    missing_objects = [obj for obj in plan.get("objects", []) if obj.lower() not in objects]
    missing_metrics = [metric for metric in plan.get("target_metrics", [])
                       if not any(metric.lower() == value or value.startswith(metric.lower() + "_") for value in metrics)]
    gaps = []
    if not candidates:
        gaps.append("no_result_answers_requested_goal:" + kind)
    gaps.extend("requested_object_unanswered:" + obj for obj in missing_objects)
    gaps.extend("requested_endpoint_unanswered:" + metric for metric in missing_metrics)
    clarification = list(plan.get("clarification_questions", []))
    if kind == "endpoint_comparison" and not plan.get("target_metrics"):
        gaps.append("evaluation_endpoint_unspecified")
        clarification.append("请指定评价终点及比较对象；不能仅凭 protocol 检索判断哪个更好。")
    incomplete_steps = [{"step_id": record["step_id"], "status": record["status"]} for record in records if not record.get("usable")]
    recorded = {record["step_id"] for record in records}
    incomplete_steps.extend({"step_id": step["id"], "status": "not_executed"}
                            for step in plan.get("steps", []) if step["id"] not in recorded)
    gaps.extend("planned_subquestion_unresolved:" + record["step_id"] for record in incomplete_steps)
    answered = not gaps and not clarification
    return {"goal_type": kind, "status": "answered" if answered else "undetermined", "answered": answered,
            "requested_objects": plan.get("objects", []), "requested_metrics": plan.get("target_metrics", []),
            "missing_objects": missing_objects, "missing_metrics": missing_metrics,
            "supporting_result_ids": candidates, "unresolved_questions": gaps,
            "clarification_questions": list(dict.fromkeys(clarification)),
            "incomplete_steps": incomplete_steps,
            "comparability": "descriptive_endpoint_checks_passed" if candidates and kind == "endpoint_comparison" else "not_a_causal_or_superiority_validation"}


def ground_conclusions(proposals: list[dict[str, Any]], records: list[dict[str, Any]], history: dict[str, Any],
                       acceptance: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    by_id = {record["result_ref"]["result_id"]: record for record in records if record.get("result_ref")}
    history_by_id = {row["id"]: row for row in [*history.get("hypotheses", []), *history.get("interpretations", [])]}
    grounded = []
    for proposal in proposals:
        conclusion = {**proposal}
        refs: dict[str, list[dict[str, Any]]] = {"supporting_refs": [], "opposing_refs": []}
        actions, objects, metrics = [], set(), set()
        for field in refs:
            for citation in proposal[field]:
                source_id = citation["source_id"]
                if citation["source_type"] == "result":
                    if source_id not in by_id:
                        raise ValueError("Conclusion cites an unknown result")
                    record = by_id[source_id]
                    if field == "supporting_refs" and not record["usable"] and proposal["evidence_level"] != "undetermined":
                        raise ValueError("Unusable evidence cannot support a positive conclusion")
                    value = saved_result(record["result_ref"], root)
                    cited_value = pointer_value(value, citation["field_path"])
                    if field == "supporting_refs":
                        actions.append(record["action_id"])
                        if not isinstance(cited_value, (dict, list)):
                            cited_value = pointer_value(value, citation["field_path"].rsplit("/", 1)[0])
                        seen_objects, seen_metrics = _scope(cited_value)
                        objects.update(seen_objects)
                        metrics.update(seen_metrics)
                    refs[field].append({**citation, "result_ref": {**record["result_ref"], "field_path": citation["field_path"]}})
                else:
                    if source_id not in history_by_id:
                        raise ValueError("Conclusion cites an unknown historical object")
                    item = history_by_id[source_id]
                    pointer_value(item, citation["field_path"])
                    refs[field].append({**citation, "version": item["version"], "path": "history_context.json",
                                        "sha256": file_hash(root / "history_context.json")})
        if proposal["evidence_level"] != "undetermined" and not refs["supporting_refs"]:
            raise ValueError("Positive conclusions require supporting references")
        if any(obj.lower() not in objects for obj in proposal["objects"]):
            raise ValueError("Conclusion objects are not supported by cited results")
        if any(metric.lower() not in metrics for metric in proposal["metrics"]):
            raise ValueError("Conclusion metrics are not supported by cited results")
        if proposal["evidence_level"] in {"fact", "descriptive"} and (
            any(action in {"hypothesis.generate", "experiment.propose", "phenotype.differential_expression"} for action in actions)
            or not actions):
            raise ValueError("Hypothesis, exploratory or historical evidence cannot be promoted to facts")
        if proposal["evidence_level"] in {"hypothesis", "exploratory"}:
            for item in history_by_id.values():
                if item.get("opposing_evidence_refs") or item.get("evidence_against"):
                    refs["opposing_refs"].append({"source_type": "history", "source_id": item["id"],
                        "field_path": "/opposing_evidence_refs" if item.get("opposing_evidence_refs") else "/evidence_against",
                        "version": item["version"], "path": "history_context.json", "sha256": file_hash(root / "history_context.json")})
                    conclusion["limitations"] = [*conclusion["limitations"], "Relevant historical counterevidence remains unresolved."]
        if not acceptance["answered"]:
            conclusion.update(evidence_level="undetermined", limitations=[*conclusion["limitations"], "Requested question is not fully answered."])
        grounded.append({**conclusion, **refs, "entailment_status": "source_bound_explanation_requires_scientific_review"})
    return grounded


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
    acceptance = assess_question(state["plan"], state.get("step_results", []), root)
    conclusions = state["plan"].get("conclusions", [])
    if not conclusions:
        ids = acceptance["supporting_result_ids"] or state["plan"].get("conclusion_refs", [])
        levels = {"lookup": "fact", "quality": "fact", "state": "fact", "hypothesis": "hypothesis",
                  "endpoint_analysis": "descriptive", "protocol_comparison": "descriptive", "endpoint_comparison": "descriptive"}
        if any(step["action_id"] == "phenotype.differential_expression" and step.get("result_ref", {}).get("result_id") in ids
               for step in state.get("step_results", []) if step.get("result_ref")):
            levels["endpoint_analysis"] = "exploratory"
        conclusions = [{"claim": "指定问题已有对应的工具证据。" if acceptance["answered"] else "当前证据尚不能完整回答指定问题。",
            "explanation": "结论依据下列已归档结果；工具执行状态与对象、终点覆盖分别检查，不推断无条件优劣或因果关系。",
            "evidence_level": levels[acceptance["goal_type"]] if acceptance["answered"] else "undetermined",
            "supporting_refs": [{"source_type": "result", "source_id": source_id, "field_path": ""} for source_id in ids],
            "opposing_refs": [], "limitations": ["这是确定性的证据覆盖说明，不是经过专家验证的机制解释。"], "objects": [], "metrics": []}]
        history = json.loads((root / "history_context.json").read_text(encoding="utf-8")) if (root / "history_context.json").exists() else {}
        conclusions = ground_conclusions(conclusions, state.get("step_results", []), history, acceptance, root)
    elif not acceptance["answered"]:
        conclusions = [{**claim, "evidence_level": "undetermined",
                        "limitations": list(dict.fromkeys([*claim["limitations"], "Requested question is not fully answered."]))}
                       for claim in conclusions]
    report = {
        "schema_version": "scientific-analysis-report-v3",
        "run_id": state["run_id"],
        "trace_id": state["trace_id"],
        "question": state["plan"].get("question", ""),
        "stop_reason": state["stop_reason"],
        "plan": state["plan"],
        "step_results": state.get("step_results", []),
        "evidence_gaps": sorted(set([*state["plan"].get("evidence_gaps", []), *acceptance["unresolved_questions"]])),
        "acceptance": acceptance,
        "conclusions": conclusions,
        "clarification_questions": acceptance["clarification_questions"],
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
    report["display_quantitative_bindings"] = relevant_bindings(report, root)
    report["complete_results"] = {"report_json": str(root / "final_report.json"),
                                  "result_refs": [step["result_ref"] for step in state.get("step_results", []) if step.get("result_ref")]}
    atomic_json(root / "final_report.json", report)
    # Read back the authority used by every presentation layer.
    return json.loads((root / "final_report.json").read_text(encoding="utf-8"))


def saved_result(ref: dict[str, Any], root: Path) -> Any:
    path = root / ref["path"]
    if not path.resolve().is_relative_to(root.resolve()) or file_hash(path) != ref["sha256"]:
        raise ValueError("Saved result reference is unauthorized or changed")
    return json.loads(path.read_text(encoding="utf-8"))


def relevant_bindings(report: dict[str, Any], root: Path, limit: int = 25) -> list[dict[str, Any]]:
    plan = report["plan"]
    targets = {value.lower() for value in [*plan.get("target_metrics", []), *plan.get("objects", [])]}
    targets.update(term.lower() for term in re.findall(r"[A-Za-z][A-Za-z0-9_:-]{1,63}", report["question"])
                   if term.lower() not in {"protocol", "protocols", "compare", "expression", "and", "the"})
    cited = {(ref["source_id"], ref["field_path"]) for claim in report["conclusions"] for ref in claim["supporting_refs"]
             if ref["source_type"] == "result"}
    ranked = []
    cache: dict[str, Any] = {}
    for binding in report["quantitative_bindings"]:
        path = binding["field_path"]
        if not path.startswith(("/result_preview/", "/phenotype_ir/")) or path.rsplit("/", 1)[-1] in {
            "sample_id", "dataset_id", "protocol_id", "version", "rank", "confidence_score", "match_score"}:
            continue
        if binding["result_id"] not in cache:
            cache[binding["result_id"]] = saved_result(binding, root)
        value = cache[binding["result_id"]]
        row_tokens: set[str] = set()
        parts = path.split("/")
        for depth in range(len(parts) - 1, 1, -1):
            parent = pointer_value(value, "/".join(parts[:depth]))
            if isinstance(parent, dict) and set(parent) & {"gene", "endpoint", "protocol_id", "metric_id", "cell_type"}:
                objects, metrics = _scope(parent)
                row_tokens = objects | metrics
                break
        score = sum(10 for token in targets if token in row_tokens) + sum(3 for token in targets if token in path.lower())
        score += 20 if (binding["result_id"], path) in cited else 0
        ranked.append((score, path, binding))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]["result_id"]))
    return [binding for _, _, binding in ranked[:limit]]


def render_report(report: dict[str, Any], root: Path) -> str:
    lines = ["# Scientific Analysis", "", "Stop reason: " + report["stop_reason"],
             "Question acceptance: " + report["acceptance"]["status"], "", "## Conclusions"]
    for claim in report["conclusions"]:
        lines.extend([f"- **{claim['evidence_level']}**: {claim['claim']}", claim["explanation"]])
        for field, label in (("supporting_refs", "Supporting evidence"), ("opposing_refs", "Opposing evidence")):
            if claim[field]:
                lines.append(label + ": " + ", ".join(f"`{ref['source_id']}{ref['field_path']}`" for ref in claim[field]))
        lines.extend("- Limitation: " + text for text in claim["limitations"])
    lines.extend(["", "## Results"])
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
    for binding in report["display_quantitative_bindings"]:
        value = pointer_value(saved_result(binding, root), binding["field_path"])
        lines.append(f"- `{binding['result_id']}{binding['field_path']}`: {value}")
    lines.append(f"Displayed {len(report['display_quantitative_bindings'])} question-selected fields; "
                 f"{len(report['quantitative_bindings'])} numeric references retained in the complete report.")
    lines.append(f"[Complete result JSON](<{report['complete_results']['report_json']}>)")
    if report["clarification_questions"]:
        lines.extend(["", "## Clarification Needed", *["- " + question for question in report["clarification_questions"]]])
    if report["evidence_gaps"]:
        lines.extend(["", "## Evidence Gaps", *["- " + str(gap) for gap in report["evidence_gaps"]]])
    lines.extend(["", "## Caveats", *["- " + text for text in report["confidence_caveats"]]])
    return "\n".join(lines)
