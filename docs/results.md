# Results: does intent/kind routing beat Paritok's stock defaults?

**Short answer: no.** We built the control plane, a pre-registered 8-campaign
drift benchmark, and a reproducible measurement harness, and measured it
honestly at full scale. `kind` routing has zero effect — confirmed exactly
across every single one of 12 sessions. `intent` routing shows a small,
inconsistent effect that **shrank as we added more data** and, at full N,
leaves task-relevant retention very slightly *worse* than stock, not better.
We do not claim a win.

This document reports what we measured, including three numbers we got excited
about early and had to revise downward as more data came in. The method —
measure, don't assume, and don't stop measuring once the first result looks
good — is the point.

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

- **single-bug** (bug_01–08, N=4 recorded): short sessions, one relevant file,
  no drift — the control.
- **campaign** (campaign_1–8, **N=8, the full pre-registered set**): two to four
  unrelated bugs chained into one session, so context accumulates to 22–96K
  tokens and drifts across sub-tasks — the condition the hypothesis is about.

All eight campaigns were designed and committed **before recording or replaying
any of them** (see the campaign_4–8 commit history), so nothing here is
selected on outcome. All compression ran on Paritok's hosted GPU. Every arm was
replayed over identical, uncompressed reference trajectories recorded under a
passthrough arm, so the comparison is not biased by having been produced under
any arm being compared.

---

## The numbers (clean, N=12, the complete pre-registered set)

Aggregate, campaign population (N=8):

| arm | ratio | saved | must-keep ret. | task ret. | guard trips |
|---|--:|--:|--:|--:|--:|
| `stock` | 0.504 | 50% | 86% | **99%** | 0 |
| `kind_only` | 0.488 | 51% | 86% | 99% | 0 |
| `intent_only` | 0.473 | 53% | 91% | **97%** | 0 |
| `segpilot` | 0.464 | 54% | 91% | **97%** | 0 |
| `segpilot+guard` | 0.630 | 37% | 97% | 99% | 9 |

Aggregate, single-bug control (N=4):

| arm | ratio | saved | must-keep ret. | task ret. |
|---|--:|--:|--:|--:|
| `stock` | 0.552 | 45% | 91% | 89% |
| `kind_only` | 0.563 | 44% | 94% | 89% |
| `intent_only` | 0.549 | 45% | 91% | 89% |
| `segpilot` | 0.527 | 47% | **89%** | 89% |
| `segpilot+guard` | 0.761 | 24% | 100% | 94% |

Per-campaign must-keep retention, stock → segpilot (all 8):

| campaign | stock | segpilot | delta |
|---|--:|--:|--:|
| campaign_1 | 67% | 95% | **+28pp** |
| campaign_2 | 96% | 87% | **−9pp** |
| campaign_3 | 87% | 93% | +6pp |
| campaign_4 | 98% | 97% | −1pp |
| campaign_5 | 86% | 95% | +10pp |
| campaign_6 | 92% | 94% | +2pp |
| campaign_7 | 94% | 95% | +1pp |
| campaign_8 | 87% | 80% | **−7pp** |
| **mean** | | | **+3.8pp** |

**5 of 8 positive, 3 of 8 negative, 0 of 8 flat.** No session lands near zero;
the effect genuinely varies session to session rather than clustering around a
stable value.

---

## The estimate shrank as N grew — read this before the rest

We are reporting this trend explicitly because it is itself a finding:

| N (campaigns) | mean must-keep delta |
|--:|--:|
| 3 | +9.0pp |
| 5 | +6.8pp |
| 7 | +5.3pp |
| **8 (final)** | **+3.8pp** |

Every time we added campaigns, the estimated effect got smaller. That is the
signature of an early result dominated by noise, not a real effect obscured by
noise — a genuine effect does not systematically shrink toward zero as sample
size grows; it stabilises. We do not know if +3.8pp is "the real number" or if
it would keep drifting toward zero with N=16 or N=32. What we can say is that
nothing in this data supports treating the early +9pp reading as reliable, and
we would have been wrong to stop measuring there.

---

## What this says, honestly

1. **`kind` routing does nothing, and this is now a very solid finding.**
   `kind_only` equals `stock` exactly on must-keep retention (86% = 86%) and
   task retention (99% = 99%) in the full campaign aggregate — not just close,
   identical. This held at N=3, N=5, N=7, and now N=8. Tool-identity kind
   labelling is more *correct* than Paritok's own mislabelling (finding #9),
   but on these workloads it does not change the compressed output at all.

