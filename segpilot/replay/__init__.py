"""Offline replay: apply any compression arm to a recorded session, for free.

A recorded reference session stores the agent's UNCOMPRESSED message history.
Replay re-runs the same `SegpilotCompressor` the live agent used over that
history under each arm, so the numbers describe the product rather than an
approximation of it. Because compression results are cached on
(content, kind, intent), the second and later sweeps make zero network calls.
"""
