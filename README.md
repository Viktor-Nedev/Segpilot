[![Built with Paritok](https://img.shields.io/badge/Built%20with-Paritok-1f2d3d)](https://github.com/Paritok-official/paritok-4b-v1)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue)](./LICENSE)

# SEGPILOT

**A measurement harness that answers one question about Paritok: is routing
compression per-segment worth it for your workload?**

Built with [Paritok](https://github.com/Paritok-official/paritok-4b-v1), running
on Paritok's hosted GPU server.

**🔬 Live site: [segpilot.onrender.com](https://segpilot.onrender.com/)** —
dashboard + a real compression demo (paste your own code, get two real Paritok
hosted-GPU compressions back). Free-tier host; if it's asleep, first request
takes ~30–50s to wake. Static mirror: [Artifact
page](https://claude.ai/code/artifact/c4b25561-75f6-4434-8466-5fe9f92a158f).

---

## The one-paragraph version

Paritok's compressor is steered by two parameters the hosted GPU actually reads —
`query` (the agent's intent) and `kind` (what the segment is). Stock Paritok sends
one global intent per request and no kind. The obvious idea: route both
*per segment*, so each tool result is compressed for what the agent is doing right
then. We built that — a kind engine, a phase-aware intent engine, a retention
guard — and then did the thing most "we optimized X" projects skip: **we measured
whether it actually helps, honestly, and reported that it mostly doesn't.**

On 3 drifted multi-bug sessions and 4 single-bug controls, replayed under five
arms: **`kind` routing changes nothing, and `intent` routing gives a small
(~9pp must-keep retention) and inconsistent improvement that reverses on one of
three sessions.** No reliable win. The lasting output is **nine measured findings
about Paritok's hosted API**, a **reproducible probe** anyone can point at their
own workload, and an honest **null result** that saves the next person from a
dead-end optimization. Full numbers: [docs/results.md](docs/results.md) ·
[examples/reports/replay_report.md](examples/reports/replay_report.md).

## What we measured first

Before building anything, we swept every steering parameter Paritok exposes
against the hosted GPU, holding content constant and varying one parameter at a
time (`python -m segpilot.probe --all`, recorded in
[examples/probe_output.txt](examples/probe_output.txt)):

| Parameter | Honoured on hosted GPU? | Evidence |
|---|---|---|
| `query` (intent) | **Yes** | 160 → 36 tokens as intent changes; decides which code survives |
| `kind` | **Yes** | 206 / 187 / 15 tokens for `file_read` / `log_output` / `assistant_thinking` |
| `level` (L0–L3) | **No** | Byte-identical for L0–L3, `null`, absent, and `"BANANA"` (accepted, HTTP 200) |
| `target_ratio` | **No** | Byte-identical for `0.5`, `0.2`, `"10%"`, `null` |

So SEGPILOT routes the two that work (`query`, `kind`) and ignores the two the
docs advertise but the server discards. That `level` is silently ignored on the
hosted path — while the self-hosted path honours it — is finding #1.

## The honest result

Five arms isolate each layer's contribution, replayed offline over identical,
uncompressed reference trajectories:

| arm | kind | intent | must-keep ret. (campaigns) |
|---|---|---|---|
| `stock` | none | global (`_extract_query`) | 81% |
| `kind_only` | tool-identity | global | 81% |
| `intent_only` | none | per-segment | 93% |
| `segpilot` | tool-identity | per-segment | 93% |
| `segpilot+guard` | tool-identity | per-segment + guard | 97% |

- **`kind` routing does nothing.** `kind_only` equals `stock` on every metric in
  every session. Correct kind labelling (finding #9) doesn't change the output on
  these workloads.
- **`intent` routing is weak and inconsistent.** +9pp must-keep retention on
  average across the three drift sessions — but it *reverses* on one of them, and
  the mean is carried by one outlier. Compression ratio is likewise mixed.
- **Task-relevant retention doesn't move**, so the token-efficiency headline is
  flat. The only axis the arms differ on is must-keep retention, and weakly.

**Verdict: we cannot claim intent/kind routing reliably beats Paritok's stock
defaults, even on the long drifted sessions the idea is meant for.** That is a
useful thing to know, measured rather than assumed.

## What we got wrong (kept, not hidden)

Twice we reported an encouraging number and had to retract it:

- **"79% fewer tokens / 3.1× on a drift session."** A hand-constructed example,
  not a benchmark result — the real agent sessions didn't reproduce it.
- **"1.61× rel/1k on the drift session."** An artefact of a cache poisoned during
  a hosted-GPU outage: passthrough segments were cached as if compressed. Fixed
  (never cache a passthrough) and re-run to a clean pass.

Both are written up in [docs/results.md](docs/results.md). Keeping them is the
point: the whole project is an argument for measuring instead of trusting the
first number.

## The nine findings (the real contribution)

Each is reproducible from this repo and written up in
[docs/findings.md](docs/findings.md):

1. Hosted GPU silently ignores `level` — and accepts `"BANANA"` with HTTP 200
2. Compression cache is blind to `kind`/`level`, collapsing any per-segment policy
3. Hosted endpoint does not validate API keys → silently unattributed usage
4. Intent dominates compression, but stock passes one stale intent per request
5. `tag_messages`/`assign_level` are dead code; hosted path sends `kind: null`
6. Proxy hardcodes `/v1`, breaking Gemini/OpenRouter/Groq-style base URLs
7. Documented `tools/view_trace.py` does not exist — so [we wrote one](segpilot/viewtrace.py)
8. `shadow_storage: "redis"` validates but is unimplemented → silent data loss
9. The two `kind` classifiers disagree; the conversation-aware one mislabels every file read

## What's in the box

```
segpilot/
  probe.py            measure which Paritok parameters actually work
  compressor.py       the five arms (stock … segpilot+guard), shared by agent + replay
  policy/             kind engine · intent engine · retention guard · level-aware cache
  replay/             offline sweep + metrics + report (Markdown & SVG Pareto)
  viewtrace.py        the missing Paritok trace viewer (finding #7)
agent/                Ruff — a small real coding agent, compressed live
bench/                8 seeded bugs + 3 drift campaigns, all self-verifying
```

Compression always runs on Paritok's hosted GPU, attributed to the account key.
The agent's upstream LLM is Gemini (its OpenAI-compatible endpoint; see finding #6
for why Paritok's own proxy can't target it).

## Setup & reproduce

Requires Python 3.11+ and a Paritok API key from
[paritok.com](https://www.paritok.com) → dashboard → API keys.

```bash
pip install -e .
export PARITOK_API_KEY=pk_live_...     # required; the endpoint answers without one but records nothing (finding #3)
export GEMINI_API_KEY=...              # upstream LLM, for recording new sessions

python -m segpilot.probe --all                 # reproduce the lever table
python -m segpilot.replay.harness --all --suffix __raw \
  --arms stock,kind_only,intent_only,segpilot,segpilot+guard   # the numbers
python -m segpilot.replay.report --out examples/reports/       # regenerate the report
python -m segpilot.viewtrace examples/traces/campaign_1_segpilot.trace.jsonl
```

The reference sessions are committed, so the replay reproduces the exact numbers
in [examples/reports/](examples/reports/) without recording anything.

## Credits

Built with **[Paritok](https://github.com/Paritok-official/paritok-4b-v1)** — the
open-source compression model for coding agents, by Jiayu Shi and Luzhuo Chen.
`segpilot/vendor/mustkeep.py` is vendored from the Paritok repository under
Apache 2.0.

## License

Apache 2.0 — see [LICENSE](./LICENSE).
