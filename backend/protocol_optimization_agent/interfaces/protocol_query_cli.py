from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode
from protocol_optimization_agent.runtime.archive import RunArchive
from protocol_optimization_agent.runtime.context import RunContext


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Query Protocol Analysis Agent outputs.")
    parser.add_argument(
        "--output-dir",
        default="runs/protocol_analysis_agent_simplified_autofeatures",
        help="Directory containing protocol analysis outputs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    find_parser = subparsers.add_parser(
        "find", help="Find protocols by protocol id, paper id, dataset id, or protocol name."
    )
    find_parser.add_argument("query")

    summary_parser = subparsers.add_parser("summary", help="Show a structured protocol summary.")
    summary_parser.add_argument("protocol_id")

    compare_parser = subparsers.add_parser("compare", help="Compare two protocols.")
    compare_parser.add_argument("protocol_id_a")
    compare_parser.add_argument("protocol_id_b")
    compare_parser.add_argument("--max-features", type=int, default=40)

    evidence_parser = subparsers.add_parser("evidence", help="Get provenance for one protocol feature.")
    evidence_parser.add_argument("protocol_id")
    evidence_parser.add_argument("feature")

    subparsers.add_parser("collection", help="Show protocol collection counts and matrix inventory.")

    chat_parser = subparsers.add_parser(
        "chat", help="Answer a natural-language protocol query using local structured outputs."
    )
    chat_parser.add_argument("question")
    chat_parser.add_argument("--max-features", type=int, default=30)

    args = parser.parse_args()
    identity = AccessPolicy().authenticate("Bearer " + os.getenv("PROTOCOL_AUTH_TOKEN", "")) if authenticated_mode() else None
    context = RunContext.from_values(user_id=identity, run_id=f"run_{uuid4().hex[:16]}")
    source = Path(args.output_dir)
    if authenticated_mode():
        source = AccessPolicy().authorize_path(identity, context.project_id, source, "read")
    archive = RunArchive(context)
    archive.start({"entrypoint": "protocol-query-cli", "config": vars(args)})
    try:
        with archive.scope(), archive.operation(f"protocol-query.{args.command}", vars(args)) as call:
            if not source.is_dir():
                raise FileNotFoundError(f"Protocol output collection does not exist: {source}")
            engine = ProtocolQueryEngine(archive.seed(source, "protocol_analysis"))
            payload = _query(engine, args)
            archive.result(call, payload)
        unavailable = (isinstance(payload, dict) and payload.get("status", "ok") != "ok") or payload == []
        archive.finish({"result": payload}, status="partial" if unavailable else "succeeded")
    except BaseException as exc:
        archive.record("error", {"type": type(exc).__name__, "message": str(exc)})
        archive.finish({"error": str(exc)}, status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed")
        raise
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"run_id={context.run_id} run_dir={archive.root}", file=sys.stderr)


def _query(engine: ProtocolQueryEngine, args: argparse.Namespace) -> Any:
    if args.command == "find":
        return engine.find_protocols(args.query)
    elif args.command == "summary":
        return engine.get_protocol_summary(args.protocol_id)
    elif args.command == "compare":
        return engine.compare_protocols(args.protocol_id_a, args.protocol_id_b, max_features=args.max_features)
    elif args.command == "evidence":
        return engine.get_evidence_for_feature(args.protocol_id, args.feature)
    elif args.command == "collection":
        return engine.get_protocol_collection_summary()
    elif args.command == "chat":
        return engine.answer_question(args.question, max_features=args.max_features)
    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
