"""Legacy CLI ingest job.

Fetches unread CMIR emails and runs each one through the LangGraph
workflow, pausing on interrupt() calls for missing fields / approval and
resuming with Command(resume=...) once a human has answered on the console.

Also owns the observability lifecycle for each run: creates the
agent_runs row, updates its status as the run pauses/resumes/finishes/
fails, and logs every human answer to hitl_actions. Per-node timing lives
in app/core/tracing.py instead - this file only cares about run-level and
human-decision-level events, which is exactly the information it already
has on hand.
"""

from __future__ import annotations

import getpass
from dataclasses import asdict
from uuid import uuid4

from langgraph.types import Command

from app.core.container import Container
from app.schemas.cmir import EmailMessage

INTERRUPT_KEY = "__interrupt__"

_STATUS_BY_REASON = {
    "missing_mandatory_fields": "waiting_missing_fields",
    "approval_required": "waiting_approval",
}

_FINAL_STATUS_BY_DECISION = {
    "approve": "completed_approved",
    "reject": "completed_rejected",
}


def _thread_config(email: EmailMessage) -> dict:
    # One LangGraph "thread" per email keeps their interrupt/resume cycles
    # independent of each other.
    return {"configurable": {"thread_id": f"email-{email.imap_id}"}}


def _resolve_answer(reason: str, payload: dict, container: Container) -> dict:
    if reason == "missing_mandatory_fields":
        return container.human_review.request_missing_fields(payload)
    if reason == "approval_required":
        return container.human_review.request_approval(payload)
    raise ValueError(f"Unknown interrupt reason: {reason}")


def process_email(email: EmailMessage, container: Container) -> None:
    thread_config = _thread_config(email)
    thread_id = thread_config["configurable"]["thread_id"]
    batch_id = f"cli_batch_{uuid4().hex[:12]}"

    run_id = container.agent_runs.start(batch_id=batch_id, thread_id=thread_id)

    try:
        state = container.graph.invoke(
            {
                "batch_id": batch_id,
                "email": asdict(email),
                "cmir": {},
                "decision": None,
                "run_id": run_id,
                "thread_id": thread_id,
            },
            config=thread_config,
        )

        # persist_email is the first node, so by the time this first
        # invoke() returns (paused or not), state["email_id"] is set.
        if state.get("email_id") is not None:
            container.agent_runs.update_status(run_id, "running", email_id=state["email_id"])

        while state.get(INTERRUPT_KEY):
            interrupted = state[INTERRUPT_KEY][0]
            payload = interrupted.value
            reason = payload["reason"]

            container.agent_runs.update_status(
                run_id, _STATUS_BY_REASON.get(reason, "running"), current_node=reason
            )

            answer = _resolve_answer(reason, payload, container)

            container.hitl_actions.log(
                run_id=run_id,
                batch_id=batch_id,
                email_id=state.get("email_id"),
                interrupt_type=reason,
                question=payload,
                answer=answer,
                actor=getpass.getuser(),
                decision=answer.get("decision"),
                reason=answer.get("reason"),
            )

            state = container.graph.invoke(Command(resume=answer), config=thread_config)

        final_status = _FINAL_STATUS_BY_DECISION.get(state.get("decision"), "completed")
        container.agent_runs.update_status(run_id, final_status, completed=True)

    except Exception as exc:
        container.agent_runs.update_status(run_id, "failed", error=str(exc))
        raise

    print(f"Finished '{email.subject}' -> status: {state['cmir'].get('status')}")


def main() -> None:
    container = Container.build()

    emails = container.email_reader.fetch_unread()
    if not emails:
        print("No unread CMIR emails found.")
        return

    print(f"Found {len(emails)} CMIR email(s).\n")

    for email in emails:
        print(f"Processing: {email.subject}")
        try:
            process_email(email, container)
        except Exception as exc:  # noqa: BLE001 - top-level guard per email
            print(f"Failed to process '{email.subject}': {exc}")
        print("-" * 60)

    print("\nProcessing completed.")


if __name__ == "__main__":
    main()
