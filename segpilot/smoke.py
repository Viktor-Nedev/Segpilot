"""Phase-0 smoke test: prove the hosted GPU path works, and reproduce the
level-aware cache bug that SEGPILOT has to work around.

Run:
    python -m segpilot.smoke

This does three things:

1. Health-checks Paritok's hosted GPU endpoint.
2. Confirms an API key is present, because the endpoint answers unauthenticated
   requests and work done without a key produces no dashboard usage at all.
3. Compresses one segment at L0 and again at L3 and compares the results.

Step 3 is the interesting one. `CompressionPipeline` caches on
`content_hash(content)`, which is a SHA256 of the content and nothing else --
not the level, not the kind. So the second call returns the first call's output
regardless of how aggressively you asked it to compress. That silently defeats
any per-segment level policy, which is why SEGPILOT keeps its own level-aware
cache instead of relying on Paritok's.

See docs/findings.md finding #2.
"""

from __future__ import annotations

import argparse
import sys

from segpilot.config import SegpilotConfig
from segpilot.policy.guard import score_retention, strip_ref_tag

# A segment with an obvious signal-to-noise split: the function body and its
# identifiers matter, the debug prints and TODO chatter do not.
SAMPLE = '''\
def calculate_order_total(items, discount_policy):
    """Compute the order total, applying any active discount policy."""
    subtotal = 0
    for item in items:
        subtotal += item.unit_price * item.quantity
    # TODO(alex): revisit this once the new pricing service lands
    # print("DEBUG subtotal:", subtotal)
    # print("DEBUG items:", items)
    if discount_policy is None:
        return subtotal
    # NOTE: legacy behaviour, kept for the 2019 migration. Remove after Q3.
    discount = discount_policy.compute_discount(subtotal)
    if discount > subtotal:
        raise ValueError("discount exceeds subtotal")
    return subtotal - discount


def apply_tax(total, rate):
    # print("DEBUG applying tax", rate)
    if rate < 0:
        raise ValueError("negative tax rate")
    return total * (1 + rate)
'''


def _hr(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEGPILOT phase-0 smoke test")
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="proceed without PARITOK_API_KEY (usage will NOT be attributed)",
    )
    args = parser.parse_args(argv)

    cfg = SegpilotConfig.load()

    # ---- 1. hosted GPU health -------------------------------------------
    _hr("1. Paritok hosted GPU")
    from paritok.strategies.gpu_server import GpuServerStrategy

    pcfg = cfg.to_paritok_config()
    available, message = GpuServerStrategy(pcfg.gpu_server).check()
    print(f"   endpoint : {cfg.paritok.base_url}")
    print(f"   available: {available}")
    print(f"   message  : {message}")
    if not available:
        print("\n   FAIL: hosted GPU unreachable. Compression would silently pass")
        print("   content through uncompressed, so no savings would be produced.")
        return 1

    # ---- 2. API key attribution -----------------------------------------
    _hr("2. API key attribution")
    if cfg.paritok.api_key:
        masked = cfg.paritok.api_key[:7] + "..." + cfg.paritok.api_key[-4:]
        print(f"   PARITOK_API_KEY present ({masked}) -- usage will be attributed.")
    else:
        print("   PARITOK_API_KEY is NOT set.")
        print("   The hosted endpoint still answers, but the work is attributed to")
        print("   nobody, so your Paritok dashboard will show zero usage.")
        if not args.allow_unauthenticated:
            print("\n   FAIL: refusing to run unattributed. Set PARITOK_API_KEY, or")
            print("   pass --allow-unauthenticated if you really meant to.")
            return 1
        print("   Continuing anyway (--allow-unauthenticated).")

    # ---- 3. compress, and expose the level-blind cache -------------------
    _hr("3. Compression at L0 vs L3 (same content, same pipeline)")
    from paritok.pipelines.compress import CompressionPipeline

    pipeline = CompressionPipeline(pcfg)
    query = "find the pricing bug in the order total calculation"

    results = {}
    for level in ("L0", "L3"):
        res = pipeline.compress(SAMPLE, query=query, level=level, kind="file_read")
        body = strip_ref_tag(res.compressed)
        rep = score_retention(SAMPLE, res.compressed, kind="file_read")
        results[level] = body
        print(
            f"   {level}: {res.original_tokens:>4} -> {res.compressed_tokens:<4} tok "
            f"(ratio {1 - res.ratio:.3f})   retention {rep.summary()}"
        )

    _hr("4. Finding #2 -- is the compression cache level-aware?")
    if results["L0"] == results["L3"]:
        print("   REPRODUCED: L0 and L3 returned byte-identical output.")
        print()
        print("   The second call never reached the model. CompressionPipeline")
        print("   keyed its cache on content_hash(content) alone (paritok/storage.py),")
        print("   so `level` and `kind` are invisible to it. Any per-segment level")
        print("   policy is silently collapsed to whatever level ran first.")
        print()
        print("   -> SEGPILOT therefore keys its own cache on (content, kind, level).")
        print("   -> Reported upstream as finding #2 (see docs/findings.md).")
    else:
        print("   NOT reproduced here: L0 and L3 differ, so the cache distinguished")
        print("   them. Upstream may have fixed this -- re-check docs/findings.md")
        print("   before publishing the claim.")
        print(f"\n   L0 body ({len(results['L0'])} chars):\n   {results['L0'][:200]}")
        print(f"\n   L3 body ({len(results['L3'])} chars):\n   {results['L3'][:200]}")

    _hr("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
