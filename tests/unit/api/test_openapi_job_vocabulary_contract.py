"""Public-API-contract guard for the job-vocabulary centralization.

`app/schemas/batches.py` and `app/schemas/fine_projection/summaries.py` moved their
`task_type`/`status` fields from `Literal["A", "B", ...]` annotations to
the shared `StrEnum` types in `app/models/enums.py`. That change is
implementation-internal, but the actual public contract two things
outside this codebase can observe -- the JSON response bodies clients
parse, and the OpenAPI schema's declared set of permitted values -- must
be unaffected:

- JSON responses: `StrEnum` members ARE `str` instances, so FastAPI's
  JSON encoder serializes them to the exact same plain string it always
  did for a `Literal` field (never a namespaced repr like
  `"JobTaskType.ORDER_RUN"`).
- OpenAPI schema: Pydantic v2 represents an `Enum`-typed field as a
  `$ref` to a named component schema (`JobTaskType`, `JobItemStatus`,
  `SummaryStatus`) rather than inlining `"enum": [...]` on the field the
  way it did for `Literal` -- that structural difference is expected and
  fine (tooling that generates from OpenAPI, e.g. openapi-generator,
  handles both forms identically). What must NOT differ is the actual set
  of permitted string values a client can rely on, once the `$ref` is
  resolved.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI

from app.models.enums import JobItemStatus, JobRunType, JobTaskType, SummaryStatus
from app.schemas.batches import BatchItemResponse, BatchStatusCounts
from app.schemas.fine_projection.summaries import ProjectionSummaryStatusResponse


def _resolve_enum_values(openapi: dict, field_schema: dict) -> set[str]:
    """Resolve a field's schema node to its permitted enum-value set,
    whether expressed as a bare `$ref` (what Pydantic v2 emits for a
    required enum field, confirmed against this app's actual schema) or
    wrapped in `allOf` (what Pydantic emits when a `$ref` field also
    carries a title/description/default) -- resilient to either shape
    rather than pinned to today's exact rendering."""
    node = field_schema
    if "$ref" in node:
        ref_name = node["$ref"].rsplit("/", 1)[-1]
        node = openapi["components"]["schemas"][ref_name]
    elif "allOf" in node:
        ref_name = node["allOf"][0]["$ref"].rsplit("/", 1)[-1]
        node = openapi["components"]["schemas"][ref_name]
    return set(node["enum"])


def test_batch_item_response_openapi_enum_values_unchanged(app: FastAPI) -> None:
    schema = app.openapi()
    properties = schema["components"]["schemas"]["BatchItemResponse"]["properties"]

    assert _resolve_enum_values(schema, properties["task_type"]) == {
        "ORDER_RUN",
        "PROJECTION_SUMMARY_REGEN",
        "MITIGATION_SUMMARY_REGEN",
    }
    assert _resolve_enum_values(schema, properties["status"]) == {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "DEAD",
    }


def test_batch_run_request_status_query_param_openapi_enum_values_unchanged(app: FastAPI) -> None:
    schema = app.openapi()
    items_get = schema["paths"]["/api/v1/batches/{job_run_id}/items"]["get"]
    status_param = next(p for p in items_get["parameters"] if p["name"] == "status")

    # Optional query param: Pydantic/FastAPI renders `T | None` as anyOf.
    ref_schema = next(s for s in status_param["schema"]["anyOf"] if "$ref" in s)
    assert _resolve_enum_values(schema, ref_schema) == {"PENDING", "RUNNING", "SUCCEEDED", "DEAD"}


def test_fine_projection_summary_status_response_openapi_enum_values_unchanged(app: FastAPI) -> None:
    schema = app.openapi()
    properties = schema["components"]["schemas"]["ProjectionSummaryStatusResponse"]["properties"]

    assert _resolve_enum_values(schema, properties["status"]) == {"PENDING", "READY", "FAILED"}


def test_batch_item_response_serializes_task_type_and_status_as_plain_strings() -> None:
    """StrEnum must serialize identically to how the old `Literal` field
    did: a bare JSON string, never a namespaced enum repr."""
    resp = BatchItemResponse(
        id=UUID(int=0),
        order_id="ORD-1",
        projection_date="2026-08-15",
        task_type=JobTaskType.ORDER_RUN,
        status=JobItemStatus.PENDING,
        attempt_count=0,
        max_attempts=5,
        created_at="2026-08-15T00:00:00",
        updated_at="2026-08-15T00:00:00",
    )

    dumped = resp.model_dump(mode="json")
    assert dumped["task_type"] == "ORDER_RUN"
    assert dumped["status"] == "PENDING"
    assert isinstance(dumped["task_type"], str)
    assert isinstance(dumped["status"], str)
    assert '"task_type":"ORDER_RUN"' in resp.model_dump_json().replace(" ", "")
    assert '"status":"PENDING"' in resp.model_dump_json().replace(" ", "")


def test_fine_projection_summary_status_response_serializes_status_as_plain_string() -> None:
    resp = ProjectionSummaryStatusResponse(
        order_id="ORD-1",
        as_of_date="2026-08-15",
        prompt_version="v1",
        status=SummaryStatus.PENDING,
    )

    dumped = resp.model_dump(mode="json")
    assert dumped["status"] == "PENDING"
    assert isinstance(dumped["status"], str)
    assert '"status":"PENDING"' in resp.model_dump_json().replace(" ", "")


def test_job_run_type_values_unchanged() -> None:
    """`JobRunType` has no dedicated response field (job_run.run_type is
    never returned over the API today), but it feeds the CheckConstraint
    DDL (see app/models/job_queue.py) -- pinning its value set here
    guards against a silent drift there too."""
    assert {member.value for member in JobRunType} == {"SCHEDULED_DAILY", "MANUAL_BATCH", "ON_DEMAND"}


def test_batch_status_counts_fields_match_job_item_status_members() -> None:
    # Catches a new JobItemStatus member that nobody mirrored onto this hand-maintained schema.
    assert set(BatchStatusCounts.model_fields) == {m.value for m in JobItemStatus}
