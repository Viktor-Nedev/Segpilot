"""Rate limiter: per-IP window + global daily cap."""

import time

from web.backend.ratelimit import RateLimiter


def test_allows_up_to_per_ip_max():
    rl = RateLimiter(per_ip_max=3, per_ip_window_s=60, daily_max=1000)
    for _ in range(3):
        assert rl.check("1.2.3.4").allowed
    assert rl.check("1.2.3.4").allowed is False


def test_different_ips_have_independent_buckets():
    rl = RateLimiter(per_ip_max=1, per_ip_window_s=60, daily_max=1000)
    assert rl.check("1.1.1.1").allowed
    assert rl.check("2.2.2.2").allowed          # different IP, not blocked
    assert rl.check("1.1.1.1").allowed is False  # same IP, blocked


def test_per_ip_window_expires():
    rl = RateLimiter(per_ip_max=1, per_ip_window_s=0.05, daily_max=1000)
    assert rl.check("1.1.1.1").allowed
    assert rl.check("1.1.1.1").allowed is False
    time.sleep(0.06)
    assert rl.check("1.1.1.1").allowed is True


def test_daily_cap_blocks_regardless_of_ip():
    rl = RateLimiter(per_ip_max=100, per_ip_window_s=60, daily_max=2)
    assert rl.check("1.1.1.1").allowed
    assert rl.check("2.2.2.2").allowed
    assert rl.check("3.3.3.3").allowed is False   # global cap hit, new IP still blocked


def test_denied_decision_carries_reason_and_retry_after():
    rl = RateLimiter(per_ip_max=1, per_ip_window_s=30, daily_max=1000)
    rl.check("1.1.1.1")
    d = rl.check("1.1.1.1")
    assert d.allowed is False
    assert "rate limit" in d.reason
    assert 0 < d.retry_after_s <= 30


def test_stats_reports_calls_today():
    rl = RateLimiter(per_ip_max=100, per_ip_window_s=60, daily_max=1000)
    rl.check("1.1.1.1")
    rl.check("2.2.2.2")
    assert rl.stats()["calls_today"] == 2
