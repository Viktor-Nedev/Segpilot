# Findings: what we measured while building on Paritok

Every claim here was reproduced against **paritok 1.2.3** (PyPI) and the **hosted
GPU server** at `https://www.paritok.com/api`, on 2026-07-21. Each has a runnable
reproduction in this repo. Where a claim turned out to be wrong, we say so —
including one of our own early assumptions.

Reproduce everything:

```bash
python -m segpilot.smoke                 # findings 2 and 3
python -m segpilot.probe --all           # findings 1, 4, 5
```

---

## Summary: which compression levers actually work?

Paritok's compressor accepts four steering parameters. We measured each one
against the hosted GPU by holding content constant and varying only that
parameter. Two work; two are silently discarded.

| Parameter | Honoured on hosted GPU? | Measured effect |
|---|---|---|
| `query` (user intent) | **Yes — dominant** | 160 → 36 tokens (4.4×), and it decides *which* code survives |
| `kind` | **Yes** | 206 / 187 / 15 tokens across `file_read` / `log_output` / `assistant_thinking` |
| `level` (L0–L3) | **No** | Byte-identical output for L0, L1, L2, L3, `null`, absent, and `"BANANA"` |
| `target_ratio` | **No** | Byte-identical output for `0.5`, `0.2`, `"10%"`, `null` |

This table is the foundation of SEGPILOT's design: we build our policy on the
levers that demonstrably work (`query`, `kind`) rather than the one the docs
advertise (`level`).

---

## Finding 1 — The hosted GPU server silently ignores `level`

**Severity: high.** This is the finding we consider most valuable to the Paritok team.

`level` is a documented, first-class parameter. `paritok/strategies/prompts.py`
states: *"Levels L0-L3 set the target compression ratio (L0 ≤ 0.50, L1 ≤ 0.35,
L2 ≤ 0.25, L3 ≤ 0.20)."* The self-hosted path honours it — `local_model.py:212`
embeds it into the SEG prompt and `local_model.py:159-160` validates it, raising
`ValueError` on an unknown value.

The hosted path does neither. `GpuServerStrategy.compress()` puts `level` in the
POST body and the server appears to discard it:

```
level=L0        -> 206 tokens
level=L1        -> 206 tokens   (identical bytes)
level=L2        -> 206 tokens   (identical bytes)
level=L3        -> 206 tokens   (identical bytes)
level=None      -> 206 tokens   (identical bytes)
level absent    -> 206 tokens   (identical bytes)
level="BANANA"  -> 206 tokens   (identical bytes, HTTP 200)
```

That `"BANANA"` is accepted with HTTP 200 is the clearest signal: the field is
not being validated, and most likely not read.

**Why this matters beyond the parameter itself.** `use_gpu_server` is documented
as *"the only switch that matters"*, implying the two backends are behaviourally
equivalent. They are not: flipping it silently changes compression semantics.
Concretely, `middleware/wrapper.py:540-547` compresses old conversation history
with a deliberate `level="L3"`, the most aggressive setting. On the hosted path
that request is indistinguishable from `level="L0"` — Paritok's own history
compression is not compressing as intended for every hosted user.

**Suggested fix:** honour `level` server-side, or reject unknown values and
document that the hosted endpoint is level-agnostic. Either is fine; silent
divergence between backends is the problem.

---

## Finding 2 — The compression cache is blind to `kind` and `level`

**Severity: high.** Blocks Paritok's own roadmap item.

`CompressionPipeline` caches on `content_hash(content)`, which is
`sha256(content)[:16]` and nothing else (`paritok/storage.py:11-13`). `kind` and
`level` never enter the key (`pipelines/compress.py:204-219`).

So the *second* compression of identical content returns the first one's output
regardless of how it was asked for:

```
pipeline.compress(text, kind="file_read", level="L0")   -> body A
pipeline.compress(text, kind="file_read", level="L3")   -> body A   (cache hit)
```

Reproduced by `python -m segpilot.smoke` (step 4).

Because `kind` **is** honoured (see the summary table), this is a live
correctness bug today, not just a latent one: the same bytes legitimately occur
as different kinds — a stack trace pasted into a file, a log excerpt read from
disk — and whichever kind arrives first wins for the rest of the session. Given
`file_read` → 206 tokens and `assistant_thinking` → 15 tokens on our sample, a
mis-served cache hit is a large error in either direction.

It also blocks roadmap item *"Adaptive compression — per-segment auto-selection
of compression aggressiveness"*: any such policy is silently collapsed by this
cache.

**Suggested fix:** key the compressed-result cache on
`(content_hash, kind, level)`. The shadow store for `expand_context` should keep
using the content-only hash, since the original text genuinely is the same.

---

## Finding 3 — The hosted endpoint does not validate API keys at all

**Severity: medium (high for hackathon participants).**

