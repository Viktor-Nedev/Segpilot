"""Disk cache for compression results, keyed on what actually determines them.

WHY WE DO NOT USE PARITOK'S CACHE
---------------------------------
`CompressionPipeline` caches on `content_hash(content)` — a SHA256 of the
content and nothing else (`paritok/storage.py:11-13`). `kind` and `query` never
enter the key. Since both of those demonstrably change the output (see
`python -m segpilot.probe`), the same bytes compressed under a different kind or
intent silently return the first result. Reproduce with `python -m segpilot.smoke`.

That is fatal for this project: every arm of our benchmark varies exactly those
two parameters on identical content, so Paritok's cache would collapse all five
arms into whichever ran first. Filed as finding #2.

WHY IT IS ON DISK
-----------------
The replay harness re-compresses the same segments across many policy arms and
many runs. Persisting to SQLite makes the second and later runs free and exactly
reproducible — which matters on a hosted GPU we do not want to hammer, and makes
the reported numbers stable between runs.

Only the *compressed result* is cached here. `expand_context` shadow storage
still belongs to Paritok and is correctly keyed on content alone, since the
original text really is identical regardless of how it was compressed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS compression_cache (
    key            TEXT PRIMARY KEY,
    content_sha    TEXT NOT NULL,
    kind           TEXT,
    intent         TEXT,
    compressed     TEXT NOT NULL,
    original_tokens   INTEGER NOT NULL,
    compressed_tokens INTEGER NOT NULL,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_content ON compression_cache(content_sha);
"""


def cache_key(content: str, kind: str | None, intent: str | None) -> str:
    """The three inputs that determine a compression result.

    `level` and `target_ratio` are deliberately excluded: we measured both to be
    no-ops on the hosted GPU, so including them would fragment the cache without
    distinguishing anything. If a future Paritok release honours `level`, add it
    here and the cache invalidates naturally through the changed key.
    """
    h = hashlib.sha256()
    h.update((content or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((kind or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((intent or "").encode("utf-8"))
    return h.hexdigest()[:32]


@dataclass
class CachedCompression:
    compressed: str
    original_tokens: int
    compressed_tokens: int

    @property
    def ratio(self) -> float:
        if not self.original_tokens:
            return 0.0
        return self.compressed_tokens / self.original_tokens


class CompressionCache:
    """Thread-safe SQLite cache. Safe to share across the gateway's workers."""

    def __init__(self, path: str | Path = "segpilot_cache.db"):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.hits = 0
        self.misses = 0

    def get(self, content: str, kind: str | None, intent: str | None) -> CachedCompression | None:
        key = cache_key(content, kind, intent)
        with self._lock:
            row = self._conn.execute(
                "SELECT compressed, original_tokens, compressed_tokens "
                "FROM compression_cache WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return CachedCompression(compressed=row[0], original_tokens=row[1],
                                 compressed_tokens=row[2])

    def put(
        self,
        content: str,
        kind: str | None,
        intent: str | None,
        *,
        compressed: str,
        original_tokens: int,
        compressed_tokens: int,
    ) -> None:
        import time

        key = cache_key(content, kind, intent)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO compression_cache "
                "(key, content_sha, kind, intent, compressed, original_tokens, "
                " compressed_tokens, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    key,
                    hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16],
                    kind,
                    intent,
                    compressed,
                    original_tokens,
                    compressed_tokens,
                    time.time(),
                ),
            )
            self._conn.commit()

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM compression_cache"
            ).fetchone()[0]
        looked_up = self.hits + self.misses
        return {
            "entries": total,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / looked_up, 3) if looked_up else 0.0,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
