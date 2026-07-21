"""Must-keep span detection — VENDORED FROM PARITOK.

Source: https://github.com/Paritok-official/paritok-4b-v1 -> src/mustkeep.py
License: Apache 2.0 (same as this project). Copyright the Paritok authors.

WHY THIS FILE IS VENDORED
-------------------------
`src/mustkeep.py` lives in Paritok's training-side package, which is NOT shipped
to PyPI — `pip install paritok` gives you `paritok/` but not `src/`. The logic is
Paritok's own training-time definition of "content that must survive
compression": file paths, stack-trace locations, line numbers, commit hashes,
URLs, error classes, snake_case/camelCase identifiers, and known package names.

SEGPILOT uses it as its objective quality metric. Because it is the *same*
definition the model was trained against, "must-keep retention" is a fair,
non-invented yardstick rather than a metric we tuned in our own favour.

The regex and merge logic are kept byte-faithful to upstream so our numbers stay
comparable; only comments have been translated from the original Chinese and an
English docstring added.
"""

import re

SKIP_KINDS = {"system_prompt"}
MIN_CONTENT_LENGTH = 50


# One combined mega-regex: a single scan handles every span kind at once.
COMBINED_PATTERN = re.compile(
    r'(?P<file_path>'
    r'/workspace/[\w\-./_]+'
    r'|/[\w\-./_]+\.(?:py|js|ts|tsx|jsx|java|go|rs|cpp|c|h|hpp|rb|php|cs|md|yaml|yml|json|toml|cfg|ini|sh)\b'
    r'|(?<![\w/])(?:src|tests?|lib|app)/[\w\-./_]+\.(?:py|js|ts|jsx|java|go|rs|cpp|c|h|md|yaml|yml|json|toml)\b'
    r')'
    r'|(?P<stack_trace_path>File\s+"[^"]+\.[a-z]{1,4}")'
    r'|(?P<line_number>'
    r'(?:line|L)\s*[:#]?\s*\d+'
    r'|(?<=[\s,\(\[])\d{1,4}:\d{1,3}(?=[\s,\)\]])'
    r')'
    r'|(?P<hash_>\b(?:commit\s+)?[0-9a-f]{7,40}\b)'
    r'|(?P<url>https?://[^\s\'"<>\]`)]+)'
    r'|(?P<error_class>\b[A-Z][a-zA-Z]*(?:Error|Exception|Warning|Failure|Fault)\b)'
    r'|(?P<identifier>\b'
    r'[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*){2,}'
    r'|[a-z][a-z0-9]*(?:[A-Z][a-zA-Z0-9]+){2,}'
    r'|[A-Z][a-z]+[A-Z][a-z][a-zA-Z0-9]{4,}'
    r'\b)'
    r'|(?P<quoted_literal>\'[^\']{4,80}/[^\']*\')'
    r'|(?P<package_name>\b(?:netcdf4|h5netcdf|xarray|numpy|pandas|torch|tensorflow|sklearn|matplotlib|scipy|requests|django|flask|fastapi|pytest|sympy)\b)',
)

# A more conservative subset applies to assistant_thinking segments: prose
# reasoning is expected to be paraphrased, so only hard facts are protected.
THINKING_GROUPS = {"error_class", "package_name", "quoted_literal", "url"}


def find_must_keep_spans(text: str, seg_id: str, seg_kind: str) -> list[dict]:
    """Return the spans in `text` that compression must not destroy.

    Each span is {seg_id, start, end, kind, text}. Overlapping spans are merged
    so a character is never counted twice.
    """
    if seg_kind in SKIP_KINDS:
        return []
    if len(text) < MIN_CONTENT_LENGTH:
        return []

    is_thinking = (seg_kind == "assistant_thinking")

    spans = []
    for m in COMBINED_PATTERN.finditer(text):
        kind = m.lastgroup
        if kind == "hash_":
            kind = "hash"
        if is_thinking and kind not in THINKING_GROUPS:
            continue
        spans.append({
            "seg_id": seg_id,
            "start": m.start(),
            "end": m.end(),
            "kind": kind,
            "text": m.group(0),
        })

    # Deduplicate + merge overlapping spans.
    spans.sort(key=lambda s: (s["start"], -s["end"]))
    merged = []
    for s in spans:
        if merged and s["start"] < merged[-1]["end"]:
            if s["end"] > merged[-1]["end"]:
                merged[-1] = s
        else:
            merged.append(s)

    return merged