`POST https://www.paritok.com/api/compress` performs real compression regardless
of what you send in `Authorization`. We tested four cases against the same
payload:

| `Authorization` | Status | Response |
|---|---|---|
| `Bearer pk_live_…` (a real key) | 200 | `{compressed, gpu_available}` |
| `Bearer pk_live_THIS_KEY_IS_NOT_REAL_000` | 200 | identical |
| `Bearer not-even-a-key` | 200 | identical |
| *(header absent)* | 200 | identical |

All four returned byte-identical compressed output. The response body contains
only `compressed` and `gpu_available` — **there is no field acknowledging
attribution**, so a client has no programmatic way to confirm its usage is being
recorded against an account.

Two consequences:

1. **For the hackathon specifically.** Participants must submit the email tied
   to their API key *"to match your submission to your dashboard usage and
   verify Paritok is running through the hosted GPU server."* Someone who
   forgets to set `PARITOK_API_KEY`, or who has a typo in it, gets a fully
   working system with real compression and **zero dashboard usage** — and
   cannot detect this from the API. They find out at submission time.
2. **Generally.** An open, unmetered inference endpoint is a cost and abuse
   exposure for the team running the GPU.

SEGPILOT refuses to start without a key (`segpilot/config.py`,
`require_api_key`), but that only guards against an *absent* key — it cannot
detect a wrong one, because the server does not say.

**Suggested fix:** reject unknown keys with 401, or — if open access is
intentional during the launch period — return `"attributed": true|false` in the
response so clients can warn their users. The second is a two-line change and
would have saved us a dashboard round-trip.

---

## Finding 4 — Intent (`query`) dominates compression quality, and stock passes one stale intent per request

**Severity: high.** This is the gap SEGPILOT is built around.

Holding content and every other parameter constant, varying only `query` on a
263-token file with three unrelated functions:

| `query` | Tokens | Which function survived |
|---|---|---|
| `"why does authenticate_user reject valid passwords"` | 58 | `authenticate_user` |
| `"debug the KMS key rotation logic"` | 65 | `rotate_encryption_keys` |
| `"fix the prometheus metrics export path"` | 36 | `export_metrics_snapshot` |
| `""` (empty) | 160 | all three |

Intent is worth **4.4×** on tokens, and it correctly selects the relevant
function. This is the model working exactly as designed — its system prompt
says *"USER INTENT — the agent's current task. Drives keep/drop."*

The gap is in how that intent is produced. `_extract_query`
(`middleware/wrapper.py:278-300`) walks user turns newest-first and returns the
first real user text — the *original instruction* — then applies it to **every
segment in the request**.

In a long agent session those diverge badly. At turn 40 the agent may be reading
a traceback from a test run, while `_extract_query` still returns *"add OAuth
support to the login flow"* from turn 1. Every tool result is compressed against
an intent that no longer describes what the agent is doing, so the compressor
keeps the wrong things and drops the right ones.

**Suggested fix:** derive intent from the agent's *current* activity — recent
assistant reasoning and the tool call that produced the segment — rather than a
single global user turn. This is what SEGPILOT's intent engine does.

---

## Finding 5 — `tag_messages` / `assign_level` are dead code

**Severity: low-medium (documentation / dead code).**

`paritok/strategies/tagger.py` implements a full conversation-aware labeller —
`tag_messages()`, `assign_level()`, `detect_stale_files()` — described as a
verbatim port of the training labeller, and explicitly recommended for runtime:
*"Use this from the middleware."*

Nothing imports them. The only tagger symbol used anywhere in the runtime is
`classify_kind_from_content`, imported once by `local_model.py:46`.

The practical consequence is on the hosted path. `local_model.py:163` calls
`classify_kind_from_content` as a fallback when `kind is None`;
`gpu_server.py` has **no such fallback** and forwards `kind: null` verbatim.
Since the middleware's two tool-result call sites
(`wrapper.py:418`, `wrapper.py:436`) pass no `kind`, every hosted-path tool
result is compressed with `kind: null` — while `kind` is one of only two
parameters the server actually honours.

**Suggested fix:** either wire `tag_messages` into the middleware as its own
docstring recommends, or at minimum mirror `local_model.py`'s
`classify_kind_from_content` fallback into `gpu_server.py`.

---

## Finding 6 — The proxy hardcodes `/v1`, blocking versioned OpenAI-compatible bases

**Severity: medium.**

`proxy/server.py:418` builds the upstream URL as `f"{openai_base_url}/v1/chat/completions"`.

Providers whose OpenAI-compatible base already contains a version segment cannot
be targeted. Google Gemini's is
`https://generativelanguage.googleapis.com/v1beta/openai`, so the proxy produces
`.../v1beta/openai/v1/chat/completions` → 404. The same applies to several
OpenRouter, Groq and Together deployments.

