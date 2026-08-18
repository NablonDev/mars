"""Queue abstraction for durable job state and asynchronous dispatch.

Postgres `job_item` rows are the source of truth for work state regardless
of the configured dispatch backend. The backend determines only how workers
are notified that work is available.

Concrete backends are selected by `build_job_queue`; callers depend only on
the `JobDispatcher` and `JobSource` protocols.
"""

from app.queue.factory import build_job_queue
from app.queue.interfaces import JobDispatcher, JobSource
from app.queue.types import ClaimedJob

__all__ = ["ClaimedJob", "JobDispatcher", "JobSource", "build_job_queue"]
