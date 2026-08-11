from fastapi import APIRouter, Depends

from app.api.dependencies import get_explanation_service
from app.schemas.explanations import ExplainProjectionRequest, ExplanationResponse
from app.services.explanation_service import ExplanationService

router = APIRouter(tags=["explanations"])


@router.post("/orders/{order_id}/explanation", response_model=ExplanationResponse)
def explain_projection(
    order_id: str,
    body: ExplainProjectionRequest,
    explanation_service: ExplanationService = Depends(get_explanation_service),
) -> ExplanationResponse:
    # POST, not GET: generating a fresh explanation isn't free -- it can
    # cost an LLM call and write an audit row -- matching the existing
    # POST /projections/run precedent rather than the read-only
    # GET /orders/{id}/projections.
    #
    # No try/except: every failure mode here is an `AppError` mapped once in
    # app/core/exceptions.py::register_exception_handlers --
    # `OrderNotFoundError` 404, `NoProjectionExistsError` and
    # `InvalidAsOfDateError` 422, `ToolLoopExhaustedError` 502 (whose raw
    # Azure OpenAI SDK text stays in `detail`, logged but never returned).
    result = explanation_service.explain_order(
        order_id, as_of_date=body.as_of_date, force_regenerate=body.force_regenerate
    )
    return ExplanationResponse.model_validate(result)
