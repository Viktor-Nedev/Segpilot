"""JSON report export: single source of truth for the live dashboard."""

import json

from segpilot.replay.harness import SessionReplay
from segpilot.replay.json_report import build_json_report, write_json_report
from segpilot.replay.metrics import ArmMetrics


def _m(arm, ct=100, tk=9, tt=10, mk=90, mt=100):
    return ArmMetrics(
        arm=arm, original_tokens=200, compressed_tokens=ct,
        mustkeep_kept=mk, mustkeep_total=mt, task_kept=tk, task_total=tt, sessions=1,
    )


def _replays():
    arms = ["stock", "segpilot"]
    camp = SessionReplay("campaign_1", "raw",
                         {"stock": _m("stock", ct=140), "segpilot": _m("segpilot", ct=90)})
    single = SessionReplay("bug_02", "raw",
                           {"stock": _m("stock", ct=110), "segpilot": _m("segpilot", ct=105)})
    return arms, [camp, single]


def test_json_report_has_both_populations_and_combined():
    arms, replays = _replays()
    from segpilot.replay.harness import aggregate
    totals = aggregate(replays, arms)
    doc = build_json_report(totals, replays, arms)

    assert "campaign" in doc["populations"]
    assert "single-bug" in doc["populations"]
    assert doc["populations"]["campaign"]["session_count"] == 1
    assert len(doc["aggregate_all"]) == 2


def test_json_report_includes_findings_and_corrections():
    arms, replays = _replays()
    from segpilot.replay.harness import aggregate
    totals = aggregate(replays, arms)
    doc = build_json_report(totals, replays, arms)

    assert len(doc["findings"]) == 9
    assert len(doc["corrections"]) == 2
    assert all("was" in c and "why" in c for c in doc["corrections"])
    assert "verdict" in doc and "not reliably" in doc["verdict"]


def test_json_report_per_task_matches_replays():
    arms, replays = _replays()
    from segpilot.replay.harness import aggregate
    totals = aggregate(replays, arms)
    doc = build_json_report(totals, replays, arms)

    task_ids = {t["task_id"] for t in doc["per_task"]}
    assert task_ids == {"campaign_1", "bug_02"}
    campaign_row = next(t for t in doc["per_task"] if t["task_id"] == "campaign_1")
    assert campaign_row["population"] == "campaign"


def test_json_report_solve_rate_optional():
    arms, replays = _replays()
    from segpilot.replay.harness import aggregate
    totals = aggregate(replays, arms)
    doc = build_json_report(totals, replays, arms, solve_rate={"stock": (3, 8), "segpilot": (5, 8)})

    assert doc["solve_rate"]["segpilot"]["rate"] == 0.625
    assert doc["solve_rate"]["stock"]["solved"] == 3


def test_write_json_report_produces_valid_json_file(tmp_path):
    arms, replays = _replays()
    from segpilot.replay.harness import aggregate
    totals = aggregate(replays, arms)
    out = tmp_path / "sub" / "results.json"
    path = write_json_report(totals, replays, arms, out_path=out)

    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["generated_from_sessions"] == 2
