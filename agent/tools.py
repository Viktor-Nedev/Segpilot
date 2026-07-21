"""Agent tools, sandboxed to a working directory.

Every tool returns a plain string, because that is what becomes a `role: "tool"`
message and therefore what SEGPILOT compresses. Output shape matters to the
benchmark: `read_file` returns line-numbered source (the convention Claude Code
and Cursor use), so we are compressing the same shape of content real agents do.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MAX_OUTPUT_CHARS = 20_000


class ToolError(Exception):
    """Recoverable — reported back to the model so it can correct itself."""


def _resolve(workdir: Path, relpath: str) -> Path:
    """Resolve a path inside the sandbox, refusing to escape it."""
    if not relpath:
        raise ToolError("path is required")
    candidate = (workdir / relpath).resolve()
    root = workdir.resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError(f"path escapes the working directory: {relpath}")
    return candidate


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n\n... [{omitted} characters omitted] ...\n\n{text[-half:]}"


def read_file(workdir: Path, file_path: str, **_) -> str:
    path = _resolve(workdir, file_path)
    if not path.exists():
        raise ToolError(f"no such file: {file_path}")
    if path.is_dir():
        raise ToolError(f"{file_path} is a directory; use list_dir")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    numbered = "".join(f"{i:6d}\t{line}\n" for i, line in enumerate(lines, 1))
    return _truncate(numbered)


def list_dir(workdir: Path, path: str = ".", **_) -> str:
    target = _resolve(workdir, path or ".")
    if not target.exists():
        raise ToolError(f"no such directory: {path}")
    rows = []
    for child in sorted(target.iterdir()):
        if child.name in {"__pycache__", ".pytest_cache"}:
            continue
        rel = child.relative_to(workdir.resolve())
        rows.append(f"{'dir ' if child.is_dir() else 'file'}  {rel.as_posix()}")
    return "\n".join(rows) or "(empty)"


def grep(workdir: Path, pattern: str, path: str = ".", **_) -> str:
    if not pattern:
        raise ToolError("pattern is required")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"bad regex: {exc}") from exc

    root = _resolve(workdir, path or ".")
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    hits = []
    for file in files:
        if "__pycache__" in file.parts:
            continue
        try:
            for n, line in enumerate(
                file.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if regex.search(line):
                    rel = file.relative_to(workdir.resolve()).as_posix()
                    hits.append(f"{rel}:{n}: {line.strip()}")
        except OSError:
            continue
    if not hits:
        return f"no matches for {pattern!r}"
    return _truncate("\n".join(hits))


def edit_file(workdir: Path, file_path: str, old_string: str, new_string: str, **_) -> str:
    """Exact string replacement. Refuses ambiguous or missing matches, so the
    model has to be specific rather than hoping."""
    path = _resolve(workdir, file_path)
    if not path.exists():
        raise ToolError(f"no such file: {file_path}")
    source = path.read_text(encoding="utf-8")
    if old_string not in source:
        raise ToolError(
            f"old_string not found in {file_path}. Read the file again and copy "
            f"the exact text, including indentation."
        )
    count = source.count(old_string)
    if count > 1:
        raise ToolError(
            f"old_string appears {count} times in {file_path}; include more "
            f"surrounding context to make it unique"
        )
    path.write_text(source.replace(old_string, new_string), encoding="utf-8")
    return f"edited {file_path}"


def run_tests(workdir: Path, path: str = "tests/", **_) -> str:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", path, "-q"],
            cwd=str(workdir), capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT: tests took longer than 120s"
    return _truncate((proc.stdout or "") + (proc.stderr or ""))


TOOL_FUNCS = {
    "read_file": read_file,
    "list_dir": list_dir,
    "grep": grep,
    "edit_file": edit_file,
    "run_tests": run_tests,
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file. Returns line-numbered contents.",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string", "description": "Path relative to the project root."}},
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories at a path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory, defaults to the project root."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search Python files for a regular expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "File or directory to search."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact string in a file. old_string must appear exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the test suite with pytest.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Test path, defaults to tests/."}},
            },
        },
    },
]


def execute(workdir: Path, name: str, args: dict) -> tuple[str, bool]:
    """Run a tool. Returns (output, ok). Errors come back as text, not raises,
    so a bad call costs the model a turn instead of killing the run."""
    func = TOOL_FUNCS.get(name)
    if func is None:
        return f"unknown tool: {name}", False
    try:
        return func(workdir, **args), True
    except ToolError as exc:
        return f"error: {exc}", False
    except TypeError as exc:
        return f"error: bad arguments for {name}: {exc}", False
    except Exception as exc:  # noqa: BLE001 — never kill the run over one tool
        return f"error: {type(exc).__name__}: {exc}", False
