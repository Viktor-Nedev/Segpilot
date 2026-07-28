# Devpost — text description (ready to paste)

**Project URL:** https://segpilot.onrender.com/ — live dashboard + a real
compression demo running on Paritok's hosted GPU.
**Fallback URL** (in case Render's free tier is asleep or down during judging):
https://claude.ai/code/artifact/c4b25561-75f6-4434-8466-5fe9f92a158f — a static
mirror of the same findings/results, always available.
**Repository:** https://github.com/Viktor-Nedev/Paritok_Hackathon
**Paritok account email:** viktornedev08@gmail.com

> Note: the live site is a Render free-tier instance. It sleeps after ~15 min
> idle and takes ~30–50s to wake on the first request — if it looks slow,
> that's why, not a bug. It self-warmed to ~4s response times in our own check.

---

## SEGPILOT — is routing Paritok compression per-segment worth it? We measured.

### What it is

Paritok's compressor is steered by two parameters the hosted GPU actually reads:
`query` (the agent's intent) and `kind` (what a segment is). Stock Paritok sends
one global intent per request and no kind at all. The obvious optimization is to
route both **per segment**, so every tool result is compressed for what the agent
is doing at that moment — most useful on long sessions where the current sub-task
has drifted away from the original instruction.

SEGPILOT is that optimization **plus the thing most "we optimized X" projects
skip: an honest measurement of whether it actually helps.** It's a kind engine, a
phase-aware intent engine, a retention guard, a five-arm benchmark, and a
reproducible replay harness — built so the claim could be checked, not just
asserted.

It's also a **live site** (segpilot.onrender.com), not just a writeup: a
dashboard that always reflects the latest committed measurement (served from
the same JSON the replay harness produces, never hand-typed), and a real
compression demo — paste your own code, get two real Paritok hosted-GPU
compressions back (baseline vs SEGPILOT-routed) and watch the intent lever
work, or not, on your own text.

### What we found

We ran the real agent on the full **pre-registered set of 8 drifted multi-bug
"campaigns"** (2 to 4 unrelated bugs chained per session, up to 96K tokens of
accumulated context) plus 4 single-bug controls, and replayed all five arms
over identical trajectories. All 8 campaigns were designed and committed
*before* recording or replaying any of them, so nothing here is selected on
outcome. The honest result:

- **`kind` routing changes nothing** — `kind_only` equals stock *exactly* on
  every metric, confirmed across all 8 campaigns.
- **`intent` routing's effect shrank as we added data** — +9.0pp must-keep
  retention at N=3 campaigns, down to +3.8pp at the full N=8 (5 of 8 positive,
  3 of 8 negative). That shrinkage is itself the finding: a real effect
  stabilises as N grows; this one didn't.
- **Task-relevant retention ends up slightly *worse* under routing** — 97% vs
  99% for stock, in both populations. Not neutral, negative.

**We cannot claim intent/kind routing reliably beats Paritok's stock defaults —
if anything, on the metric that matters most, it's a small net negative.**
That's a genuinely useful thing to know — measured, not assumed at N=3 and
then left alone — and it saves the next person a dead-end optimization.

### The real contribution: nine measured findings

Building this surfaced nine reproducible findings about Paritok's hosted API,
each with a runnable check (see `docs/findings.md`). Highlights:

- The hosted GPU **silently ignores `level`** — and accepts `level="BANANA"` with
  HTTP 200 — while the self-hosted path honours it. `use_gpu_server` is documented
  as "the only switch that matters"; it silently changes compression semantics.
- The compression **cache is blind to `kind`/`level`**, which collapses any
  per-segment policy — it blocks Paritok's own roadmap item.
- The hosted endpoint **does not validate API keys**: real, garbage, and absent
  keys all return identical 200s, so a typo'd key produces zero dashboard usage
  with no way to detect it.
- The two `kind` classifiers **disagree**, and the one the middleware uses
  mislabels every multi-line file read as `log_output`.
- The documented `tools/view_trace.py` **doesn't exist**, so we wrote one and
  included it (`segpilot/viewtrace.py`).

### Where Paritok's hosted GPU did the work

Every compression in the project — the lever probe, all five arms across every
session, the trace demo — ran on Paritok's hosted GPU, attributed to the account
key. The whole measurement (which parameters work, how hard each arm compresses,
how much must-keep content survives) is Paritok output. The hosted GPU made it
possible to run ~hundreds of compressions without a local model.

### Honesty as a feature

We reported two encouraging numbers during development and retracted both — a
hand-constructed "79% / 3.1×" example that didn't reproduce, and a "1.61×" that
was a cache-poisoning artefact during a GPU outage. Both are kept in the writeup
(`docs/results.md`), because the entire project is an argument for measuring
instead of trusting the first number.

### Tech

Python. Paritok hosted GPU for compression; Gemini 2.5 Flash (OpenAI-compatible
endpoint) as the agent's upstream LLM. A small real coding agent, an 8-bug +
3-campaign self-verifying benchmark, an offline replay harness with a
Markdown+SVG report, 39 tests. One command reproduces every number; the reference
sessions are committed so no re-recording is needed.

Built with Paritok — https://github.com/Paritok-official/paritok-4b-v1