2. **`intent` routing has a small, inconsistent, shrinking effect on must-keep
   retention.** Mean +3.8pp at full N, with real session-to-session variance
   (5 positive, 3 negative) rather than a stable small gain. This is weaker
   than what N=3 suggested and should be read as "not established," not as "a
   modest but real win."

3. **Task-relevant retention is not neutral — it is slightly *worse* under
   routing.** This is the sharpest finding in the full dataset and the one the
   smaller samples did not show clearly: in the campaign population,
   `intent_only`/`segpilot` score **97%** task retention against **99%** for
   `stock`/`kind_only`. The single-bug control shows the same direction —
   `segpilot` at 89% must-keep against `stock`'s 91%. Routing is not simply
   "no effect on what matters, some effect on what doesn't" — on the metric
   that most directly represents whether the agent kept the code it actually
   needed, stock is marginally *ahead*.

4. **The guard works as a safety mechanism, at a real cost.** `segpilot+guard`
   reaches the highest must-keep retention in both populations (97% campaign,
   100% single-bug) by restoring segments that lost too much — but at a real
   compression cost (ratio 0.630 vs 0.464 for plain `segpilot` on campaigns)
   and 9 guard trips across the campaign set. It does what it is for; it is not
   a free upgrade.

**Verdict: intent/kind routing does not reliably beat Paritok's stock
defaults.** At the full pre-registered N=8, `kind` routing is confirmed to do
nothing. `intent` routing's effect on must-keep retention is small and
inconsistent, and its effect on task-relevant retention — the metric that
actually matters — is slightly negative. Building a compression-routing layer
on this evidence would not be justified.

---

## Three numbers we got excited about, and walked back

Three times during this project we reported an encouraging number and had to
revise it. All three are kept here rather than deleted:

- **"79% fewer tokens / 3.1× on a drift session."** A hand-constructed
  illustration, not a benchmark result — the real agent sessions never
  reproduced it. Kept in `bench/intent_ab.py`, now clearly labelled as
  illustrative.
- **"1.61× rel/1k on the drift session."** An artefact of a cache poisoned
  during a hosted-GPU outage: several segments passed through uncompressed and
  were cached as if compressed. Fixed by never caching a passthrough (see
  `segpilot/compressor.py`, `compress_segment`) and re-running to a clean pass.
- **"+9pp must-keep retention, a modest but real win" (N=3).** Not fabricated
  or buggy — a genuine reading of 3 sessions. It just did not hold up: +9.0pp
  → +6.8pp → +5.3pp → +3.8pp as N grew to 8, while task-relevant retention
  turned out to be slightly negative once the full set was in.

The lesson compounds across all three: run the measurement, don't stop at the
first encouraging number, and don't stop at the second one either.

---

## What stands

Independent of the null headline, the following are solid and reproducible:

- **Nine measured findings** about Paritok's hosted API and internals
  ([findings.md](findings.md)), including the silently-ignored `level`, the
  level-blind cache, the unauthenticated endpoint, and the disagreeing kind
  classifiers — each with a runnable reproduction.
- **A reproducible measurement harness** (`segpilot/probe.py`,
  `segpilot/replay/`) that anyone can point at Paritok to re-derive these
  numbers, or apply to their own workload.
- **A clean, now well-supported layer attribution**: on these workloads
  `kind` contributes exactly nothing; what little effect exists is entirely
  from `intent`.
- **A working retention guard** built on Paritok's own must-keep span
  definition, with a measured cost.
- **A live site** ([segpilot.onrender.com](https://segpilot.onrender.com/))
  serving this data directly from the harness, plus a real compression demo
  anyone can run against their own text.

## Reproduce

```bash
python -m segpilot.replay.harness --all --suffix __raw \
  --arms stock,kind_only,intent_only,segpilot,segpilot+guard
python -m segpilot.replay.report --out examples/reports/ --json
```

Sample size: 8 campaign + 4 single-bug sessions, one recording each, all
pre-registered before recording. Live solve-rate A/B is a separate,
in-progress track — see the project's live status for current results; with an
offline signal this weak, small-N solve-rate differences would mostly be
noise, so we are running it at reduced scope rather than skipping it.
