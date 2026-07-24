# Results: does intent/kind routing beat Paritok's stock defaults?

**Short answer: no, not reliably.** We built the control plane, a reproducible
measurement harness, and a drift benchmark, and measured it honestly. `kind`
routing has no effect; `intent` routing shows a weak, inconsistent improvement in
must-keep retention that does not reach reliability at our sample size and does
not change task-relevant retention. We do not claim a win.

This document reports what we measured, including two numbers we got excited
about early and then had to retract when the data was cleaned up. The method —
measure, don't assume — is the point.

---

## What we tested

SEGPILOT routes the two parameters Paritok's hosted GPU actually honours
(`query`/intent and `kind`; see [findings.md](findings.md)) per segment, instead
of Paritok's stock behaviour (one global intent from `_extract_query`, no kind).
The hypothesis: this helps most on **long, drifted sessions**, where the agent's
current sub-task no longer matches the original instruction.

Five arms isolate the contribution of each layer:

| arm | kind | intent |
|---|---|---|
| `stock` | none | global (`_extract_query`) |
| `kind_only` | tool-identity | global |
| `intent_only` | none | per-segment |
| `segpilot` | tool-identity | per-segment |
| `segpilot+guard` | tool-identity | per-segment + retention guard |

Two populations, reported separately:

- **single-bug** (bug_01–04): short sessions, one relevant file, no drift — the control.
- **campaign** (campaign_1–3): three unrelated bugs chained into one session, so
  context accumulates to 43–56K tokens and drifts across sub-tasks — the condition
  the hypothesis is about.

All compression ran on Paritok's hosted GPU. Every arm was replayed over
identical, uncompressed reference trajectories recorded under a passthrough arm,
so the comparison is not biased by having been produced under any arm being
compared.

---

## The numbers (clean)

Aggregate across all 7 sessions:

| arm | ratio | saved | must-keep ret. | task ret. | rel/1k |
|---|--:|--:|--:|--:|--:|
| `stock` | 0.490 | 51% | 83% | 92% | 0.06 |
| `kind_only` | 0.481 | 52% | 83% | 92% | 0.06 |
| `intent_only` | 0.483 | 52% | 92% | 92% | 0.06 |
| `segpilot` | 0.460 | 54% | 92% | 92% | 0.06 |
| `segpilot+guard` | 0.646 | 35% | 98% | 95% | 0.04 |

Campaign sessions only (the drift test), must-keep retention per session:

| arm | campaign_1 | campaign_2 | campaign_3 | mean |
|---|--:|--:|--:|--:|
| `stock` | 67% | 96% | 87% | 83% |
| `intent_only` | 96% | 87% | 93% | 92% |
| `segpilot` | 95% | 87% | 93% | 92% |

Campaign compression ratio (lower = harder), per session:

| arm | campaign_1 | campaign_2 | campaign_3 | mean |
|---|--:|--:|--:|--:|
| `stock` | 0.399 | 0.548 | 0.496 | 0.481 |
| `segpilot` | 0.368 | 0.405 | 0.551 | 0.441 |

---

## What this says, honestly

1. **`kind` routing does nothing.** `kind_only` equals `stock` on every metric
   in every session (must-keep 83% vs 83%, ratio 0.481 vs 0.490). Tool-identity
   kind labelling is more *correct* than Paritok's mislabelling (finding #9), but
   on these workloads it does not change the compressed output. The whole effect,
   such as it is, comes from `intent`.

2. **`intent` routing shows a weak, inconsistent effect.** Across the three drift
   sessions, intent-aware compression retains **+9pp** must-keep content on average
   (92% vs 83%) at slightly harder compression (0.441 vs 0.481). But it is
   **inconsistent**: it wins on campaign_1 (+28pp) and campaign_3 (+6pp) and
   **loses on campaign_2 (−9pp)**. The average is carried by one session's
   outlier. On compression ratio it is likewise mixed — harder on two campaigns,
   softer on one.

3. **Task-relevant retention does not move.** Within every session, all arms
   score the same task retention (the ground-truth `must_appear` spans survive
   regardless of arm). So `rel/1k` is flat (~1.0–1.06×) and reflects only how
   hard each arm happened to compress, not a quality-adjusted win.

4. **The guard works as a safety mechanism.** `segpilot+guard` reaches the
   highest must-keep retention (98%) by restoring segments that lost too much,
   at a real compression cost (ratio 0.646). It does what it is for.

**Verdict: we cannot claim intent/kind routing reliably beats Paritok's stock
defaults, even under drift.** At N=3 campaigns, with an effect that reverses on
one of three sessions, no movement in task-relevant retention, and no live
solve-rate confirmation, the honest conclusion is a null-to-weak result.

---

## Two retractions

We reported two encouraging numbers before the data was clean. Both were wrong,
and both are kept here rather than deleted:

- **"1.61× rel/1k on the drift session."** An artefact of a cache poisoned during
  a hosted-GPU outage: several segments passed through uncompressed and were
  cached as if compressed. Fixed by never caching a passthrough (see
  `segpilot/compressor.py`, `compress_segment`) and re-running to a clean pass.
- **"67% → 95% must-keep, a clean win."** Real for campaign_1, but it did not
  replicate — campaign_2 showed the opposite direction. It was session-specific,
  not a reliable effect.

The lesson is exactly the one the [findings](findings.md) document opens with:
run the measurement, do not trust the first number.

---

## What stands

Independent of the null headline, the following are solid and reproducible:

- **Nine measured findings** about Paritok's hosted API and internals
  ([findings.md](findings.md)), including the silently-ignored `level`, the
  level-blind cache, the unauthenticated endpoint, and the disagreeing kind
  classifiers — each with a runnable reproduction.
- **A reproducible measurement harness** (`segpilot/probe.py`,
  `segpilot/replay/`) that anyone can point at Paritok to re-derive these numbers.
- **A clean layer attribution**: on these workloads the effect is entirely
  `intent`, not `kind`.
- **A working retention guard** built on Paritok's own must-keep span definition.

## Reproduce

```bash
python -m segpilot.replay.harness --all --suffix __raw \
  --arms stock,kind_only,intent_only,segpilot,segpilot+guard
python -m segpilot.replay.report --out examples/reports/
```

Sample size: 3 campaign + 4 single-bug sessions, one recording each. Live
solve-rate A/B was not run — with an offline signal this weak and inconsistent,
a 3-task solve-rate comparison would be noise. Larger-N and live confirmation are
the obvious next step for anyone extending this.
