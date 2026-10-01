from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from protocol_optimization_agent.query_engine import ProtocolQueryEngine


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
    engine = ProtocolQueryEngine(Path(args.output_dir))
    payload: Any

    if args.command == "find":
        payload = engine.find_protocols(args.query)
    elif args.command == "summary":
        payload = engine.get_protocol_summary(args.protocol_id)
    elif args.command == "compare":
        payload = engine.compare_protocols(args.protocol_id_a, args.protocol_id_b, max_features=args.max_features)
    elif args.command == "evidence":
        payload = engine.get_evidence_for_feature(args.protocol_id, args.feature)
    elif args.command == "collection":
        payload = engine.get_protocol_collection_summary()
    elif args.command == "chat":
        payload = engine.answer_question(args.question, max_features=args.max_features)
    else:
        raise ValueError(f"Unknown command: {args.command}")

    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
