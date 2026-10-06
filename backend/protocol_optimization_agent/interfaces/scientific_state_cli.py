from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from protocol_optimization_agent.extensions.experimental_feedback import lifecycle
from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.science.ingestion import (
    ingest_experiment_proposals,
    ingest_phenotype_outputs,
    ingest_protocol_outputs,
)
from protocol_optimization_agent.science.models import ExperimentFeedback, FeedbackAdmission
from protocol_optimization_agent.science.state import ScientificWorldModel


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
    history.add_argument("kind", choices=["protocol", "phenotype", "evidence", "claim", "hypothesis", "experiment", "analysis_dataset", "analysis_result", "feedback_admission"])
    history.add_argument("record_id")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("agent", choices=["pa", "da", "po"])
    ingest.add_argument("output_dir", type=Path)
    review = commands.add_parser("review")
    review.add_argument("experiment_id")
    review.add_argument("--decision", choices=["approve", "reject"], required=True)
    review.add_argument("--researcher", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--evaluation-id", required=True)
    performed = commands.add_parser("performed")
    performed.add_argument("experiment_id")
    performed.add_argument("--researcher", required=True)
    feedback = commands.add_parser("feedback")
    feedback.add_argument("json_path", type=Path)
    admit = commands.add_parser("admit-feedback")
    admit.add_argument("json_path", type=Path)
    args = parser.parse_args()
    if authenticated_mode():
        identity = AccessPolicy().authenticate("Bearer " + os.getenv("PROTOCOL_AUTH_TOKEN", ""))
        if args.user_id and args.user_id != identity:
            raise PermissionError("CLI user_id cannot override authenticated identity.")
        args.user_id = identity
    context = RunContext.from_values(user_id=args.user_id, run_id=args.run_id, workspace_root=args.workspace_root,
                                     project_id=args.project_id)
    store = ScientificWorldModel(context, args.project_id)
    if authenticated_mode() and getattr(args, "json_path", None):
        args.json_path = AccessPolicy().authorize_path(context.user_id, args.project_id, args.json_path, "read")
    if args.command == "snapshot":
        result = store.snapshot(args.limit)
    elif args.command == "history":
        result = store.history(args.kind, args.record_id)
    elif args.command == "ingest":
        result = {"pa": ingest_protocol_outputs, "da": ingest_phenotype_outputs, "po": ingest_experiment_proposals}[
            args.agent
        ](store, AccessPolicy().authorize_path(context.user_id, args.project_id, args.output_dir, "write")
          if authenticated_mode() else args.output_dir)
    elif args.command == "review":
        result = lifecycle.review(store, args.experiment_id, args.decision == "approve", args.researcher, args.reason,
                              evaluation_id=args.evaluation_id)
    elif args.command == "performed":
        result = lifecycle.mark_performed(store, args.experiment_id, args.researcher)
    elif args.command == "feedback":
        payload = ExperimentFeedback.model_validate_json(args.json_path.read_text(encoding="utf-8"))
        result = lifecycle.record_feedback(store, payload)
    else:
        result = lifecycle.admit_feedback(store, FeedbackAdmission.model_validate_json(args.json_path.read_text(encoding="utf-8")))
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
