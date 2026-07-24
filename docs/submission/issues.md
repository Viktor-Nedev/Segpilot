# Findings as GitHub issues (ready to paste)

File these on https://github.com/Paritok-official/paritok-4b-v1/issues, each
tagged **`hackathon-feedback`**. Full write-ups with reproductions are in
[../findings.md](../findings.md).

> ⚠️ The Paritok repo showed "Issue creation is restricted." If filing is blocked,
> submit these through the Devpost feedback form, the Hugging Face model
> discussions, or the Paritok Discord, and reference `docs/findings.md`.

Each block below is one issue: title on the first line, body under it.

---

### Hosted GPU silently ignores `level` (and accepts `level="BANANA"` with HTTP 200)

`level` is documented and honoured on the self-hosted path (`local_model.py`
embeds it in the SEG prompt and validates it), but the hosted GPU appears to
discard it. On identical content, `L0`/`L1`/`L2`/`L3`/`null`/absent/`"BANANA"`
all return byte-identical output, and `"BANANA"` is accepted with HTTP 200.
Because `use_gpu_server` is documented as "the only switch that matters," flipping
it silently changes compression semantics — e.g. `wrapper.py`'s deliberate
`level="L3"` history compression is a no-op on hosted. Suggest honouring `level`
server-side, or rejecting unknown values and documenting the hosted endpoint as
level-agnostic. Repro: `python -m segpilot.probe --level`.

---

### Compression cache is blind to `kind`/`level`

`CompressionPipeline` caches on `content_hash(content)` only (`storage.py`), so a
second compression of identical content returns the first result regardless of
`kind`/`level`. Since `kind` is honoured and changes output, this is a live
correctness bug (the same bytes as `file_read` vs `log_output` collide), and it
blocks the roadmap's per-segment adaptive compression. Suggest keying the
compressed-result cache on `(content_hash, kind, level)`; the shadow store for
`expand_context` can stay content-only. Repro: `python -m segpilot.smoke`.

---

### Hosted endpoint does not validate API keys

`POST /api/compress` returns identical HTTP 200s for a real key, a garbage key, a
malformed key, and no `Authorization` header at all, and the response has no
attribution field. A participant who typos `PARITOK_API_KEY` gets full
compression and zero dashboard usage, undetectably — which matters because
dashboard usage is how submissions are verified. Suggest 401 on unknown keys, or
returning `"attributed": true|false` so clients can warn.

---

### Intent dominates compression, but stock passes one stale intent per request

Intent (`query`) is the strongest lever (measured 160→36 tokens; it decides which
code survives). But `_extract_query` (`wrapper.py`) returns the user's first real
instruction and applies it to every segment, so in a long session tool results are
compressed against a task the agent finished long ago. Suggest deriving intent
from the current activity (recent reasoning + the producing tool call) rather than
one global user turn. (We tried this; the win was inconsistent — see our
`docs/results.md` — but the stale-intent gap itself is real.)

---

### `tag_messages` / `assign_level` are dead code; hosted path sends `kind: null`

`tagger.py` ships a conversation-aware labeller its docstring recommends for
middleware use, but nothing imports `tag_messages`/`assign_level`. On the hosted
path `gpu_server.py` has no `classify_kind_from_content` fallback (unlike
`local_model.py:163`), so every tool result is compressed with `kind: null` — and
`kind` is one of only two honoured parameters. Suggest wiring `tag_messages` in,
or at least mirroring the local sniffing fallback into `gpu_server.py`.

---

### Proxy hardcodes `/v1`, breaking versioned OpenAI-compatible bases

`proxy/server.py` builds `f"{openai_base_url}/v1/chat/completions"`. Providers
whose base already carries a version — Gemini's
`https://generativelanguage.googleapis.com/v1beta/openai`, several OpenRouter/Groq
deployments — become `.../v1beta/openai/v1/chat/completions` → 404. These are the
cheap tiers cost-sensitive users are on. Suggest treating `openai_base_url` as a
full prefix, or adding a `chat_completions_path` setting.

---

### Documented `tools/view_trace.py` does not exist

`config.py` (TraceConfig) says "Inspect it with `python tools/view_trace.py`" and
`compress.py` repeats "View: tools/view_trace.py", but there is no `tools/`
directory or `view_trace` module in the package. The trace format is genuinely
useful; the missing viewer is a real gap. We wrote one and are happy to contribute
it upstream (reads the exact `_debug_dump` JSONL, renders before/after).

---

### `shadow_storage: "redis"` validates but is unimplemented

`config.py` accepts `shadow_storage: "redis"` as valid, but `storage.py` has a
TODO saying `RedisShadowStorage` isn't built, and the pipeline falls back to
in-process memory with no warning. A user who sets `redis` for persistence gets
memory, and after a restart every live `[REF:id]` is unresolvable, so
`expand_context` fails on content the model can still see referenced. Suggest
rejecting `redis` until implemented, or warning loudly at startup.

---

### The two `kind` classifiers disagree; the conversation-aware one mislabels file reads

`classify_segment_kind` + `reclassify_tool_result` (used by `tag_messages`) label
every multi-line file read as `log_output`, while `classify_kind_from_content`
labels the same content `file_read`. Root cause: the `role == "tool"` branch tests
">5 newlines → log_output" before any code check, then returns `log_output`, so
`reclassify_tool_result` short-circuits and its `file_read` rules (including the
explicit Claude Code `cat -n` rule) are unreachable. `file_read` and `log_output`
select different training prompts, so this degrades every file read. Suggest
reordering the branch, or returning `tool_result` for ambiguous multi-line content
so reclassification can run.
