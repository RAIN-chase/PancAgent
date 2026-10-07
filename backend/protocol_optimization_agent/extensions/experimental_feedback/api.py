"""Explicitly enabled wet-lab endpoints; reuse the API's authenticated store resolver."""

from collections.abc import Callable
from typing import Any

from protocol_optimization_agent.extensions.experimental_feedback import lifecycle
from protocol_optimization_agent.interfaces.api import ProjectRequest
from protocol_optimization_agent.science.models import ExperimentFeedback, FeedbackAdmission
from protocol_optimization_agent.science.state import ScientificWorldModel


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


def register_routes(app: Any, request_store: Callable[[ProjectRequest], ScientificWorldModel]) -> None:
    @app.post("/scientific/experiments/review")
    def review_experiment(request: ExperimentReviewRequest):
        store = request_store(request)
        return lifecycle.review(store, request.experiment_id, request.approve, store.context.user_id,
                                request.reason, evaluation_id=request.evaluation_id)

    @app.post("/scientific/experiments/performed")
    def performed_experiment(request: ExperimentPerformedRequest):
        store = request_store(request)
        return lifecycle.mark_performed(store, request.experiment_id, store.context.user_id)

    @app.post("/scientific/experiments/feedback")
    def record_feedback(request: ExperimentFeedbackRequest):
        store = request_store(request)
        if request.feedback.researcher != store.context.user_id:
            raise PermissionError("Feedback researcher must match request identity.")
        return lifecycle.record_feedback(store, request.feedback)

    @app.post("/scientific/experiments/admit-feedback")
    def admit_feedback(request: FeedbackAdmissionRequest):
        store = request_store(request)
        if request.admission.researcher != store.context.user_id:
            raise PermissionError("Admission researcher must match request identity.")
        return lifecycle.admit_feedback(store, request.admission)
