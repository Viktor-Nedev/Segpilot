# Demo video script (< 3 minutes)

Target: 2:40. Screen recording with voiceover. No copyrighted music; a quiet
ambient bed or none. Show a terminal + the repo + the published results page.
Keep the terminal font large.

---

## 0:00–0:20 · The hook (show the results page hero)

> "Everyone's building AI. Almost no one measures whether their optimization
> actually works. This is SEGPILOT. It asks one question about Paritok — is
> routing compression per-segment worth it? — and the honest answer, which I'll
> show you measured, is: mostly no. But getting there produced nine findings that
> are the real result."

**On screen:** the live site (segpilot.onrender.com), scroll the hero + stat
strip (9 findings / 12 sessions / 5 arms / 3 retracted).

## 0:20–0:55 · Measure the instrument (terminal: probe)

> "Paritok's compressor takes four steering knobs. Before building anything, I
> measured which ones the hosted GPU actually reads. Watch."

**On screen:** run
```
python -m segpilot.probe --level --query
```
Let it print the verdicts.

> "`query` — the agent's intent — is honoured: change it and the output changes.
> `level` is ignored — L0 through L3 give byte-identical output, and it even
> accepts `level=BANANA` with HTTP 200. That's finding number one, and the
> self-hosted path honours level while the hosted one doesn't."

## 0:55–1:35 · Route the knobs that work, then measure (terminal: replay)

> "So SEGPILOT routes the two knobs that work — intent and kind — per segment.
> Five arms, from stock Paritok up to the full policy, replayed over the same
> recorded agent sessions. Here's the whole benchmark, from committed data, no
> network."

**On screen:** run
```
python -m segpilot.replay.harness --all --suffix __raw --arms stock,intent_only,segpilot
```
Point at the campaign section.

> "This is the full pre-registered set — eight campaigns, designed and
> committed before we recorded or looked at a single one. Kind routing does
> nothing, exactly, on every one of them. Intent routing looked promising
> early — plus nine points at three campaigns — but every time we added more,
> that number shrank. By all eight, it's under four points, five campaigns up,
> three down, and the metric that actually matters — did the agent keep the
> code it needed — comes out slightly worse under routing, not better. That's
> not a reliable win. It's not a win at all."

## 1:35–2:05 · Honesty (show docs/results.md retractions)

> "Three times during this I got an exciting number and had to take it back —
> a hand-built example that didn't reproduce, a 1.61× that turned out to be a
> cache bug during a GPU outage, and that early plus-nine-points reading that
> just didn't survive more data. All three are written up, not deleted. The
> whole point of the project is: measure, don't trust the first number — or
> the second one."

**On screen:** scroll the "Numbers we retracted" section of the live site.

## 2:05–2:35 · The real payload (show findings + viewtrace)

> "What lasts is nine reproducible findings about Paritok's hosted API — the
> ignored level, the cache that's blind to kind, an endpoint that doesn't check
> API keys, two kind classifiers that disagree. And the trace viewer Paritok's
> own code points at but never shipped — I wrote it."

**On screen:** run
```
python -m segpilot.viewtrace examples/traces/campaign_1_segpilot.trace.jsonl
```
Show one before/after where the compressor kept the failing assertion and dropped
the docstring.

## 2:35–2:40 · Close

> "SEGPILOT. Built on Paritok's hosted GPU. An honest measurement, and nine
> findings to go with it."

**On screen:** results page footer / repo URL.
