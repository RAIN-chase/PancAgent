from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.input_loader import load_protocol_analysis_inputs
from protocol_optimization_agent.llm_client import build_llm_client
from protocol_optimization_agent.protocol_chat_agent import ProtocolChatAgent
from protocol_optimization_agent.query_engine import ProtocolQueryEngine


class ProtocolAnalysisRunRequest(BaseModel):
    input_dir: str = Field(default="inputs/Protocols inputs")
    output_dir: str = Field(default="runs/protocol_analysis_agent_api")
    min_confidence: float = Field(default=0.65)
    allow_flagged_events: bool = False
    max_llm_normalization_events: int = 0
    max_llm_semantic_stages: int = 0
    max_llm_feature_discovery_protocols: int = 0
    allow_schema_invalid_events: bool = False
    base_feature_registry_path: str = "configs/base_feature_registry.json"
    approved_feature_registry_path: str = "configs/approved_feature_registry.json"
    run_specific_feature_registry_path: str = ""
    llm: dict[str, Any] = Field(default_factory=lambda: {"provider": "offline"})


class ProtocolFindRequest(BaseModel):
    output_dir: str = Field(default="runs/protocol_analysis_agent_api")
    query: str


class ProtocolCompareRequest(BaseModel):
    output_dir: str = Field(default="runs/protocol_analysis_agent_api")
    protocol_id_a: str
    protocol_id_b: str
    max_features: int = 40


class ProtocolChatRequest(BaseModel):
    output_dir: str = Field(default="runs/protocol_analysis_agent_api")
    question: str
    max_features: int = 30
    llm: dict[str, Any] = Field(default_factory=lambda: {"provider": "offline"})


def create_app() -> Any:
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise RuntimeError(
            "FastAPI is required for the backend API. Install project dependencies with `pip install -e .`."
        ) from exc

    app = FastAPI(
        title="ProtocolOptimizationAgent Backend API",
        version="0.1.0",
        description="Backend API boundary between the frontend UI and LangGraph-powered protocol agents.",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/protocol-analysis/run")
    def run_protocol_analysis(request: ProtocolAnalysisRunRequest) -> dict[str, Any]:
        allowed_statuses = ["evidence_supported"]
        if request.allow_flagged_events:
            allowed_statuses.append("has_flags")

        loaded = load_protocol_analysis_inputs(Path(request.input_dir))
        agent = ProtocolAnalysisAgent(
            output_dir=Path(request.output_dir),
            quality_filters={
                "min_protocol_confidence": request.min_confidence,
                "require_comparison_ready": True,
                "allowed_event_validation_statuses": allowed_statuses,
                "reject_unparsed_numeric_fields": True,
                "require_evidence_quote_for_key_factors": True,
                "max_llm_normalization_events": request.max_llm_normalization_events,
                "max_llm_semantic_stages": request.max_llm_semantic_stages,
                "max_llm_feature_discovery_protocols": request.max_llm_feature_discovery_protocols,
                "allow_schema_invalid_events": request.allow_schema_invalid_events,
                "base_feature_registry_path": request.base_feature_registry_path,
                "approved_feature_registry_path": request.approved_feature_registry_path,
                "run_specific_feature_registry_path": request.run_specific_feature_registry_path,
            },
            llm_client=build_llm_client(request.llm),
        )
        outputs = agent.run(
            protocol_records=loaded["protocol_records"],
            factor_events=loaded["factor_events"],
            source_identity_rows=loaded["source_identity_rows"],
            protocol_database_path=loaded["protocol_database_path"],
        )
        return {
            "status": "ok",
            "execution_engine": "langgraph",
            "protocol_record_source": loaded["protocol_record_source"],
            "outputs": {key: str(path) for key, path in outputs.items()},
        }

    @app.post("/protocol-query/find")
    def find_protocols(request: ProtocolFindRequest) -> dict[str, Any]:
        return {"status": "ok", "results": ProtocolQueryEngine(Path(request.output_dir)).find_protocols(request.query)}

    @app.post("/protocol-query/compare")
    def compare_protocols(request: ProtocolCompareRequest) -> dict[str, Any]:
        result = ProtocolQueryEngine(Path(request.output_dir)).compare_protocols(
            request.protocol_id_a,
            request.protocol_id_b,
            max_features=request.max_features,
        )
        return result

    @app.post("/protocol-query/chat")
    def chat_protocol_query(request: ProtocolChatRequest) -> dict[str, Any]:
        return ProtocolChatAgent(Path(request.output_dir), llm_client=build_llm_client(request.llm)).answer(
            request.question,
            max_features=request.max_features,
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ProtocolOptimizationAgent backend API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "uvicorn is required to run the backend API. Install project dependencies with `pip install -e .`."
        ) from exc

    uvicorn.run(
        "protocol_optimization_agent.api:create_app", host=args.host, port=args.port, reload=args.reload, factory=True
    )


if __name__ == "__main__":
    main()
