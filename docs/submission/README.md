# Submission kit

Everything needed to submit, drafted and ready. Files here are drafts to paste,
not part of the running project.

- **[devpost.md](devpost.md)** — the text description, project/repo URLs, Paritok email.
- **[video_script.md](video_script.md)** — scene-by-scene script for the <3 min demo.
- **[issues.md](issues.md)** — the nine findings as ready-to-file GitHub issues.
- **[social_post.md](social_post.md)** — `#BuiltWithParitok` post drafts.
- **[results_page.html](results_page.html)** — source of the fallback Artifact mirror.

## Your checklist (the parts only you can do)

- [ ] **Record the demo video** (< 3 min) following `video_script.md`; upload to
      YouTube/Vimeo as **public**; paste the link into Devpost.
- [ ] **File the 9 findings** as issues on the Paritok repo, tagged
      `hackathon-feedback` (see the note in `issues.md` about restricted issues →
      fallback to the Devpost feedback form / HF discussions / Discord).
- [ ] **Post** one `#BuiltWithParitok` update (optional, Social Blitz $50).
- [ ] **Submit on Devpost** with:
  - Project URL: https://segpilot.onrender.com/ (fallback if Render is asleep
    during judging: https://claude.ai/code/artifact/c4b25561-75f6-4434-8466-5fe9f92a158f)
  - Repo URL: https://github.com/Viktor-Nedev/Segpilot
  - Paritok account email: **confirm it is the one your API key is under**
  - Text description (from `devpost.md`)
  - Video link
- [ ] **Confirm the repo's About section** shows the Apache-2.0 license and the
      "Built with Paritok" credit is visible in the README (it is).

## Already done (in the repo)

- Live site (`https://segpilot.onrender.com/`): dashboard driven by
  `web/results.json`, plus a real Paritok hosted-GPU compression demo
- Apache 2.0 `LICENSE`, Paritok credit + badge in `README.md`
- Full N=8 pre-registered campaign result, offline replay, five arms
- Honest results: `docs/results.md`; findings: `docs/findings.md`
- Live solve-rate A/B: attempted four times, stopped on Gemini free-tier
  quota — documented as not completed, not claimed as validated
- Adaptive regret-loop controller: built and unit-tested, never run live —
  same "built, not proven" labelling
- Reproducible evidence: `examples/reports/`, `examples/traces/`, `examples/probe_output.txt`
- 104 passing tests; every number reproduces from committed reference sessions
