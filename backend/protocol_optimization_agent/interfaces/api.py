from __future__ import annotations

import argparse
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.agents.protocol_chat import ProtocolChatAgent
from protocol_optimization_agent.analysis.protocol.input_loader import (
    PROTOCOL_INPUT_FILENAME,
    load_protocol_analysis_inputs,
)
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.extensions.experimental_feedback import lifecycle
from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode, forbid_client_paths
from protocol_optimization_agent.runtime.archive import RunArchive, fingerprint
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.runtime.llm_client import build_llm_client
from protocol_optimization_agent.science.models import ExperimentFeedback, FeedbackAdmission
from protocol_optimization_agent.science.state import ScientificWorldModel

_request_identity: ContextVar[str | None] = ContextVar("request_identity", default=None)
_request_paths: ContextVar[dict[str, Path] | None] = ContextVar("request_paths", default=None)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = "default"
    output_artifact_id: str = ""


def _request_path(request: ProjectRequest, field: str, permission: str = "read") -> Path:
    paths = _request_paths.get() or {}
    if field in paths:
        return paths[field]
    if not authenticated_mode():
        return Path(getattr(request, field))
    if field in request.model_fields_set:
        raise PermissionError("Client paths are disabled; submit an authorized artifact ID.")
    policy = AccessPolicy()
    identity = _request_identity.get()
    if not identity:
        raise PermissionError("Authenticated request identity is required.")
    artifact_id = getattr(request, "input_artifact_id", "") if field == "input_dir" else request.output_artifact_id
    return policy.artifact(identity, request.project_id, artifact_id, permission)


def archived_request(function):
    @wraps(function)
    def run(request):
        identity = _request_identity.get() if authenticated_mode() else None
        context = RunContext.from_values(user_id=identity, project_id=request.project_id)
        archive = RunArchive(context)
        archive.start({"entrypoint": f"api.{function.__name__}", "request": request.model_dump(mode="json")})
        token = None
        try:
            with archive.scope(), archive.operation(function.__name__, request.model_dump(mode="json")) as call_id:
                paths = {}
                if function.__name__ == "run_protocol_analysis":
                    # Validate the requested destination, but only write into a fresh server run.
                    _request_path(request, "output_dir", "write")
                    source = _request_path(request, "input_dir") / PROTOCOL_INPUT_FILENAME
                    if not source.is_file():
                        raise FileNotFoundError(f"Required formal protocol input is missing: {source}")
                    identity = fingerprint(source)
                    archive.inputs({"protocols_jsonl": str(source)})
                    paths["input_dir"] = archive.root / "inputs" / identity["sha256"]
                    paths["output_dir"] = archive.root / "protocol_analysis"
                else:
                    paths["output_dir"] = archive.seed(_request_path(request, "output_dir"), "protocol_analysis")
                token = _request_paths.set(paths)
                result = function(request)
                result.update(
                    {
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "run_manifest": str(archive.root / "run_manifest.json"),
                    }
                )
                archive.result(call_id, result)
            archive.finish(result)
            return result
        except BaseException as exc:
            archive.finish({"error": str(exc)}, status="failed")
            raise
        finally:
            if token is not None:
                _request_paths.reset(token)

    # FastAPI resolves annotations in the wrapper's module.
    run.__annotations__ = function.__annotations__
    return run


def _request_store(request: ProjectRequest) -> ScientificWorldModel:
    identity = _request_identity.get() if authenticated_mode() else None
    context = RunContext.from_values(user_id=identity, project_id=request.project_id)
    return ScientificWorldModel(context, request.project_id)


class ProtocolAnalysisRunRequest(ProjectRequest):
    input_artifact_id: str = ""
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


class ProtocolFindRequest(ProjectRequest):
    output_dir: str = Field(default="runs/protocol_analysis_agent_api")
    query: str


class ProtocolCompareRequest(ProjectRequest):
    output_dir: str = Field(default="runs/protocol_analysis_agent_api")
    protocol_id_a: str
    protocol_id_b: str
    max_features: int = 40


class ProtocolChatRequest(ProjectRequest):
    output_dir: str = Field(default="runs/protocol_analysis_agent_api")
    question: str
    max_features: int = 30
    llm: dict[str, Any] = Field(default_factory=lambda: {"provider": "offline"})


class ExperimentReviewRequest(ProjectRequest):
    experiment_id: str
    evaluation_id: str
    approve: bool
    reason: str


