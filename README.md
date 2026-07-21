[![Built with Paritok](https://img.shields.io/badge/Built%20with-Paritok-1f2d3d)](https://github.com/Paritok-official/paritok-4b-v1)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue)](./LICENSE)

# SEGPILOT

**Paritok compresses. SEGPILOT decides what to compress *for* — and proves it was safe.**

Built with [Paritok](https://github.com/Paritok-official/paritok-4b-v1), running on
Paritok's hosted GPU server.

---

## The one-paragraph version

Paritok's compressor is **intent-conditioned** — its system prompt says *"USER
INTENT — the agent's current task. Drives keep/drop."* We measured how much that
matters and it is the single largest lever in the system: on one 263-token file,
changing only the intent moved output from 160 tokens to 36, and decided *which*
function survived. But stock Paritok derives one intent per request — the user's
**original instruction** — and applies it to every segment. Forty turns into a
session, that instruction no longer describes what the agent is doing, so the
compressor confidently keeps the wrong code. SEGPILOT computes a per-segment,
phase-aware intent from what the agent is actually doing right now. On a drifted
session that is **79% fewer tokens with the task-critical code still intact**.

## What we measured

We swept every steering parameter Paritok exposes against the hosted GPU,
holding content constant and varying one parameter at a time. Two work, two are
silently discarded:

| Parameter | Honoured on hosted GPU? | Measured effect |
|---|---|---|
| `query` (intent) | **Yes — dominant** | 160 → 36 tokens (4.4×); decides which code survives |
| `kind` | **Yes** | 206 / 187 / 15 tokens across `file_read` / `log_output` / `assistant_thinking` |
| `level` (L0–L3) | **No** | Byte-identical for L0–L3, `null`, absent, and `"BANANA"` |
| `target_ratio` | **No** | Byte-identical for `0.5`, `0.2`, `"10%"`, `null` |

Reproduce it yourself:

```bash
python -m segpilot.probe --all
```

This table is why SEGPILOT is built on `query` and `kind` rather than on the
documented-but-inert `level`. The full write-up, including a correction to our
own initial hypothesis, is in **[docs/findings.md](docs/findings.md)**.

## The headline result

`python -m bench.intent_ab` — a session where the user asked for rate limiting,
but forty turns later the agent is debugging a session-expiry test:

| | stock intent | SEGPILOT intent |
|---|---|---|
| intent used | `"Add rate limiting to all public API endpoints"` | `"test_session_refresh_near_expiry is failing. Sessions look like they expire early when clock skew compensation is on…"` |
| tokens | 333 | **71** (−79%) |
| compression ratio | 0.767 | **0.164** |
| relevant-region retention | 100% | 67% |
| **relevance per 100 tokens** | 0.30 | **0.94 (3.1×)** |
| task-critical code kept | yes | **yes** |

Read that last block honestly: stock's 100% retention is an artefact of *barely
compressing at all* (ratio 0.77), not of choosing better. The meaningful
comparison is relevant content retained per token spent, where SEGPILOT is 3.1×
better while keeping the code the agent needed. Our benchmark prints that caveat
itself rather than quoting the flattering number alone.

## How it works

```
Agent  ──►  SEGPILOT  ──►  Paritok hosted GPU  ──►  billed LLM
                │              (compression)
                ├─ kind engine      conversation-aware `kind`
                ├─ intent engine    per-segment, phase-aware intent   ◄── the core
                ├─ retention guard  must-keep spans must survive
                └─ regret loop      expand_context → learn
```

**Kind engine.** Paritok ships `tagger.tag_messages()`, a conversation-aware
labeller its own docstring recommends for runtime use. Nothing imports it. On
the hosted path the consequence is concrete: `gpu_server.py` has no
`classify_kind_from_content` fallback, so every tool result is compressed with
`kind: null` — one of only two parameters the server honours.

**Intent engine.** The core. Derives intent from recent assistant reasoning
(what the agent is trying to find out), qualified by the tool call that produced
the segment (where it is looking). See below for what we got wrong here first.

**Retention guard.** Scores every compression with `find_must_keep_spans` —
Paritok's *own* training-time definition of content that must survive, vendored
in `segpilot/vendor/mustkeep.py` because it is not shipped to PyPI. Using their
definition rather than one of ours means the metric was not tuned in our favour.

**Regret loop.** Every `expand_context` call is a labelled negative example: the
compressor dropped something the model then had to ask for. That signal is free,
real-time, and currently unused. SEGPILOT feeds it back into intent selection.

## What we got wrong

Our first intent implementation ranked the tool call highest and prefixed every
intent with the original instruction:

    "Add rate limiting to all public API endpoints — currently reading src/auth.py"

It scored **worse than stock**: relevant-region retention fell 100% → 0% and the
function under debug was destroyed. Two mistakes: a file path says *where* but
not *what*, and leading with a stale goal steers the compressor to confidently
discard the right code. A stale intent is worse than no intent — an empty intent
compresses conservatively, a wrong one is confidently wrong.

We also initially concluded from reading the source that stock compresses
everything at `L0`. Both halves were wrong: `DEFAULT_LEVEL` is `L1`, and on the
hosted path `level` is ignored entirely. We only caught it by running the sweep
instead of trusting the read.

Both corrections are kept in the repo rather than quietly fixed, because the
method — measure, don't assume — is the point.

## Setup

Requires Python 3.11+ and a Paritok API key from
[paritok.com](https://www.paritok.com) → dashboard → API keys.

```bash
pip install -e .
export PARITOK_API_KEY=pk_live_...     # required; see below
export GEMINI_API_KEY=...              # upstream LLM

python -m segpilot.smoke               # verify hosted GPU + reproduce finding 2
python -m segpilot.probe --all         # reproduce the lever table
python -m bench.intent_ab              # reproduce the headline result
```

> **The API key is not optional.** Paritok's hosted endpoint answers
> unauthenticated requests, so it is easy to do all your work and produce **zero
> dashboard usage**. SEGPILOT refuses to start without a key rather than let that
> happen silently. (finding 3)

## Findings contributed upstream

Eight findings, each reproducible from this repo, are written up in
[docs/findings.md](docs/findings.md) and filed upstream:

1. Hosted GPU silently ignores `level` — and accepts `"BANANA"` with HTTP 200
2. Compression cache is blind to `kind`/`level`, collapsing any per-segment policy
3. Hosted endpoint serves unauthenticated requests → silently unattributed usage
4. Intent dominates quality, but stock passes one stale intent per request
5. `tag_messages`/`assign_level` are dead code; hosted path sends `kind: null`
6. Proxy hardcodes `/v1`, breaking Gemini/OpenRouter/Groq-style base URLs
7. Documented `tools/view_trace.py` does not exist (we wrote one)
8. `shadow_storage: "redis"` validates but is unimplemented → silent data loss

## Credits

Built with **[Paritok](https://github.com/Paritok-official/paritok-4b-v1)** —
the open-source compression model for coding agents, by Jiayu Shi and Luzhuo
Chen. `segpilot/vendor/mustkeep.py` is vendored from the Paritok repository
under Apache 2.0.

## License

Apache 2.0 — see [LICENSE](./LICENSE).
