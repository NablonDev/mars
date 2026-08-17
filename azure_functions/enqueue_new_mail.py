from __future__ import annotations

import logging

import azure.functions as func

from app.core.container import Container

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
        logging.warning("enqueue_new_mail timer is past due")

    container = Container.build()
    rows = container.email_repository.claim_new_for_queue(
        limit=container.config.service_bus.enqueue_batch_limit
    )
    if not rows:
        logging.info("No new email rows to enqueue")
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
            logging.info("=" * 80)
            logging.info(
                            "Service Bus Namespace : %s",
                            container.config.service_bus.fully_qualified_namespace,
                        )
            logging.info(
                            "Service Bus Queue     : %s",
                            container.config.service_bus.queue_name,
                        )
            logging.info(
                            "Session ID            : %s",
                            repr(container.config.service_bus.session_id),
                        )
            logging.info(
                            "Has Connection String : %s",
                            bool(container.config.service_bus.connection_string),
                        )

            if container.config.service_bus.connection_string:
                            logging.info(
                                "Connection String    : %s...",
                                container.config.service_bus.connection_string[:70],
                            )

            logging.info("Message ID            : %s", message_id)
            logging.info("Payload               : %s", payload)
            logging.info("=" * 80)

            container.service_bus_queue.send(
                            payload,
                            message_id=message_id,
                        )

            container.email_repository.mark_queued(
                            row["id"],
                            message_id,
                        )

            logging.info("Queued email_id=%s to Service Bus", message_id)



        except Exception as exc:
            logging.exception("Failed enqueueing email_id=%s", message_id)

            container.email_repository.mark_queue_failed(
                row["id"],
                str(exc),
                retryable=True,
            )