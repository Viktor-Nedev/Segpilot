"""A/B the intent engine against stock Paritok's `_extract_query`.

    python -m bench.intent_ab

THE SCENARIO
------------
A long agent session that has drifted, which is the normal case rather than a
contrived one. The user's original instruction was to add rate limiting. Forty
turns later the agent is debugging a failing session-expiry test and reading
`src/auth.py`.

Stock Paritok compresses that file against the *original instruction*, because
`_extract_query` returns the user's task text and applies it to every segment.
SEGPILOT compresses it against what the agent is actually doing.

WHAT WE MEASURE
---------------
Tokens alone would be a misleading score: an intent that makes the compressor
throw everything away wins on tokens and loses in reality. So we measure both:

  * tokens          -- cost
  * task retention  -- did the code the agent is about to need survive?

Task retention is the honest metric here. It counts must-keep spans drawn from
the *currently relevant* region of the file (the session-expiry logic), not the
whole file, because keeping irrelevant code is exactly what we want to stop
paying for.
"""

from __future__ import annotations

import sys

from paritok.strategies.gpu_server import GpuServerStrategy
from paritok.token_counter import count_tokens

from segpilot.config import SegpilotConfig
from segpilot.policy.guard import score_retention
from segpilot.policy.intent import build_intent, stock_intent

# The file the agent is reading. Two unrelated concerns live in it: rate
# limiting (the old task) and session expiry (the current one).
AUTH_PY = '''\
import time
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RateLimitBucket:
    """Token bucket used by the API rate limiter."""
    capacity: int
    refill_per_second: float
    tokens: float
    last_refill: float


def consume_rate_limit_token(bucket, now=None):
    """Consume one token from the rate-limit bucket."""
    now = now or time.time()
    elapsed = now - bucket.last_refill
    bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_per_second)
    bucket.last_refill = now
    if bucket.tokens < 1:
        return False
    bucket.tokens -= 1
    return True


def build_rate_limit_bucket(capacity, refill_per_second):
    """Construct a fresh rate-limit bucket."""
    return RateLimitBucket(capacity, refill_per_second, float(capacity), time.time())


def compute_session_expiry(issued_at, ttl_seconds, clock_skew_seconds=0):
    """Return the absolute expiry timestamp for a session.

    BUG: clock_skew_seconds is subtracted instead of added, so sessions expire
    earlier than intended whenever skew compensation is configured.
    """
    logger.debug("computing expiry issued_at=%s ttl=%s", issued_at, ttl_seconds)
    return issued_at + ttl_seconds - clock_skew_seconds


def is_session_expired(session, now=None):
    """True when the session's expiry timestamp has passed."""
    now = now or time.time()
    expiry = compute_session_expiry(
        session.issued_at, session.ttl_seconds, session.clock_skew_seconds
    )
    logger.debug("session %s expiry=%s now=%s", session.id, expiry, now)
    return now >= expiry


def refresh_session(session, session_store, now=None):
    """Extend a session that has not yet expired."""
    if is_session_expired(session, now):
        raise SessionExpiredError("cannot refresh an expired session")
    session.issued_at = now or time.time()
    session_store.persist(session)
    return session
'''

# The region the agent actually needs right now.
RELEVANT_REGION = AUTH_PY[AUTH_PY.index("def compute_session_expiry"):]

ORIGINAL_INSTRUCTION = "Add rate limiting to all public API endpoints"

# A drifted session: the standing goal is stale, the recent turns are about
# session expiry.
MESSAGES = [
    {"role": "user", "content": ORIGINAL_INSTRUCTION},
    {"role": "assistant", "content": "I'll start by adding a token-bucket rate limiter."},
    {"role": "assistant", "content": "Rate limiting is wired into the middleware now. Running the test suite."},
    {"role": "user", "content": "<system-reminder>tests failed</system-reminder>"},
    {"role": "assistant", "content": (
        "test_session_refresh_near_expiry is failing. Sessions look like they expire "
        "early when clock skew compensation is on. Let me read the expiry logic in src/auth.py."
    )},
]

TOOL_NAME = "Read"
TOOL_ARGS = {"file_path": "src/auth.py"}


