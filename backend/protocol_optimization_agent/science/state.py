from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.science.identity import stable_id as stable_id
from protocol_optimization_agent.science.models import (
    AnalysisDataset,
    AnalysisResult,
    Claim,
    Evidence,
    ExperimentCandidate,
    ExperimentFeedback,
    FeedbackAdmission,
    Hypothesis,
    PhenotypeState,
    ProtocolState,
    ScientificInterpretation,
    ScientificRecord,
)

RECORD_TYPES = {
    "protocol": ProtocolState,
    "phenotype": PhenotypeState,
    "evidence": Evidence,
    "claim": Claim,
    "hypothesis": Hypothesis,
    "experiment": ExperimentCandidate,
    "feedback_admission": FeedbackAdmission,
    "analysis_dataset": AnalysisDataset,
    "analysis_result": AnalysisResult,
    "interpretation": ScientificInterpretation,
}
SCIENTIFIC_STAGES = {
    "S0": "Evidence ingestion",
    "S1": "Structured protocol available",
    "S2": "Phenotype characterized",
    "S3": "Protocol-phenotype association",
    "S4": "Hypothesis generated",
    "S5": "Candidate experiments proposed",
    "S6": "Researcher review",
    "S7": "Experiment performed",
    "S8": "New phenotype observed",
    "S9": "Hypothesis assessed",
}




