"""In-memory job store for the async compress-demo pattern.

WHY ASYNC SUBMIT+POLL, NOT A SYNCHRONOUS POST
----------------------------------------------
We measured Paritok's hosted GPU cold-starting for minutes at a time (RunPod
serverless spin-up) during earlier replay runs. A synchronous HTTP handler that
blocks on that call would exceed the request timeout of almost any free-tier
host, including Render's. So `/api/compress` returns a `job_id` immediately, the
actual compression runs in a background thread, and the frontend polls
`/api/compress/{job_id}` every ~1.5s until it's done.

Threads, not asyncio tasks, because `GpuServerStrategy.compress()` is a
synchronous, blocking call (httpx sync client) — wrapping it in a thread keeps
the event loop free to serve other requests (including other clients' polls)
while a compression is in flight.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

# Jobs older than this are evicted on the next submission, so memory does not
# grow unbounded across a long-running demo session.
_JOB_TTL_S = 15 * 60


@dataclass
class Job:
    id: str
    status: str = "queued"          # queued -> running -> done | error
    created_at: float = field(default_factory=time.time)
    result: dict | None = None
    error: str | None = None


class JobStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def _evict_stale(self, now: float) -> None:
        stale = [jid for jid, j in self._jobs.items() if now - j.created_at > _JOB_TTL_S]
        for jid in stale:
            del self._jobs[jid]

    def submit(self, work: Callable[[], dict]) -> str:
        """Start `work` in a background thread; return a job id immediately."""
        job_id = uuid.uuid4().hex[:16]
        job = Job(id=job_id)
        now = time.time()
        with self._lock:
            self._evict_stale(now)
            self._jobs[job_id] = job

        def runner():
            with self._lock:
                job.status = "running"
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
                with self._lock:
                    job.status = "error"
                    job.error = f"{type(exc).__name__}: {exc}"
            else:
                with self._lock:
                    job.status = "done"
                    job.result = result

        threading.Thread(target=runner, daemon=True).start()
        return job_id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
