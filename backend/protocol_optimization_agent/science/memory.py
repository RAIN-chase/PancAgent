"""Scoped conversation summaries and source-bound memory; never a belief promotion engine."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode
from protocol_optimization_agent.runtime.archive import atomic_json, file_hash
from protocol_optimization_agent.science.identity import stable_id
from protocol_optimization_agent.science.models import Evidence, ScientificInterpretation, ScientificRecord
from protocol_optimization_agent.science.reporting import pointer_value, saved_result

if TYPE_CHECKING:
    from protocol_optimization_agent.science.state import ScientificWorldModel

ALIASES = (("maturity", "成熟度"), ("composition", "细胞组成"), ("expression", "表达量", "基因表达"),
           ("stress", "应激"), ("beta", "β细胞", "beta cell"))
FILTERS = {"protocol_ids", "endpoints", "stages", "protocol_versions", "hypothesis_versions"}


def checked_lists(value: dict[str, Any], allowed: set[str]) -> dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Undeclared memory fields")
    for items in value.values():
        if not isinstance(items, list) or len(items) > 100 or any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in items):
            raise ValueError("Memory fields require bounded nonempty string lists")
    return value


def _endpoint(value: str) -> str:
    return next((group[0] for group in ALIASES if value.casefold() in group), value)


def retrieve_records(db: sqlite3.Connection, kind: str, question: str, filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    checked_lists(filters, FILTERS)
    terms = set(re.findall(r"\bprotocol\s+[A-Za-z0-9_.-]+\b|[A-Za-z][A-Za-z0-9_.-]{1,63}|[\u4e00-\u9fff]{2,16}", question.casefold()))
    terms -= {"protocol", "protocols", "the", "and", "which", "better", "compare", "find"}
    for group in ALIASES:
        if any(term in question.casefold() for term in group):
            terms.update(group)
    ranked = []
    # ponytail: project-scale SQLite rows; add normalized indexed fields if corpus size makes scans costly.
    for (encoded,) in db.execute("SELECT payload FROM records WHERE kind=?", (kind,)):
        row = json.loads(encoded)
        spec = row.get("test_spec") or row.get("scope", {})
        fields = {
            "protocol_ids": [*row.get("protocol_ids", []), *spec.get("protocol_ids", []), spec.get("baseline_protocol_id", "")],
            "endpoints": spec.get("endpoints", []) or [row.get("objective", "")],
            "stages": spec.get("stages", []) or [spec.get("location", {}).get("target_stage", spec.get("context", {}).get("stage", ""))],
            "protocol_versions": spec.get("protocol_versions", []) or [spec.get("baseline_protocol_version", "")],
            "hypothesis_versions": [str(row["version"])],
        }
        matches, compatible = 0, True
        for name, requested in filters.items():
            if not requested:
                continue
            actual = {str(item) for item in fields[name]}
            wanted = set(requested)
            exact = bool(wanted & actual)
            equivalent = name == "endpoints" and bool({_endpoint(item) for item in wanted} & {_endpoint(item) for item in actual})
            if not exact and not equivalent:
                compatible = False
                break
            matches += 2 if exact else 1
        text = " ".join(str(row.get(key, "")) for key in ("statement", "claim", "feature", "objective", "explanation")).casefold()
        words = sum(bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text)) for term in terms)
        if compatible and (matches or words):
            ranked.append((-matches, -words, row["id"], row))
    selected = [item[-1] for item in sorted(ranked, key=lambda item: item[:3])[:limit]]
    for row in selected:
        for direction, names in (("supporting", ("evidence_for", "rationale_evidence")), ("opposing", ("evidence_against",))):
            ids = list(dict.fromkeys(eid for name in names for eid in row.get(name, [])))
            refs = row.get(direction + "_evidence_refs", [])
            ids.extend(ref["evidence_id"] for ref in refs if ref.get("evidence_id") and ref["evidence_id"] not in ids)
            evidence: list[dict[str, Any]] = []
            missing: list[str] = []
            for eid in ids:
                found = db.execute("SELECT payload FROM records WHERE kind='evidence' AND id=?", (eid,)).fetchone()
                if found:
                    evidence.append(json.loads(found[0]))
                else:
                    missing.append(eid)
            row[direction + "_evidence"] = evidence
            row[direction + "_missing_evidence_ids"] = missing
    return selected


class ConversationMemory:
    def __init__(self, store: ScientificWorldModel):
        self.store = store
        with store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS conversations (thread_id TEXT PRIMARY KEY, version INTEGER, summary TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS conversation_revisions (thread_id TEXT, version INTEGER, run_id TEXT, summary TEXT, PRIMARY KEY(thread_id,version))")
            db.execute("CREATE TABLE IF NOT EXISTS conversation_messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, message_key TEXT, run_id TEXT, payload TEXT, UNIQUE(thread_id,message_key))")

    def restore(self, thread_id: str) -> dict[str, Any]:
        if thread_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_:.-]{0,199}", thread_id):
            raise ValueError("Invalid conversation thread identifier")
        with self.store._connect() as db:
            row = db.execute("SELECT summary FROM conversations WHERE thread_id=?", (thread_id,)).fetchone() if thread_id else None
        return json.loads(row[0]) if row else {"schema_version": "conversation-summary-v1", "version": 0,
            "protocol_ids": [], "endpoints": [], "stages": [], "protocol_versions": [], "user_constraints": [],
            "pending_questions": [], "result_refs": [], "opposing_refs": [], "constraint_sources": []}

    def capture(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        if not thread_id:
            return
        self.restore(thread_id)
        self._write_permission()
        with self.store._connect() as db:
            for message in messages:
                db.execute("INSERT OR IGNORE INTO conversation_messages(thread_id,message_key,run_id,payload) VALUES (?,?,?,?)",
                    (thread_id, stable_id("message", message), self.store.context.run_id, json.dumps(message, ensure_ascii=False)))

    def save(self, thread_id: str, summary: dict[str, Any], expected_version: int) -> dict[str, Any]:
        if not thread_id:
            return summary
        self.restore(thread_id)
        self._write_permission()
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM conversations WHERE thread_id=?", (thread_id,)).fetchone()
            if (row[0] if row else 0) != expected_version:
                raise ValueError("Conversation changed concurrently; reload before updating memory")
            summary = {**summary, "version": expected_version + 1}
            encoded = json.dumps(summary, ensure_ascii=False, allow_nan=False)
            db.execute("INSERT OR REPLACE INTO conversations VALUES (?,?,?)", (thread_id, summary["version"], encoded))
            db.execute("INSERT INTO conversation_revisions VALUES (?,?,?,?)", (thread_id, summary["version"], self.store.context.run_id, encoded))
        return summary

    def _write_permission(self) -> None:
        if authenticated_mode():
            AccessPolicy().require(self.store.context.user_id, self.store.project_id, "write")


def update_summary(previous: dict[str, Any], question: str, update: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    checked_lists(update, (FILTERS - {"hypothesis_versions"}) | {"user_constraints", "remove_constraints", "resolved_questions"})
    summary = json.loads(json.dumps(previous))
    for field in FILTERS - {"hypothesis_versions"}:
        if field in update:
            summary[field] = list(dict.fromkeys(update[field]))
    if re.search(r"换成|改为|switch|change.*to", question, re.I):
        for group in ALIASES[:4]:
            if any(term in question.casefold() for term in group):
                summary["endpoints"] = [group[0]]
                break
    constraints = list(summary["user_constraints"])
    for message in messages:
        text = message.get("content", "")
        if message.get("type") == "human" and isinstance(text, str) and re.search(r"不要|不得|必须|仅|只[看用选比]|只能|保留|禁止|不允许|不使用|排除|\b(?:only|must|avoid|never|do not|keep|exclude)\b", text, re.I):
            source = {"constraint": text, "message_key": stable_id("message", message)}
            if source not in summary["constraint_sources"]:
                constraints.append(text)
                summary["constraint_sources"].append(source)
    constraints.extend(update.get("user_constraints", []))
    summary["user_constraints"] = list(dict.fromkeys(item for item in constraints if item not in update.get("remove_constraints", [])))
    summary["pending_questions"] = [item for item in summary["pending_questions"] if item not in update.get("resolved_questions", [])]
    return summary


def _context_preview(value: Any, depth: int = 0) -> Any:
    if isinstance(value, dict):
        if depth >= 5:
            return {"omitted_for_context": True}
        protected = {"confidence_caveats", "evidence_gaps", "limitations", "result_preview", "result_refs",
                     "status", "protocol_id", "protocol_id_a", "protocol_id_b", "gene", "endpoint", "unit"}
        keys = list(dict.fromkeys([*[key for key in value if key in protected or key.startswith("opposing")], *list(value)[:12]]))
        result = {key: value[key] if key in {"confidence_caveats", "evidence_gaps", "limitations"} or key.startswith("opposing")
                  else _context_preview(value[key], depth + 1) for key in keys}
        if len(keys) < len(value):
            result["omitted_fields_for_context"] = len(value) - len(keys)
        return result
    if isinstance(value, list):
        return [_context_preview(child, depth + 1) for child in value[:6]] if depth < 7 else {"omitted_for_context": True}
    if isinstance(value, str) and len(value) > 600:
        return value[:600] + " [omitted remainder; consult source]"
    return value


def context_messages(messages: list[Any], max_chars: int) -> list[Any]:
    groups: list[list[Any]] = []
    for message in messages:
        if getattr(message, "type", "") == "tool":
            try:
                value = json.loads(message.content)
                if not isinstance(value, dict):
                    raise ValueError("Unstructured tool result cannot be compacted")
                view = {**_context_preview(value), "result_refs": value.get("result_refs", []),
                        "context_view": "Bounded preview, not complete evidence; use original saved JSON field paths. Omitted content remains unknown."}
                message = message.model_copy(update={"content": json.dumps(view, ensure_ascii=False)})
            except (ValueError, TypeError):
                pass
        if getattr(message, "type", "") == "human" or not groups:
            groups.append([])
        groups[-1].append(message)
    selected: list[Any] = []
    used = 0
    for group in reversed(groups[-4:]):
        size = sum(len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False)) for item in group)
        if used + size > max_chars:
            if not selected:
                raise ValueError("Current turn exceeds model context budget")
            break
        selected = group + selected
        used += size
    return selected


def scoped_messages(messages: list[Any], reset: bool) -> list[Any]:
    if reset:
        start = next((index for index in range(len(messages) - 1, -1, -1) if getattr(messages[index], "type", "") == "human"), len(messages))
        return messages[start:]
    return messages


def prompt_summary(summary: dict[str, Any]) -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    for source in summary.get("constraint_sources", []):
        if source["constraint"] in summary.get("user_constraints", []):
            sources.setdefault(source["constraint"], source)
    return {**summary, "constraint_sources": list(sources.values()),
            "result_refs": summary.get("result_refs", [])[-8:],
            "result_ref_count": len(summary.get("result_refs", [])),
            "context_view": "Recent result references only; full summary and raw sources remain archived."}


def _protocol_ids(value: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"protocol_id", "protocol_id_a", "protocol_id_b"} and isinstance(child, str):
                ids.append(child)
            elif key == "protocol_ids" and isinstance(child, list):
                ids.extend(item for item in child if isinstance(item, str))
            ids.extend(_protocol_ids(child))
    elif isinstance(value, list):
        for child in value:
            ids.extend(_protocol_ids(child))
    return list(dict.fromkeys(ids))


def remember_report(store: ScientificWorldModel, report: dict[str, Any], root: Path) -> dict[str, str]:
    if root.resolve() != store.context.run_root().resolve() or report["run_id"] != store.context.run_id:
        raise PermissionError("Run memory must belong to the current user/project context")
    evidence_ids: dict[str, str] = {}
    records: list[ScientificRecord] = []
    for step in report["step_results"]:
        if not step.get("usable") or not step.get("result_ref") or not step["action_id"].startswith(("protocol.", "phenotype.", "data.")):
            continue
        ref = step["result_ref"]
        result = saved_result(ref, root)
        eid = stable_id("tool_observation", [store.context.run_id, ref["result_id"], ref["sha256"]])
        evidence_ids[ref["result_id"]] = eid
        records.append(Evidence(id=eid, kind="protocol" if step["action_id"].startswith("protocol.") and step["action_id"] != "protocol.phenotype_compare" else "phenotype",
            source_refs=[{**ref, "path": str(root / ref["path"])}], run_id=store.context.run_id, trace_id=report["trace_id"],
            quality="passed", observations={"classification": "tool_observation", "action_id": step["action_id"],
                "result_ref": ref, "validation": result.get("validation", {}), "policy": "Observed tool output, not an established causal fact."}))
    for claim in report["conclusions"]:
        citations = [*claim["supporting_refs"], *claim["opposing_refs"]]
        if not citations:
            continue
        for citation in citations:
            if citation["source_type"] == "result":
                pointer_value(saved_result(citation["result_ref"], root), citation["field_path"])
            else:
                if file_hash(root / "history_context.json") != citation["sha256"]:
                    raise ValueError("Historical source snapshot changed")
        iid = stable_id("interpretation", [claim, store.context.run_id])
        protocols = list(dict.fromkeys(pid for citation in claim["supporting_refs"] if citation["source_type"] == "result"
            for pid in _protocol_ids(pointer_value(saved_result(citation["result_ref"], root), citation["field_path"]))))
        records.append(ScientificInterpretation(id=iid, claim=claim["claim"], explanation=claim["explanation"],
            classification="hypothesis" if claim["evidence_level"] == "hypothesis" else "interpretation",
            source_refs=[{**citation, "origin_run_root": str(root)} for citation in citations],
            evidence_for=[evidence_ids[citation["source_id"]] for citation in claim["supporting_refs"] if citation["source_id"] in evidence_ids],
            evidence_against=[evidence_ids[citation["source_id"]] for citation in claim["opposing_refs"] if citation["source_id"] in evidence_ids],
            scope={"protocol_ids": protocols or report["plan"].get("objects", []), "endpoints": report["plan"].get("target_metrics", [])},
            limitations=claim["limitations"], originating_run_id=store.context.run_id))
    if records:
        store.put(records, "S0")
    return evidence_ids


def finish_summary(store: ScientificWorldModel, request: dict[str, Any], thread_id: str, report: dict[str, Any], root: Path,
                   messages: list[dict[str, Any]]) -> dict[str, Any]:
    memory = ConversationMemory(store)
    summary = json.loads(json.dumps(request["session_summary"]))
    protocols = list(dict.fromkeys(str(value) for step in report["plan"].get("steps", [])
        for key in ("protocol_id", "protocol_id_a", "protocol_id_b", "protocol_ids")
        for value in (step["inputs"].get(key, []) if key == "protocol_ids" else [step["inputs"].get(key)]) if value))
    for step in report["step_results"]:
        if step.get("usable") and step.get("result_ref"):
            protocols.extend(pid for pid in _protocol_ids(saved_result(step["result_ref"], root).get("result_preview", {})) if pid not in protocols)
    if protocols:
        summary["protocol_ids"] = protocols
    if report["plan"].get("target_metrics"):
        summary["endpoints"] = report["plan"]["target_metrics"]
    summary["pending_questions"] = list(dict.fromkeys([*summary["pending_questions"], *report["clarification_questions"]]))
    for step in report["step_results"]:
        if step.get("result_ref"):
            ref = {**step["result_ref"], "run_id": store.context.run_id, "run_root": str(root)}
            if ref not in summary["result_refs"]:
                summary["result_refs"].append(ref)
    for conclusion in report["conclusions"]:
        for ref in conclusion["opposing_refs"]:
            scoped = {**ref, "run_id": store.context.run_id, "run_root": str(root)}
            if scoped not in summary["opposing_refs"]:
                summary["opposing_refs"].append(scoped)
    history = request.get("history_context", {})
    for row in [*history.get("hypotheses", []), *history.get("interpretations", [])]:
        for field in ("opposing_evidence_refs", "evidence_against", "opposing_evidence"):
            if row.get(field):
                ref = {"source_type": "history", "source_id": row["id"], "version": row["version"], "field_path": "/" + field,
                       "run_id": store.context.run_id, "run_root": str(root), "sha256": file_hash(root / "history_context.json")}
                if ref not in summary["opposing_refs"]:
                    summary["opposing_refs"].append(ref)
    memory.capture(thread_id, messages)
    summary = memory.save(thread_id, summary, request["session_summary"]["version"])
    atomic_json(root / "session_summary.json", summary)
    return summary
