# Social post drafts (#BuiltWithParitok)

Pick one, post from X / LinkedIn / dev.to, tag **#BuiltWithParitok** and link the
results page + repo.

---

## X / short

Built SEGPILOT for the Paritok hackathon: route the compressor's intent/kind
per-segment, then *measure* whether it helps.

Honest answer: mostly it doesn't. But building it surfaced 9 reproducible findings
about Paritok's hosted API — including that it accepts `level="BANANA"` with
HTTP 200.

Measure, don't assume. #BuiltWithParitok

🔬 [results page]
💻 [repo]

---

## LinkedIn / longer

For the Token-Efficiency Hackathon I built SEGPILOT on Paritok's open-source
compression model.

The idea: Paritok's compressor is steered by intent and kind. Stock sends one
global intent per request. So route both per-segment, for what the agent is doing
right now — most useful on long, drifted sessions.

I built it, then did the part most "we optimized X" projects skip: I measured
whether it works. Across 7 real agent sessions and 5 arms, the honest result is
that per-segment routing does **not** reliably beat Paritok's defaults — kind
routing does nothing, intent routing is weak and inconsistent.

That's a useful thing to know, measured instead of assumed. And the real payload
is nine reproducible findings about Paritok's hosted API: a silently-ignored
`level` parameter (it accepts `"BANANA"` with HTTP 200), a cache blind to segment
kind, an endpoint that doesn't validate API keys, two `kind` classifiers that
disagree, and a documented trace viewer that was missing — so I wrote it.

I also retracted two encouraging numbers mid-project when the data was cleaned up,
and kept both in the writeup. The whole thing is an argument for measuring instead
of trusting the first number.

Built with Paritok. #BuiltWithParitok

🔬 Results: [results page]
💻 Code: [repo]

---

## dev.to / blog angle (title ideas)

- "I measured whether my Paritok optimization worked. It didn't. Here's why that's
  the good outcome."
- "Nine things I found in Paritok's hosted API by building a benchmark that proved
  my own idea wrong."
