"""JobStore: submit + poll, background execution, error surfacing."""

import time

from web.backend.jobs import JobStore


def _wait_until_done(store, job_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get(job_id)
        if job.status in ("done", "error"):
            return job
        time.sleep(0.01)
    raise TimeoutError(f"job {job_id} did not finish in {timeout}s")


def test_submit_returns_immediately_with_a_job_id():
    store = JobStore()
    t0 = time.time()
    job_id = store.submit(lambda: (time.sleep(0.2), {"ok": True})[1])
    elapsed = time.time() - t0
    assert isinstance(job_id, str) and job_id
    assert elapsed < 0.1          # did not block on the 0.2s work


def test_job_completes_with_result():
    store = JobStore()
    job_id = store.submit(lambda: {"value": 42})
    job = _wait_until_done(store, job_id)
    assert job.status == "done"
    assert job.result == {"value": 42}


def test_job_surfaces_exceptions_as_error_status():
    def boom():
        raise ValueError("bad input")

    store = JobStore()
    job_id = store.submit(boom)
    job = _wait_until_done(store, job_id)
    assert job.status == "error"
    assert "bad input" in job.error


def test_unknown_job_id_returns_none():
    store = JobStore()
    assert store.get("does-not-exist") is None


def test_job_ids_are_unique():
    store = JobStore()
    ids = {store.submit(lambda: {}) for _ in range(20)}
    assert len(ids) == 20