def _hr(t: str) -> None:
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def main() -> int:
    cfg = SegpilotConfig.load()
    strategy = GpuServerStrategy(cfg.to_paritok_config().gpu_server)
    available, message = strategy.check()
    if not available:
        print(f"hosted GPU unavailable: {message}")
        return 1

    base = count_tokens(AUTH_PY)

    stock = stock_intent(MESSAGES)
    pilot = build_intent(MESSAGES, tool_name=TOOL_NAME, tool_args=TOOL_ARGS)

    _hr("Intents")
    print(f"   stock    [{stock.source}]\n      {stock.text!r}\n")
    print(f"   segpilot [{pilot.source}]\n      {pilot.text!r}")

    _hr(f"Compressing src/auth.py ({base} tokens) with each intent")
    rows = []
    for label, intent in (("stock", stock), ("segpilot", pilot)):
        body = strategy.compress(AUTH_PY, query=intent.text, kind="file_read", level="L1")
        tok = count_tokens(body)
        # Retention over the whole file tells us how much was kept overall;
        # retention over the relevant region tells us whether the *right* part
        # was kept. The second is the one that matters.
        whole = score_retention(AUTH_PY, body, kind="file_read")
        relevant = score_retention(RELEVANT_REGION, body, kind="file_read")
        has_bug = "clock_skew_seconds" in body and "compute_session_expiry" in body
        rows.append((label, tok, whole, relevant, has_bug, body))
        print(
            f"\n   {label:<9} {tok:>4} tok  (ratio {tok / base:.3f})"
            f"   whole-file retention {whole.retention:>5.0%}"
            f"   relevant-region retention {relevant.retention:>5.0%}"
        )
        print(f"   {'':<9} buggy function survived: {'YES' if has_bug else 'NO'}")

    _hr("Verdict")
    (_, s_tok, _, s_rel, s_bug, _), (_, p_tok, _, p_rel, p_bug, _) = rows
    tok_delta = (s_tok - p_tok) / s_tok if s_tok else 0.0

    # Raw retention is not comparable across unequal compression: an intent that
    # compresses almost nothing trivially retains almost everything. Stock's
    # ratio here is ~0.77, i.e. it barely compressed. So we also report
    # relevance density -- relevant content retained per token spent -- which is
    # what an engineer paying per token actually cares about.
    s_density = s_rel.retention / s_tok if s_tok else 0.0
    p_density = p_rel.retention / p_tok if p_tok else 0.0

    print(f"   tokens                    : {s_tok} -> {p_tok}  ({tok_delta:+.1%} cheaper)")
    print(f"   compression ratio         : {s_tok / base:.3f} -> {p_tok / base:.3f}")
    print(f"   relevant-region retention : {s_rel.retention:.0%} -> {p_rel.retention:.0%}")
    print(f"   relevance per 100 tokens  : {s_density * 100:.2f} -> {p_density * 100:.2f}"
          f"  ({p_density / s_density:.1f}x)" if s_density else "")
    print(f"   task-critical code kept   : {s_bug} -> {p_bug}")
    print()

    # The gate that matters: did the agent keep what it needs to finish the job?
    # Everything else is an efficiency question downstream of that.
    if not p_bug:
        print("   FAIL: SEGPILOT discarded the task-critical code. Nothing else")
        print("   about this run is worth quoting -- fix the intent first.")
    elif p_density > s_density and p_tok < s_tok:
        print("   SEGPILOT wins: task-critical code preserved, "
              f"{tok_delta:.0%} fewer tokens,")
        print(f"   and {p_density / s_density:.1f}x more relevant content per token spent.")
        print()
        print("   Note stock did not really compress here (ratio "
              f"{s_tok / base:.2f}) -- its high raw")
        print("   retention is an artefact of keeping the file nearly intact, not")
        print("   of choosing better. That is the honest reading of these numbers.")
    elif p_tok < s_tok:
        print("   SEGPILOT is cheaper and kept the task-critical code, but its")
        print("   relevance density did not improve. Report both numbers.")
    else:
        print("   SEGPILOT did NOT win on this sample. Do not ship a claim built")
        print("   on it -- investigate before publishing any numbers.")

    _hr("Compressed bodies")
    for label, _, _, _, _, body in rows:
        print(f"\n--- {label} ---\n{body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
