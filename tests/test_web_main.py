"""Integration test of the FastAPI app: submit -> poll, rate limiting, results
endpoint. The Paritok strategy is faked so this needs no network."""

import time

import pytest
from fastapi.testclient import TestClient

import web.backend.main as webmain


class FakeStrategy:
    """Deterministic 'compression': drops lines that don't mention the query,
    unless query is empty (baseline), in which case it returns the input
    unchanged -- close enough to the real honoured/ignored contrast to test
    the endpoint's plumbing without hitting the network."""

    def compress(self, content, *, query=None, kind=None, **kw):
        if not query:
            return content
        lines = [l for l in content.splitlines() if query.lower() in l.lower()]
        return "\n".join(lines) or content.splitlines()[0]

    def check(self):
        return True, "fake gpu ok"


@pytest.fixture(autouse=True)
def fake_gpu(monkeypatch):
    fake = FakeStrategy()
    monkeypatch.setattr(webmain, "_strategy", fake)
    monkeypatch.setattr(webmain, "_get_strategy", lambda: fake)
    # Fresh limiter per test so tests don't interfere with each other's quota.
    monkeypatch.setattr(webmain, "_limiter", webmain.RateLimiter(
        per_ip_max=100, per_ip_window_s=60, daily_max=1000
    ))
    monkeypatch.setattr(webmain, "_jobs", webmain.JobStore())
    yield


@pytest.fixture
def client():
    return TestClient(webmain.app)


def _wait_for_job(client, job_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/compress/{job_id}")
        body = r.json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish")


def test_health_reports_gpu_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["gpu_available"] is True


def test_compress_submit_returns_job_id(client):
    r = client.post("/api/compress", json={
        "code": "def foo(): pass\ndef bar(): pass",
        "intent": "foo",
        "kind": "file_read",
    })
    assert r.status_code == 200
    assert "job_id" in r.json()


def test_compress_job_completes_with_baseline_and_segpilot(client):
    r = client.post("/api/compress", json={
        "code": "line about foo\nline about bar\nline about baz",
        "intent": "foo",
        "kind": "file_read",
    })
    job_id = r.json()["job_id"]
    body = _wait_for_job(client, job_id)

    assert body["status"] == "done"
    result = body["result"]
    # baseline (no query) = unchanged; segpilot (query="foo") keeps only that line
    assert "bar" in result["baseline"]["text"]
    assert "bar" not in result["segpilot"]["text"]
    assert "foo" in result["segpilot"]["text"]
    assert result["segpilot"]["tokens"] <= result["baseline"]["tokens"]


def test_compress_rejects_bad_kind(client):
    r = client.post("/api/compress", json={
        "code": "x", "intent": "", "kind": "not_a_real_kind",
    })
    assert r.status_code == 400


def test_compress_rejects_oversized_code(client):
    r = client.post("/api/compress", json={
        "code": "x" * (webmain.MAX_CODE_CHARS + 1), "intent": "", "kind": "file_read",
    })
    assert r.status_code == 422   # pydantic max_length violation


def test_poll_unknown_job_is_404(client):
    r = client.get("/api/compress/does-not-exist")
    assert r.status_code == 404


def test_rate_limit_returns_429_with_reason(client, monkeypatch):
    monkeypatch.setattr(webmain, "_limiter", webmain.RateLimiter(
        per_ip_max=1, per_ip_window_s=60, daily_max=1000
    ))
    payload = {"code": "x", "intent": "", "kind": "file_read"}
    r1 = client.post("/api/compress", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/compress", json=payload)
    assert r2.status_code == 429
    assert "reason" not in r2.json() or "error" in r2.json()
    assert "retry_after_s" in r2.json()


def test_results_endpoint_404s_when_no_report_generated(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webmain, "RESULTS_JSON", tmp_path / "missing.json")
    r = client.get("/api/results")
    assert r.status_code == 404


def test_results_endpoint_serves_generated_json(client, tmp_path, monkeypatch):
    target = tmp_path / "results.json"
    target.write_text('{"hello": "world"}', encoding="utf-8")
    monkeypatch.setattr(webmain, "RESULTS_JSON", target)
    r = client.get("/api/results")
    assert r.status_code == 200
    assert r.json() == {"hello": "world"}
