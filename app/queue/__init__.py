"""Queue abstraction for durable job state and asynchronous dispatch.

This package holds two unrelated systems that happen to share a folder:

1. The generic durable job queue used by both fine_projection and
   fine_mitigation -- reached via `get_job_dispatcher`/`build_job_queue`,
   backed by `postgres.py` and/or `service_bus.py`. Postgres `job_item`
   rows are the source of truth for work state regardless of the
   configured dispatch backend; the backend determines only how workers
   are notified that work is available. Concrete backends are selected by
   `build_job_queue`; callers depend only on the `JobDispatcher` and
   `JobSource` protocols.
2. `cmir_mail_producer.py` -- the cmir-only mail notification channel
   (`ServiceBusMailQueue`), used solely by `Container`, the cmir/
   po_validation composition root. It shares no code or interface with
   the job-queue system above.
"""

from app.queue.factory import build_job_queue
from app.queue.interfaces import JobDispatcher, JobSource
from app.queue.types import ClaimedJob

__all__ = [
    "ClaimedJob",
    "JobDispatcher",
    "JobSource",
    "build_job_queue"
]
