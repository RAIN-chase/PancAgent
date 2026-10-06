from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import uuid4

from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.analysis.protocol.input_loader import load_protocol_analysis_inputs
from protocol_optimization_agent.runtime.archive import RunArchive
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.runtime.io import read_json
from protocol_optimization_agent.runtime.llm_client import build_llm_client


def main() -> None:
    parser = argparse.ArgumentParser(description="Run only the Protocol Analysis Agent.")
    parser.add_argument(
        "--input-dir", default="inputs/Protocols inputs", help="Directory containing the formal protocols.jsonl export."
    )
    parser.add_argument(
        "--output-dir", default="runs/protocol_analysis_agent_run", help="Directory for protocol analysis outputs."
    )
    parser.add_argument("--min-confidence", type=float, default=0.65)
    parser.add_argument(
        "--allow-flagged-events", action="store_true", help="Also keep factor events with validation_status=has_flags."
    )
    parser.add_argument(
        "--allow-schema-invalid-events",
        action="store_true",
        help="Retain schema-invalid factor events with schema_status=accepted_with_schema_findings.",
    )
    parser.add_argument("--base-feature-registry-path", default="configs/base_feature_registry.json")
    parser.add_argument("--approved-feature-registry-path", default="configs/approved_feature_registry.json")
    parser.add_argument("--run-specific-feature-registry-path", default="")
    parser.add_argument("--max-llm-feature-discovery-protocols", type=int, default=0)
    parser.add_argument(
        "--llm-provider", default="offline", choices=["offline", "openai", "openai_compatible", "deepseek"]
    )
    parser.add_argument("--llm-model", default="")
    parser.add_argument("--llm-api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--llm-base-url", default="https://api.openai.com/v1")
    args = parser.parse_args()
    context = RunContext.from_values(user_id=os.getenv("PROTOCOL_USER_ID"), workspace_root=Path(args.output_dir),
                                     run_id=f"run_{uuid4().hex[:16]}")
    archive = RunArchive(context)
    archive.start({"entrypoint": "protocol-analysis-cli", "config": vars(args)})

    input_dir = Path(args.input_dir)
    allowed_statuses = ["evidence_supported"]
    if args.allow_flagged_events:
        allowed_statuses.append("has_flags")

    try:
        with archive.scope():
            agent = ProtocolAnalysisAgent(
                output_dir=archive.root / "protocol_analysis",
                quality_filters={
                    "min_protocol_confidence": args.min_confidence,
                    "require_comparison_ready": True,
                    "allowed_event_validation_statuses": allowed_statuses,
                    "reject_unparsed_numeric_fields": True,
                    "require_evidence_quote_for_key_factors": True,
                    "allow_schema_invalid_events": args.allow_schema_invalid_events,
                    "base_feature_registry_path": args.base_feature_registry_path,
                    "approved_feature_registry_path": args.approved_feature_registry_path,
                    "run_specific_feature_registry_path": args.run_specific_feature_registry_path,
                    "max_llm_feature_discovery_protocols": args.max_llm_feature_discovery_protocols,
                },
                llm_client=build_llm_client(
                    {"provider": args.llm_provider, "model": args.llm_model,
                     "api_key_env": args.llm_api_key_env, "base_url": args.llm_base_url}
                    if args.llm_provider != "offline" else {"provider": "offline"}
                ),
            )
            loaded = load_protocol_analysis_inputs(input_dir)
            outputs = agent.run(
                protocol_records=loaded["protocol_records"],
                factor_events=loaded["factor_events"],
                source_identity_rows=loaded["source_identity_rows"],
            )
        gate = read_json(outputs["matrix_release_gate_report"])
        archive.finish({"outputs": outputs, "protocol_record_source": loaded["protocol_record_source"]},
                       status="partial" if gate.get("release_ready") is False else "succeeded")
    except BaseException as exc:
        archive.record("error", {"type": type(exc).__name__, "message": str(exc)})
        archive.finish({"error": str(exc)}, status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed")
        raise

    print(f"Protocol record source: {loaded['protocol_record_source']}")
    print("Protocol Analysis Agent outputs:")
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
