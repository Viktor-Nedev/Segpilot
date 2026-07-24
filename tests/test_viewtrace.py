"""viewtrace: renders Paritok's compress_trace.jsonl format."""

import json

from segpilot.viewtrace import load_trace, render_markdown, render_text

TRACE_LINES = [
    {"ts": 1.0, "elapsed_s": 0.5, "query": "fix the parse bug",
     "original_tokens": 200, "compressed_tokens": 60, "ratio": 0.7,
     "shadow_id": "abc123", "original": "def parse(): return Decimal(raw)  # long\n" * 5,
     "compressed": "def parse(): return Decimal(raw)"},
    {"ts": 2.0, "skipped": True, "reason": "below_min_tokens", "original_tokens": 12},
]


def _write(tmp_path):
    p = tmp_path / "compress_trace.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in TRACE_LINES) + "\n", encoding="utf-8")
    return p


def test_load_trace_reads_all_records(tmp_path):
    recs = load_trace(_write(tmp_path))
    assert len(recs) == 2
    assert recs[1]["skipped"] is True


def test_load_trace_tolerates_malformed_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"original_tokens":1,"compressed_tokens":1}\nNOT JSON\n', encoding="utf-8")
    recs = load_trace(p)
    assert len(recs) == 1


def test_text_render_shows_before_after_and_summary(tmp_path):
    out = render_text(load_trace(_write(tmp_path)))
    assert "200 → 60" in out
    assert "fix the parse bug" in out          # intent shown
    assert "SKIPPED" in out                      # skip record shown
    assert "below_min_tokens" in out
    assert "1 compressed, 1 skipped" in out      # summary
    assert "saved 140" in out


def test_markdown_render_has_fences_and_summary(tmp_path):
    out = render_markdown(load_trace(_write(tmp_path)))
    assert "```" in out
    assert "# Compression trace" in out
    assert "1 compressed, 1 skipped" in out


def test_full_body_not_truncated(tmp_path):
    long_original = "x" * 5000
    recs = [{"original_tokens": 100, "compressed_tokens": 10,
             "original": long_original, "compressed": "x"}]
    out = render_text(recs, body_chars=10**9)
    assert long_original in out
