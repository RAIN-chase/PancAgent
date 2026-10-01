from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from protocol_optimization_agent.runtime import RunContext
from protocol_optimization_agent.scientific_ingestion import (
    ingest_experiment_proposals,
    ingest_phenotype_outputs,
    ingest_protocol_outputs,
)
from protocol_optimization_agent.scientific_models import ExperimentFeedback
from protocol_optimization_agent.scientific_state import ScientificWorldModel


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect scientific state and record researcher-reviewed experimental feedback."
    )
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--project-id", default=os.getenv("PROTOCOL_RESEARCH_PROJECT_ID") or "default")
    parser.add_argument("--run-id", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--limit", type=int, default=20)
    history = commands.add_parser("history")
    history.add_argument("kind", choices=["protocol", "phenotype", "evidence", "claim", "hypothesis", "experiment"])
    history.add_argument("record_id")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("agent", choices=["pa", "da", "po"])
    ingest.add_argument("output_dir", type=Path)
    review = commands.add_parser("review")
    review.add_argument("experiment_id")
    review.add_argument("--decision", choices=["approve", "reject"], required=True)
    review.add_argument("--researcher", required=True)
    review.add_argument("--reason", required=True)
    performed = commands.add_parser("performed")
    performed.add_argument("experiment_id")
    performed.add_argument("--researcher", required=True)
    feedback = commands.add_parser("feedback")
    feedback.add_argument("json_path", type=Path)
    args = parser.parse_args()
    context = RunContext.from_values(user_id=args.user_id, run_id=args.run_id, workspace_root=args.workspace_root)
    store = ScientificWorldModel(context, args.project_id)
    if args.command == "snapshot":
        result = store.snapshot(args.limit)
    elif args.command == "history":
        result = store.history(args.kind, args.record_id)
    elif args.command == "ingest":
        result = {"pa": ingest_protocol_outputs, "da": ingest_phenotype_outputs, "po": ingest_experiment_proposals}[
            args.agent
        ](store, args.output_dir)
    elif args.command == "review":
        result = store.review(args.experiment_id, args.decision == "approve", args.researcher, args.reason)
    elif args.command == "performed":
        result = store.mark_performed(args.experiment_id, args.researcher)
    else:
        payload = ExperimentFeedback.model_validate_json(args.json_path.read_text(encoding="utf-8"))
        result = store.record_feedback(payload)
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
