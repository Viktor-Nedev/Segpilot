"""bench.record's session-validity logic: distinguishing a genuinely killed
run from one that just happened to be interrupted, and applying different
rules for reference vs live recording. No network -- run_agent is faked."""

import json
from pathlib import Path

import bench.record as record_mod
from bench.record import record_one


def _write_session(path: Path, *, tool_result_count: int, stop_reason: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "x"}]
    for i in range(tool_result_count):
        messages.append({"role": "assistant", "content": "", "tool_calls": []})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "result"})
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "result": {"stop_reason": stop_reason}}) + "\n")
        f.write(json.dumps({"type": "messages", "messages": messages}) + "\n")


def _fake_run_agent_factory(write_fn):
    """Returns a fake standing in for agent.ruff.main: takes the CLI argv,
    writes whatever session `write_fn` describes, returns an rc."""
    def fake(argv):
        # argv contains --sessions-dir and --task/--arm; reconstruct the path
        # the same way _session_path does.
        d = dict(zip(argv[::2], argv[1::2]))
        path = Path(d["--sessions-dir"]) / f"{d['--task']}__{d['--arm']}.jsonl"
        write_fn(path)
        return 0
    return fake


def test_reference_run_with_partial_content_is_kept(tmp_path, monkeypatch):
    """A raw/reference session cut short by upstream_error, but with real
    accumulated content, is still valid -- it's the trajectory that matters,
    not a task outcome."""
    monkeypatch.setattr(record_mod, "run_agent", _fake_run_agent_factory(
        lambda p: _write_session(p, tool_result_count=7, stop_reason="upstream_error: 429")
    ))
    status = record_one("campaign_x", "raw", str(tmp_path), force=False, max_turns=40, live=False)
    assert status == "solved"   # not ratelimited/empty -- kept as real content
    assert (tmp_path / "campaign_x__raw.jsonl").exists()


def test_reference_run_with_zero_content_is_ratelimited(tmp_path, monkeypatch):
    monkeypatch.setattr(record_mod, "run_agent", _fake_run_agent_factory(
        lambda p: _write_session(p, tool_result_count=0, stop_reason="upstream_error: 429")
    ))
    status = record_one("campaign_x", "raw", str(tmp_path), force=False, max_turns=40, live=False)
    assert status == "ratelimited"
    assert not (tmp_path / "campaign_x__raw.jsonl").exists()   # cleaned up


def test_live_run_interrupted_with_partial_content_is_invalidated(tmp_path, monkeypatch):
    """The exact scenario hit recording campaign_1's live A/B: stock reached 5
    real turns before a 429. For a live (solve-rate) run this must NOT be kept
    as a legitimate outcome, unlike the reference case above."""
    monkeypatch.setattr(record_mod, "run_agent", _fake_run_agent_factory(
        lambda p: _write_session(p, tool_result_count=5, stop_reason="upstream_error: 429")
    ))
    status = record_one("campaign_1", "stock", str(tmp_path), force=False, max_turns=40, live=True)
    assert status == "ratelimited"
    assert not (tmp_path / "campaign_1__stock.jsonl").exists()


def test_live_run_that_finishes_naturally_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(record_mod, "run_agent", _fake_run_agent_factory(
        lambda p: _write_session(p, tool_result_count=9, stop_reason="agent_done")
    ))
    status = record_one("campaign_1", "segpilot", str(tmp_path), force=False, max_turns=40, live=True)
    assert status == "solved"
    assert (tmp_path / "campaign_1__segpilot.jsonl").exists()


def test_resume_skips_a_valid_existing_reference_session(tmp_path, monkeypatch):
    path = tmp_path / "campaign_x__raw.jsonl"
    _write_session(path, tool_result_count=6, stop_reason="agent_done")
    called = {"n": 0}
    monkeypatch.setattr(record_mod, "run_agent", lambda argv: called.__setitem__("n", called["n"] + 1) or 0)

    status = record_one("campaign_x", "raw", str(tmp_path), force=False, max_turns=40, live=False)
    assert status == "skipped"
    assert called["n"] == 0   # never re-ran


def test_resume_does_not_skip_an_interrupted_existing_live_session(tmp_path, monkeypatch):
    """A live session left over from a previous interrupted attempt (real
    content, but stop_reason=upstream_error) must be re-recorded on resume,
    not silently treated as done."""
    path = tmp_path / "campaign_1__stock.jsonl"
    _write_session(path, tool_result_count=5, stop_reason="upstream_error: 429")
    monkeypatch.setattr(record_mod, "run_agent", _fake_run_agent_factory(
        lambda p: _write_session(p, tool_result_count=11, stop_reason="agent_done")
    ))

    status = record_one("campaign_1", "stock", str(tmp_path), force=False, max_turns=40, live=True)
    assert status == "solved"   # re-recorded and this time finished naturally


def test_resume_skips_a_valid_existing_live_session(tmp_path, monkeypatch):
    path = tmp_path / "campaign_1__stock.jsonl"
    _write_session(path, tool_result_count=10, stop_reason="agent_done")
    called = {"n": 0}
    monkeypatch.setattr(record_mod, "run_agent", lambda argv: called.__setitem__("n", called["n"] + 1) or 0)

    status = record_one("campaign_1", "stock", str(tmp_path), force=False, max_turns=40, live=True)
    assert status == "skipped"
    assert called["n"] == 0
