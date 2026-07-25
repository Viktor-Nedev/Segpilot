"""Client-side shadow store: shadow_id -> original text (+ its kind).

WHY THIS EXISTS
---------------
Paritok's compressor tags compressed output with `[REF:id src=path]`, resolvable
back to the original via an `expand_context` virtual tool -- but that tool is
resolved SERVER-SIDE by Paritok's own middleware, using a shadow store that only
`CompressionPipeline` populates. We deliberately do not use
`CompressionPipeline` (see compressor.py's module docstring: its cache is blind
to kind/level, which would collapse our benchmark arms). So the `[REF:id]` tags
the hosted GPU returns to us are, on our side, dangling -- nothing resolves them.

For the adaptive regret loop (Phase 7 Block D) we need real resolution: the
agent has to be able to ask for the original back, and we have to know it asked.
So this is our own store, populated at the moment of compression (the original
text is already in memory then) and queried when the agent calls our own
`expand_context` tool (see agent/tools.py).

Storing `kind` alongside the original is what lets a regret event (an
expand_context call) be attributed back to the (kind) bucket that produced the
compression -- the signal segpilot/policy/adaptive.py learns from.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

# Matches the `[REF:id]` or `[REF:id src=path]` prefix Paritok's compressor
# tags its output with. Same shape as guard.py's _REF_TAG, but capturing the id.
_REF_ID = re.compile(r"^\[REF:([a-f0-9]+)(?:\s+src=[^\]]*)?\]")


def extract_shadow_id(compressed_text: str) -> str | None:
    """Pull the shadow id out of a compressed body's [REF:id] tag, or None if
    the text carries no such tag (e.g. it was never compressed)."""
    if not compressed_text:
        return None
    m = _REF_ID.match(compressed_text.strip())
    return m.group(1) if m else None


@dataclass
class ShadowEntry:
    original: str
    kind: str | None = None


class ShadowStore:
    """In-memory, per-session. No persistence: the adaptive loop this backs is
    explicitly scoped to online, within-session learning (see adaptive.py), so
    there is nothing to keep across a restart."""

    def __init__(self):
        self._lock = threading.Lock()
        self._store: dict[str, ShadowEntry] = {}

    def put(self, shadow_id: str, original: str, *, kind: str | None = None) -> None:
        with self._lock:
            self._store[shadow_id] = ShadowEntry(original=original, kind=kind)

    def get(self, shadow_id: str) -> ShadowEntry | None:
        with self._lock:
            return self._store.get(shadow_id)

    def record(
        self, compressed_text: str, original: str, *, kind: str | None = None
    ) -> str | None:
        """Extract the ref id from `compressed_text` and store `original` under
        it. Returns the id, or None if there was no ref tag to extract."""
        sid = extract_shadow_id(compressed_text)
        if sid:
            self.put(sid, original, kind=kind)
        return sid

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
