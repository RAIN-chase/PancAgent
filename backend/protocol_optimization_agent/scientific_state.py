from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from protocol_optimization_agent.runtime import RunContext
from protocol_optimization_agent.scientific_models import (
    Claim,
    Evidence,
    ExperimentCandidate,
    ExperimentFeedback,
    Hypothesis,
    PhenotypeState,
    ProtocolState,
    ScientificRecord,
)

RECORD_TYPES = {
    "protocol": ProtocolState,
    "phenotype": PhenotypeState,
    "evidence": Evidence,
    "claim": Claim,
    "hypothesis": Hypothesis,
    "experiment": ExperimentCandidate,
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


def stable_id(prefix: str, payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


class ScientificWorldModel:
    """User/project-scoped records with transactional revisions and experiment feedback."""

    def __init__(self, context: RunContext, project_id: str = "default") -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", project_id):
            raise ValueError("project_id must be a single machine-safe identifier.")
        self.context = context
        self.project_id = project_id
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
        if stage not in SCIENTIFIC_STAGES:
            raise ValueError("Unknown scientific stage.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            known = {(kind, record_id) for kind, record_id in db.execute("SELECT kind,id FROM records")}
            known.update((self._kind(record), record.id) for record in records)
            for record in records:
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
                    # Regeneration must not erase experimental belief or contradictory evidence.
                    record = record.model_copy(
                        update={
                            key: previous[key] for key in ("evidence_for", "evidence_against", "status", "confidence")
                        }
                    )
                    if kind == "hypothesis":
                        record = record.model_copy(update={"tested_experiment_ids": previous["tested_experiment_ids"]})
                if previous and kind == "experiment":
                    continue
                self._save(db, kind, record, "S0" if kind == "evidence" and stage in {"S1", "S2"} else stage)
        return {
            "path": str(self.path),
            "project_id": self.project_id,
            "stage": stage,
            "record_ids": [r.id for r in records],
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
        return stable_id("feedback", [row for row in payloads if row["status"] != "PROPOSED"])

    def reasoning_context(self) -> dict[str, Any]:
        with self._connect() as db:
            hypotheses = [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM records WHERE kind='hypothesis' ORDER BY id")
            ]
        return {
            "hypotheses": [
                {key: h[key] for key in ("id", "status", "evidence_for", "evidence_against", "tested_experiment_ids")}
                for h in hypotheses
            ],
            "feedback_fingerprint": self.feedback_fingerprint(),
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

    def review(self, experiment_id: str, approve: bool, researcher: str, reason: str) -> dict[str, Any]:
        if not researcher.strip() or not reason.strip():
            raise ValueError("Researcher identity and review reason are required.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = ExperimentCandidate.model_validate(self._required(db, "experiment", experiment_id))
            if candidate.status != "PROPOSED":
                raise ValueError("Only proposed experiments may be reviewed.")
            if approve and (
                not candidate.eligible
                or candidate.critique
                or candidate.assessment is None
                or candidate.utility is None
                or not candidate.evidence_refs
                or not candidate.scientific_preconditions.get("scientific_ready")
            ):
                raise ValueError("Resolve scientific preconditions and decision assessments before approval.")
            candidate.status = "APPROVED" if approve else "REJECTED"
            candidate.reviewer, candidate.review_reason = researcher, reason
            self._save(db, "experiment", candidate, "S6")
        return self.get("experiment", experiment_id)

    def mark_performed(self, experiment_id: str, researcher: str) -> dict[str, Any]:
        if not researcher.strip():
            raise ValueError("Researcher identity is required.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = ExperimentCandidate.model_validate(self._required(db, "experiment", experiment_id))
            if candidate.status != "APPROVED":
                raise ValueError("Only approved experiments may be marked performed.")
            candidate.status = "PERFORMED"
            self._save(db, "experiment", candidate, "S7", {"researcher": researcher})
        return self.get("experiment", experiment_id)

    def record_feedback(self, feedback: ExperimentFeedback) -> dict[str, Any]:
        digest = stable_id("feedback", feedback.model_dump(mode="json"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = ExperimentCandidate.model_validate(self._required(db, "experiment", feedback.experiment_id))
            if candidate.status == "ASSESSED" and candidate.feedback_digest == digest:
                return self._required(db, "experiment", candidate.id)
            if candidate.status != "PERFORMED":
                raise ValueError("Experimental feedback requires a performed experiment.")
            if set(feedback.outcomes) != set(candidate.hypothesis_ids):
                raise ValueError("Feedback must assess every hypothesis linked to this experiment.")
            if any(
                not any(
                    m.value is not None and m.evidence_refs and m.status in {"ok", "numeric", "computed", "passed"}
                    for m in p.metrics
                )
                or p.validation_status != "passed"
                for p in feedback.phenotypes
            ):
                raise ValueError("Feedback requires validated numeric phenotype observations.")
            self._save(db, "evidence", feedback.evidence, "S8")
            for phenotype in feedback.phenotypes:
                if phenotype.evidence_ids and phenotype.evidence_ids != [feedback.evidence.id]:
                    raise ValueError("Feedback phenotypes must refer to the supplied experimental evidence.")
                phenotype = phenotype.model_copy(update={"evidence_ids": [feedback.evidence.id]})
                self._save(db, "phenotype", phenotype, "S8")
            candidate.status = "OBSERVED"
            candidate.result_evidence_ids.append(feedback.evidence.id)
            self._save(db, "experiment", candidate, "S8")
            for hypothesis_id, outcome in feedback.outcomes.items():
                hypothesis = Hypothesis.model_validate(self._required(db, "hypothesis", hypothesis_id))
                if outcome == "supports":
                    hypothesis.evidence_for.append(feedback.evidence.id)
                elif outcome == "refutes":
                    hypothesis.evidence_against.append(feedback.evidence.id)
                hypothesis.tested_experiment_ids.append(candidate.id)
                hypothesis.status = (
                    "UNRESOLVED"
                    if bool(hypothesis.evidence_for) == bool(hypothesis.evidence_against)
                    else "SUPPORTED"
                    if hypothesis.evidence_for
                    else "REFUTED"
                )
                hypothesis.confidence = "moderate" if hypothesis.status != "UNRESOLVED" else "low"
                self._save(
                    db,
                    "hypothesis",
                    hypothesis,
                    "S9",
                    {
                        "researcher": feedback.researcher,
                        "interpretation": feedback.interpretation,
                        "outcome": outcome,
                        "evidence_id": feedback.evidence.id,
                    },
                )
            candidate.status, candidate.feedback_digest = "ASSESSED", digest
            self._save(db, "experiment", candidate, "S9")
        return self.get("experiment", candidate.id)

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
            if kind == "evidence":
                raise ValueError("Evidence is immutable; register a new evidence id for revised observations.")
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