class ExperimentPerformedRequest(ProjectRequest):
    experiment_id: str


class ExperimentFeedbackRequest(ProjectRequest):
    feedback: ExperimentFeedback


class FeedbackAdmissionRequest(ProjectRequest):
    admission: FeedbackAdmission


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

    @app.middleware("http")
    async def authenticate_request(request: Request, call_next):
        token = None
        try:
            if authenticated_mode() and request.url.path != "/health":
                identity = AccessPolicy().authenticate(request.headers.get("authorization", ""))
                token = _request_identity.set(identity)
            return await call_next(request)
        except (PermissionError, OSError, KeyError, ValueError):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        finally:
            if token is not None:
                _request_identity.reset(token)

    @app.exception_handler(PermissionError)
    async def permission_denied(request: Request, exc: PermissionError):
        return JSONResponse({"detail": "Project, artifact or operation access denied"}, status_code=403)

    @app.exception_handler(ValueError)
    async def invalid_operation(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/protocol-analysis/run")
    @archived_request
    def run_protocol_analysis(request: ProtocolAnalysisRunRequest) -> dict[str, Any]:
        if authenticated_mode():
            forbid_client_paths(
                {key: getattr(request, key) for key in request.model_fields_set if key.endswith("_path")}
            )
            if "llm" in request.model_fields_set:
                raise PermissionError("LLM configuration is server-owned in authenticated mode.")
        allowed_statuses = ["evidence_supported"]
        if request.allow_flagged_events:
            allowed_statuses.append("has_flags")

        loaded = load_protocol_analysis_inputs(_request_path(request, "input_dir"))
        agent = ProtocolAnalysisAgent(
            output_dir=_request_path(request, "output_dir", "write"),
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
        )
        return {
            "status": "ok",
            "execution_engine": "langgraph",
            "protocol_record_source": loaded["protocol_record_source"],
            "outputs": {key: str(path) for key, path in outputs.items()},
        }

    @app.post("/protocol-query/find")
    @archived_request
    def find_protocols(request: ProtocolFindRequest) -> dict[str, Any]:
        return {
            "status": "ok",
            "results": ProtocolQueryEngine(_request_path(request, "output_dir")).find_protocols(request.query),
        }

    @app.post("/protocol-query/compare")
    @archived_request
    def compare_protocols(request: ProtocolCompareRequest) -> dict[str, Any]:
        result = ProtocolQueryEngine(_request_path(request, "output_dir")).compare_protocols(
            request.protocol_id_a,
            request.protocol_id_b,
            max_features=request.max_features,
        )
        return result

    @app.post("/protocol-query/chat")
    @archived_request
    def chat_protocol_query(request: ProtocolChatRequest) -> dict[str, Any]:
        if authenticated_mode() and "llm" in request.model_fields_set:
            raise PermissionError("LLM configuration is server-owned in authenticated mode.")
        return ProtocolChatAgent(
            _request_path(request, "output_dir", "write"), llm_client=build_llm_client(request.llm)
        ).answer(
            request.question,
            max_features=request.max_features,
        )

    @app.post("/scientific/experiments/review")
    def review_experiment(request: ExperimentReviewRequest):
        store = _request_store(request)
        return lifecycle.review(
            store,
            request.experiment_id,
            request.approve,
            store.context.user_id,
            request.reason,
            evaluation_id=request.evaluation_id,
        )

    @app.post("/scientific/experiments/performed")
    def performed_experiment(request: ExperimentPerformedRequest):
        store = _request_store(request)
        return lifecycle.mark_performed(store, request.experiment_id, store.context.user_id)

    @app.post("/scientific/experiments/feedback")
    def record_feedback(request: ExperimentFeedbackRequest):
        store = _request_store(request)
        if request.feedback.researcher != store.context.user_id:
            raise PermissionError("Feedback researcher must match request identity.")
        return lifecycle.record_feedback(store, request.feedback)

    @app.post("/scientific/experiments/admit-feedback")
    def admit_feedback(request: FeedbackAdmissionRequest):
        store = _request_store(request)
        if request.admission.researcher != store.context.user_id:
            raise PermissionError("Admission researcher must match request identity.")
        return lifecycle.admit_feedback(store, request.admission)

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
        "protocol_optimization_agent.interfaces.api:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )


if __name__ == "__main__":
    main()
