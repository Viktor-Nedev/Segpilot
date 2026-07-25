"""Adaptive controller: per-kind escalate/back-off state machine.

Thresholds under test (MIN_OBSERVATIONS=5, ESCALATE_REGRET_MAX=0.05,
BACKOFF_REGRET_MIN=0.20) are the actual fixed values in adaptive.py, not
reimplemented here -- if they ever change, these tests should be the first
thing that notices.
"""

from segpilot.policy.adaptive import CONSERVATIVE, ROUTED, AdaptiveController


def test_starts_conservative_for_a_new_kind():
    c = AdaptiveController()
    assert c.should_route("file_read") is False


def test_buckets_are_independent_per_kind():
    c = AdaptiveController()
    for _ in range(10):
        c.record_compression("file_read")   # clean, no regret -> escalates
    assert c.should_route("file_read") is True
    assert c.should_route("log_output") is False   # untouched bucket stays conservative


def test_does_not_transition_before_min_observations():
    c = AdaptiveController()
    for _ in range(4):   # one short of MIN_OBSERVATIONS
        c.record_compression("file_read")
    assert c.should_route("file_read") is False


def test_escalates_after_min_observations_with_low_regret():
    c = AdaptiveController()
    for _ in range(5):
        c.record_compression("file_read")
    # 0 regrets / 5 observations = 0% <= ESCALATE_REGRET_MAX
    assert c.should_route("file_read") is True


def test_does_not_escalate_when_regret_rate_too_high():
    c = AdaptiveController()
    for _ in range(5):
        c.record_compression("file_read")
    c.record_regret("file_read")   # 1/5 = 20%, above ESCALATE_REGRET_MAX
    # note: recording regret also re-evaluates, but bucket was CONSERVATIVE
    # and never crossed the escalate bar, so it stays conservative
    assert c.should_route("file_read") is False


def test_backs_off_after_high_regret_once_routed():
    c = AdaptiveController()
    for _ in range(5):
        c.record_compression("file_read")
    assert c.should_route("file_read") is True   # escalated

    # push regret rate up past BACKOFF_REGRET_MIN (0.20) while routed
    for _ in range(3):
        c.record_regret("file_read")
    # 3 regrets / 5 observations = 60% >= BACKOFF_REGRET_MIN
    assert c.should_route("file_read") is False


def test_low_regret_after_escalation_stays_routed():
    c = AdaptiveController()
    for _ in range(20):
        c.record_compression("file_read")
    assert c.should_route("file_read") is True
    c.record_regret("file_read")   # 1/20 = 5%, at the escalate bar, below backoff
    assert c.should_route("file_read") is True


def test_snapshot_reports_level_and_counts():
    c = AdaptiveController()
    for _ in range(6):
        c.record_compression("file_read")
    c.record_regret("file_read")
    snap = c.snapshot()
    assert snap["file_read"]["observations"] == 6
    assert snap["file_read"]["regrets"] == 1
    assert snap["file_read"]["regret_rate"] == round(1 / 6, 3)
    assert snap["file_read"]["level"] in (CONSERVATIVE, ROUTED)


def test_snapshot_records_transition_audit_trail():
    c = AdaptiveController()
    for _ in range(5):
        c.record_compression("file_read")
    snap = c.snapshot()
    assert len(snap["file_read"]["transitions"]) == 1
    assert "escalate" in snap["file_read"]["transitions"][0]


def test_regret_on_unseen_kind_creates_its_own_bucket():
    c = AdaptiveController()
    c.record_regret("log_output")   # regret before any compression recorded
    snap = c.snapshot()
    assert snap["log_output"]["regrets"] == 1
    assert snap["log_output"]["observations"] == 0
    assert c.should_route("log_output") is False


def test_none_kind_falls_back_to_unknown_bucket():
    c = AdaptiveController()
    for _ in range(5):
        c.record_compression(None)
    assert "unknown" in c.snapshot()
