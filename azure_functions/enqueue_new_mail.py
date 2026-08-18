from __future__ import annotations

import logging

import azure.functions as func

from app.core.container import Container

logger = logging.getLogger(__name__)

bp = func.Blueprint()


@bp.function_name(name="enqueue_new_mail")
@bp.timer_trigger(
    schedule="0 */1 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def enqueue_new_mail(timer: func.TimerRequest) -> None:
    """Poll PostgreSQL for new emails and enqueue them to Service Bus."""
    if timer.past_due:
        logger.warning("enqueue_new_mail timer is past due")

    container = Container.build()
    rows = container.email_repository.claim_new_for_queue(limit=container.config.queue_enqueue_batch_limit)
    if not rows:
        logger.info("No new email rows to enqueue")
        return

    for row in rows:
        message_id = str(row["id"])
        payload = {
            "batch_id": f"queue_batch_{message_id}",
            "email_id": message_id,
            "sender": row["sender"],
            "subject": row["subject"],
            "body": row["raw_content"],
            "source_message_id": row.get("source_message_id"),
            "source_imap_id": row.get("source_imap_id"),
        }

        try:
            logger.info("=" * 80)
            logger.info("Service Bus Namespace : %s", container.config.service_bus_fully_qualified_namespace)
            logger.info("Service Bus Queue     : %s", container.config.service_bus_queue_name)
            logger.info("Session ID            : %s", repr(container.config.service_bus_session_id))
            logger.info("Has Connection String : %s", bool(container.config.service_bus_connection_string))

            if container.config.service_bus_connection_string:
                logger.info(
                    "Connection String    : %s...",
                    container.config.service_bus_connection_string[:70],
                )

            logger.info("Message ID            : %s", message_id)
            logger.info("Payload               : %s", payload)
            logger.info("=" * 80)

            container.service_bus_queue.send(payload, message_id=message_id)
            container.email_repository.mark_queued(row["id"], message_id)

            logger.info("Queued email_id=%s to Service Bus", message_id)

        except Exception as exc:
            logger.exception("Failed enqueueing email_id=%s", message_id)

            container.email_repository.mark_queue_failed(
                row["id"],
                str(exc),
                retryable=True,
            )