class ScientificWorldModel:
    """User/project-scoped records with transactional revisions and experiment feedback."""

    def __init__(self, context: RunContext, project_id: str = "default") -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", project_id):
            raise ValueError("project_id must be a single machine-safe identifier.")
        self.context = context
        self.project_id = project_id
        if authenticated_mode():
            AccessPolicy().require(context.user_id, project_id)
            if context.project_id != project_id:
                raise PermissionError("Scientific project must match the authenticated run context.")
        root = context.user_root().resolve()
        self.path = (root / "research" / project_id / "scientific_state.sqlite3").resolve()
        if not self.path.is_relative_to(root):
            raise ValueError("Scientific state path escapes user workspace.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, version INTEGER, payload TEXT, PRIMARY KEY(kind,id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS history (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, id TEXT, version INTEGER, stage TEXT, timestamp TEXT, run_id TEXT, trace_id TEXT, payload TEXT)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def put(self, records: list[ScientificRecord], stage: str) -> dict[str, Any]:
        if authenticated_mode():
            AccessPolicy().require(self.context.user_id, self.project_id, "write")
        if stage not in SCIENTIFIC_STAGES:
            raise ValueError("Unknown scientific stage.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            known = {(kind, record_id) for kind, record_id in db.execute("SELECT kind,id FROM records")}
            known.update((self._kind(record), record.id) for record in records)
            for record in records:
                if isinstance(record, ScientificInterpretation):
                    record = ScientificInterpretation.model_validate(record.model_dump())
                kind = self._kind(record)
                if isinstance(record, Claim) and (record.status != "UNRESOLVED" or record.evidence_against):
                    raise ValueError("Belief updates must use reviewed experiment feedback.")
                if isinstance(record, Hypothesis) and (record.evidence_for or record.tested_experiment_ids):
                    raise ValueError("Hypothesis outcomes must use reviewed experiment feedback.")
                if isinstance(record, ExperimentCandidate) and (
                    record.status != "PROPOSED"
                    or record.reviewer
                    or record.result_evidence_ids
                    or record.feedback_digest
                    or record.approved_evaluation_id
                    or record.review_reason
                    or record.result_phenotype_ids
                ):
                    raise ValueError("Experiment lifecycle changes require researcher commands.")
                for ref in self._evidence_links(record):
                    if ("evidence", ref) not in known:
                        raise ValueError(f"Missing evidence reference: {ref}")
                if isinstance(record, ExperimentCandidate):
                    if any(("hypothesis", ref) not in known for ref in record.hypothesis_ids):
                        raise ValueError("Experiment refers to an unknown hypothesis.")
                previous = self._get(db, kind, record.id)
                if previous and isinstance(record, (Hypothesis, Claim)):
                    if isinstance(record, Hypothesis) and previous.get("test_spec", {}) != record.test_spec:
                        raise ValueError("Testable hypothesis identity cannot change its intervention/context.")
                    # Regeneration must not erase experimental belief or contradictory evidence.
                    record = record.model_copy(
                        update={
                            key: previous[key] for key in ("evidence_for", "evidence_against", "status", "confidence")
                        }
                    )
                    if isinstance(record, Hypothesis):
                        record = record.model_copy(update={"tested_experiment_ids": previous["tested_experiment_ids"]})
                        opposing = {json.dumps(ref, sort_keys=True): ref for ref in [
                            *previous.get("opposing_evidence_refs", []), *record.opposing_evidence_refs]}
                        record = record.model_copy(update={"opposing_evidence_refs": list(opposing.values())})
                if kind == "feedback_admission":
                    raise ValueError("Feedback admission requires a researcher command.")
                if isinstance(record, ExperimentCandidate):
                    if previous and previous["status"] != "PROPOSED":
                        continue
                    if previous and (previous["hypothesis_ids"] != record.hypothesis_ids):
                        raise ValueError("Candidate identity cannot change its hypothesis.")
                    evaluation = record.model_dump(exclude={
                        "version", "evaluation_id", "evaluation_fingerprint", "evaluation_version", "evaluated_at",
                        "approved_evaluation_id", "reviewer", "review_reason", "status",
                        "result_evidence_ids", "result_phenotype_ids", "feedback_digest",
                    })
                    fingerprint = stable_id("evaluation_content", evaluation)
                    if previous and previous.get("evaluation_fingerprint") == fingerprint:
                        continue
                    revision = previous.get("evaluation_version", 0) + 1 if previous else 1
                    record = record.model_copy(update={
                        "evaluation_id": stable_id("evaluation", [record.id, fingerprint, revision]),
                        "evaluation_fingerprint": fingerprint,
                        "evaluation_version": revision,
                        "evaluated_at": datetime.now(timezone.utc).isoformat(),
                    })
                self._save(db, kind, record, "S0" if kind == "evidence" and stage in {"S1", "S2"} else stage)
        return {
            "path": str(self.path),
            "project_id": self.project_id,
            "stage": stage,
            "record_ids": [r.id for r in records],
            "records": [self.get(self._kind(r), r.id) for r in records],
        }

    def snapshot(self, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        with self._connect() as db:
            records = {
                kind: [
                    json.loads(row[0])
                    for row in db.execute("SELECT payload FROM records WHERE kind=? ORDER BY id LIMIT ?", (kind, limit))
                ]
                for kind in RECORD_TYPES
            }
            counts = dict(db.execute("SELECT kind,COUNT(*) FROM records GROUP BY kind"))
            history = [
                {
                    "seq": row[0],
                    "kind": row[1],
                    "id": row[2],
                    "version": row[3],
                    "stage": row[4],
                    "timestamp": row[5],
                    "run_id": row[6],
                    "trace_id": row[7],
                }
                for row in db.execute(
                    "SELECT seq,kind,id,version,stage,timestamp,run_id,trace_id FROM history ORDER BY seq DESC LIMIT ?",
                    (limit,),
                )
            ]
        return {
            "schema_version": "scientific-world-model-v1",
            "project_id": self.project_id,
            "user_id": self.context.user_id,
            "counts": counts,
            "records": records,
            "history": history,
            "stages": SCIENTIFIC_STAGES,
            "path": str(self.path),
            "limit_per_kind": limit,
        }

    def get(self, kind: str, record_id: str) -> dict[str, Any]:
        if kind not in RECORD_TYPES:
            raise ValueError("Unknown record kind.")
        with self._connect() as db:
            result = self._get(db, kind, record_id)
        if result is None:
            raise ValueError(f"Unknown {kind}: {record_id}")
        return result

    def feedback_fingerprint(self) -> str:
        with self._connect() as db:
            payloads = [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM records WHERE kind='experiment' ORDER BY id")
            ]
            admissions = [json.loads(row[0]) for row in db.execute("SELECT payload FROM records WHERE kind='feedback_admission' ORDER BY id")]
        return stable_id("feedback", {"experiments": [row for row in payloads if row["status"] != "PROPOSED"], "admissions": admissions})

    def reasoning_context(self, question: str | None = None, limit: int = 8, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 20:
            raise ValueError("Planning history limit must be between 1 and 20.")
        with self._connect() as db:
            phrases = re.findall(r"\bprotocol\s+[A-Za-z0-9_.-]+\b", question or "", re.I)
            terms = list(dict.fromkeys(term.lower() for term in [*phrases,
                *re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,63}|[\u4e00-\u9fff]{2,16}", question or "")]
                if term.lower() not in {"protocol", "protocols", "compare", "the", "and", "which", "better", "expression", "find"}))[:12]
            parameters = ["%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for term in terms]
            if question is not None or filters:
                from protocol_optimization_agent.science.memory import retrieve_records
                hypotheses = retrieve_records(db, "hypothesis", question or "", filters or {}, limit)
                interpretations = retrieve_records(db, "interpretation", question or "", filters or {}, limit)
            else:
                hypotheses = [json.loads(row[0]) for row in db.execute("SELECT payload FROM records WHERE kind='hypothesis' ORDER BY id")]
                interpretations = []
            admissions = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM records WHERE kind='feedback_admission'" + (" AND 0" if question is not None else "") + " ORDER BY id"
            )]
            previous_clause = " AND (" + " OR ".join("lower(json_extract(payload, '$.record.objective')) LIKE ? ESCAPE '\\'" for _ in terms) + ")" if terms else " AND 0"
            previous = db.execute("SELECT payload FROM history WHERE kind='analysis_result'" +
                (previous_clause if question is not None else "") + " ORDER BY seq DESC LIMIT 1",
                parameters if question is not None else []).fetchone()
        return {
            "hypotheses": [
                {key: h.get(key) for key in ("id", "version", "feature", "statement", "objective", "protocol_ids", "topic_id", "test_spec", "status", "evidence_for", "evidence_against", "tested_experiment_ids", "supporting_evidence_refs", "opposing_evidence_refs", "supporting_evidence", "opposing_evidence", "supporting_missing_evidence_ids", "opposing_missing_evidence_ids")}
                for h in hypotheses
            ],
            "interpretations": interpretations,
            "feedback_fingerprint": self.feedback_fingerprint(),
            "admitted_feedback": admissions,
            "previous_analysis_result": ({key: json.loads(previous[0])["record"].get(key) for key in ("id", "version", "objective", "analysis_dataset_id")}
                if question is not None and previous else json.loads(previous[0])["record"] if previous else None),
            "retrieval_scope": {"question": question, "limit": limit, "filters": filters or {}, "terms": terms,
                "policy": "structured conditions first; lexical and explicit synonyms second; no transfer across mismatched contexts"} if question is not None or filters else {},
        }

    def history(self, kind: str, record_id: str) -> list[dict[str, Any]]:
        if kind not in RECORD_TYPES:
            raise ValueError("Unknown record kind.")
        with self._connect() as db:
            return [
                {
                    "version": row[0],
                    "stage": row[1],
                    "timestamp": row[2],
                    "run_id": row[3],
                    "trace_id": row[4],
                    **json.loads(row[5]),
                }
                for row in db.execute(
                    "SELECT version,stage,timestamp,run_id,trace_id,payload FROM history WHERE kind=? AND id=? ORDER BY seq",
                    (kind, record_id),
                )
            ]

    def review(self, experiment_id: str, approve: bool, researcher: str, reason: str,
               *, evaluation_id: str) -> dict[str, Any]:
        from protocol_optimization_agent.extensions.experimental_feedback.lifecycle import review

        return review(self, experiment_id, approve, researcher, reason, evaluation_id=evaluation_id)

    def mark_performed(self, experiment_id: str, researcher: str) -> dict[str, Any]:
        from protocol_optimization_agent.extensions.experimental_feedback.lifecycle import mark_performed

        return mark_performed(self, experiment_id, researcher)

    def record_feedback(self, feedback: ExperimentFeedback) -> dict[str, Any]:
        from protocol_optimization_agent.extensions.experimental_feedback.lifecycle import record_feedback

        return record_feedback(self, feedback)

    def admit_feedback(self, admission: FeedbackAdmission) -> dict[str, Any]:
        from protocol_optimization_agent.extensions.experimental_feedback.lifecycle import admit_feedback

        return admit_feedback(self, admission)

    @staticmethod
    def _kind(record: ScientificRecord) -> str:
        for kind, cls in RECORD_TYPES.items():
            if type(record) is cls:
                return kind
        raise ValueError("Unsupported scientific record type.")

    @staticmethod
    def _evidence_links(record: ScientificRecord) -> list[str]:
        if isinstance(record, (ProtocolState, PhenotypeState)):
            return record.evidence_ids
        if isinstance(record, Claim):
            return [*record.evidence_for, *record.evidence_against, *getattr(record, "rationale_evidence", [])]
        if isinstance(record, ScientificInterpretation):
            return [*record.evidence_for, *record.evidence_against]
        return []

    @staticmethod
    def _get(db: sqlite3.Connection, kind: str, record_id: str) -> dict[str, Any] | None:
        row = db.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        return json.loads(row[0]) if row else None

    def _required(self, db: sqlite3.Connection, kind: str, record_id: str) -> dict[str, Any]:
        result = self._get(db, kind, record_id)
        if result is None:
            raise ValueError(f"Unknown {kind}: {record_id}")
        return result

    def _save(
        self,
        db: sqlite3.Connection,
        kind: str,
        record: ScientificRecord,
        stage: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        previous = self._get(db, kind, record.id)
        payload = record.model_dump(mode="json", exclude={"version"})
        if previous:
            old = {key: value for key, value in previous.items() if key != "version"}
            if old == payload:
                return
            if kind in {"evidence", "interpretation", "analysis_dataset", "analysis_result", "feedback_admission"}:
                raise ValueError("Evidence/dataset/admission is immutable; register a new id for revised observations.")
        payload["version"] = previous["version"] + 1 if previous else 1
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?)", (kind, record.id, payload["version"], encoded))
        db.execute(
            "INSERT INTO history(kind,id,version,stage,timestamp,run_id,trace_id,payload) VALUES (?,?,?,?,?,?,?,?)",
            (
                kind,
                record.id,
                payload["version"],
                stage,
                datetime.now(timezone.utc).isoformat(),
                self.context.run_id,
                self.context.trace_id,
                json.dumps({"record": payload, "details": details or {}}, allow_nan=False),
            ),
        )
