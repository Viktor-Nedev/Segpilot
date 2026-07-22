"""Reading recorded sessions written by agent/ruff.py."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Session:
    """A recorded agent run, loaded from JSONL.

    The important field is `messages`: the full, uncompressed history. Replay
    applies each arm to exactly this content, so every arm sees identical input.
    """

    path: Path
    task_id: str
    recorded_arm: str
    instruction: str
    ground_truth: dict
    messages: list[dict] = field(default_factory=list)
    result: dict = field(default_factory=dict)

    @property
    def must_appear(self) -> list[str]:
        return list(self.ground_truth.get("must_appear", []))

    @property
    def tool_result_count(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "tool")


def load_session(path: str | Path) -> Session:
    path = Path(path)
    meta: dict = {}
    messages: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("type") == "meta":
                meta = row
            elif row.get("type") == "messages":
                messages = row.get("messages", [])
    if not messages:
        raise ValueError(f"{path} has no recorded messages")
    return Session(
        path=path,
        task_id=meta.get("task_id", path.stem),
        recorded_arm=meta.get("arm", "unknown"),
        instruction=meta.get("instruction", ""),
        ground_truth=meta.get("ground_truth", {}),
        messages=messages,
        result=meta.get("result", {}),
    )


def find_sessions(directory: str | Path, *, suffix: str = "") -> list[Path]:
    """All session files in a directory, optionally filtered by name suffix
    (e.g. '__raw' for reference trajectories)."""
    directory = Path(directory)
    if not directory.exists():
        return []
    pattern = f"*{suffix}.jsonl" if suffix else "*.jsonl"
    return sorted(directory.glob(pattern))