This matters for adoption: those providers are the cheap tier that
cost-sensitive users — Paritok's core audience — are most likely to be on.

**Suggested fix:** treat `openai_base_url` as a full endpoint prefix, or add a
`chat_completions_path` setting.

---

## Finding 7 — Documented tool `tools/view_trace.py` does not exist

**Severity: low.**

`config.py:110` (`TraceConfig` docstring) says *"Inspect it with
`python tools/view_trace.py`"*, and `pipelines/compress.py:52` repeats
*"View: tools/view_trace.py"*. There is no `tools/` directory in the repository
and no `view_trace` module in the PyPI package.

The trace format itself is genuinely useful — it is what SEGPILOT's own analysis
is built on — so the missing viewer is a real gap for anyone trying to audit
compression quality.

**Suggested fix:** we wrote one and are happy to contribute it upstream
(`segpilot/viewtrace.py`).

---

## Finding 8 — `shadow_storage: "redis"` validates but is not implemented

**Severity: medium (silent data loss).**

`config.py` accepts `shadow_storage: "redis"` as a valid value
(`_VALID_SHADOW_STORAGE = {"memory", "redis"}`), but `storage.py:84` carries a
TODO saying `RedisShadowStorage` is not built, and `CompressionPipeline`
unconditionally falls back to `MemoryShadowStorage`.

A user who sets `redis` — the documented way to get persistence across proxy
restarts — gets in-process memory with no warning. After a restart every
`[REF:id]` in the live conversation is unresolvable, so `expand_context` fails
on content the model can still see referenced.

**Suggested fix:** reject `redis` until implemented, or warn loudly at startup.

---

## Finding 9 — The two kind classifiers disagree, and the conversation-aware one mislabels every file read

**Severity: medium-high.** Directly degrades compression, because `kind` is one
of only two parameters the hosted GPU honours.

Paritok ships two ways to determine a segment's `kind`, and they return
different answers for the same content:

| Content | `classify_segment_kind` + `reclassify_tool_result` | `classify_kind_from_content` |
|---|---|---|
| raw source code | `log_output` | `file_read` |
| line-numbered source (`cat -n` style) | `log_output` | `file_read` |
| Claude Code's `"Here's the result of running \`cat -n\`…"` | `log_output` | `file_read` |

The second column is the path `tag_messages` uses — the one the tagger's own
docstring recommends: *"Use this from the middleware."* It is wrong on all three.

**Root cause.** The `role == "tool"` branch of `classify_segment_kind` tests in
this order:

```
1. "Traceback"/"FAILED"/"Error:" in content   -> log_output
2. head starts with "/" "." "#!" or has "@@"  -> file_read
3. head has more than 5 newlines              -> log_output   <-- everything lands here
4. otherwise                                  -> tool_result
```

Any file read longer than five lines trips rule 3 before any code-shaped check.
And because the function then returns `log_output` rather than `tool_result`,
`reclassify_tool_result` short-circuits on its first line
(`if kind != "tool_result": return kind`) — so its `file_read` rules, *including
the explicit Claude Code `cat -n` rule written for exactly this case*, are
unreachable in practice.

**Impact.** `file_read` and `log_output` select different training system prompts
(`system_prompts/file_read.txt` vs `other.txt`). Mislabelling source as log
output means every file read in a conversation is compressed by the prompt tuned
for logs.

**Suggested fix:** reorder the branch so code-shaped content is tested before the
newline-count heuristic, or have `classify_segment_kind` return `tool_result` for
ambiguous multi-line content so `reclassify_tool_result` can do its job.

**What we do instead.** SEGPILOT does not sniff content when it does not have to.
The agent already told us which tool produced each result via `tool_call_id`, so
we map tool identity → kind directly (`segpilot/policy/kind.py`) and fall back to
`classify_kind_from_content` only for unrecognised tools. Tool identity is ground
truth; sniffing is a guess made in its absence. Paritok already builds this exact
linkage in `_build_tool_use_index` (`wrapper.py:327`) to recover a file path — it
just never uses it for `kind`.

Regression-tested in `tests/test_kind.py::test_we_disagree_with_tagger_on_file_reads`,
which fails loudly if upstream changes this behaviour.

---

## A correction to our own analysis

Before measuring, we read `pipelines/compress.py` and concluded that stock
Paritok compresses every tool result at **L0, the weakest level**, and that
wiring up per-segment levels was the headline opportunity.

Both halves were wrong:

1. `DEFAULT_LEVEL` in `local_model.py:49-50` is **L1**, not L0 — chosen because
   *"L1 is the level the SWE-bench Verified benchmark was run at."*
2. On the hosted path the question is moot, because `level` is ignored entirely
   (finding 1).

We only caught this by running the parameter sweep rather than trusting the
source read. It is why SEGPILOT's policy is built on `query` and `kind`, and why
this document leads with a table of what we actually measured.
