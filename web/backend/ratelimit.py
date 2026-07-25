"""In-memory rate limiting for the public compress demo.

WHY THIS EXISTS
---------------
`/api/compress` spends the project's Paritok API quota on every call. It is the
only public-facing endpoint that costs money/quota, so it gets defense in depth:
a per-IP sliding window (stops one visitor from hammering it) plus a global daily
cap (stops the aggregate from draining the quota even if many different IPs hit
it, e.g. if the demo goes viral or gets scraped).

In-memory is a deliberate choice, not a shortcut: this runs as a single Render
free-tier instance with no horizontal scaling, so there is no cross-process state
to reconcile. State resets on redeploy, which is acceptable for a hackathon demo
and errs toward availability rather than needing Redis for a problem this small.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RateLimitDecision:
    allowed: bool
    reason: str = ""
    retry_after_s: float = 0.0


class RateLimiter:
    """Per-IP sliding window + a global daily cap.

    `per_ip_max` calls allowed within `per_ip_window_s` seconds, per IP.
    `daily_max` calls allowed globally per rolling 24h window.
    """

    def __init__(
        self,
        *,
        per_ip_max: int = 5,
        per_ip_window_s: float = 60.0,
        daily_max: int = 200,
    ):
        self.per_ip_max = per_ip_max
        self.per_ip_window_s = per_ip_window_s
        self.daily_max = daily_max
        self._lock = threading.Lock()
        self._by_ip: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()

    def _prune(self, times: deque[float], window_s: float, now: float) -> None:
        while times and now - times[0] > window_s:
            times.popleft()

    def check(self, ip: str) -> RateLimitDecision:
        now = time.time()
        with self._lock:
            self._prune(self._global, 86400.0, now)
            if len(self._global) >= self.daily_max:
                retry = 86400.0 - (now - self._global[0])
                return RateLimitDecision(
                    False, "daily quota reached for this demo — try again tomorrow", retry
                )

            bucket = self._by_ip.setdefault(ip, deque())
            self._prune(bucket, self.per_ip_window_s, now)
            if len(bucket) >= self.per_ip_max:
                retry = self.per_ip_window_s - (now - bucket[0])
                return RateLimitDecision(
                    False, f"rate limit: max {self.per_ip_max} per {int(self.per_ip_window_s)}s", retry
                )

            bucket.append(now)
            self._global.append(now)
            return RateLimitDecision(True)

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            self._prune(self._global, 86400.0, now)
            return {
                "calls_today": len(self._global),
                "daily_max": self.daily_max,
                "tracked_ips": len(self._by_ip),
            }
