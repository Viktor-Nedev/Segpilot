"""SEGPILOT configuration.

Layered like Paritok's own config: dataclass defaults -> optional YAML file ->
environment overrides. Environment always wins, so secrets never need to live
in the repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

# The four policies we benchmark against each other. `stock` reproduces what
# Paritok does today (no kind, no level -> model default L0) and is the baseline
# every SEGPILOT number is quoted against.
POLICIES: tuple[str, ...] = ("stock", "static", "guarded", "adaptive")

LEVELS: tuple[str, ...] = ("L0", "L1", "L2", "L3")

# Target compression ratios the model was trained against, from
# paritok/strategies/prompts.py. Lower = more aggressive.
LEVEL_TARGET_RATIO: dict[str, float] = {
    "L0": 0.50,
    "L1": 0.35,
    "L2": 0.25,
    "L3": 0.20,
}


@dataclass
class ParitokBackendConfig:
    """How we reach Paritok's compressor.

    Defaults to the hosted GPU server, which is what the hackathon requires so
    that usage is attributed to your dashboard account.
    """

    use_gpu_server: bool = True
    base_url: str = "https://www.paritok.com/api"
    model: str = "paritok-4b-v1"
    api_key: str = ""
    timeout: float = 90.0

    # The hosted endpoint answers unauthenticated requests, which means work
    # done without a key produces ZERO dashboard usage. For a hackathon
    # submission that silently invalidates the "verify via dashboard" step, so
    # we refuse to start rather than let it happen by accident.
    require_api_key: bool = True


@dataclass
class UpstreamConfig:
    """The billed LLM we forward compressed requests to."""

    provider: str = "gemini"
    # Gemini's OpenAI-compatible surface. NOTE: this base already carries a
    # version segment, which is precisely why Paritok's own proxy cannot target
    # it (it appends a second `/v1`). See docs/findings.md finding #6.
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    model: str = "gemini-2.5-flash"
    api_key: str = ""
    timeout: float = 180.0


@dataclass
class PolicyConfig:
    mode: str = "guarded"

    # --- guard (layer 2) ---
    # Minimum fraction of must-keep spans that must survive compression. Below
    # this we retry one level gentler, then fall back to the original.
    min_retention: float = 0.85
    # Segments smaller than this are not worth a GPU round-trip. Mirrors
    # Paritok's own CompressionConfig.min_tokens default.
    min_tokens: int = 512
    max_tokens: int = 50_000

    # --- adaptive (layer 3) ---
    # Regret rate above which a (kind, level) bucket backs off one level.
    regret_backoff: float = 0.20
    # Regret rate below which a bucket escalates one level.
    regret_escalate: float = 0.05
    # Observations required in a bucket before it may move.
    min_observations: int = 8

    _VALID_MODES: ClassVar[frozenset] = frozenset(POLICIES)

    def __post_init__(self):
        assert self.mode in self._VALID_MODES, (
            f"policy.mode must be one of {sorted(self._VALID_MODES)}, got '{self.mode}'"
        )
        assert 0.0 <= self.min_retention <= 1.0, (
            f"min_retention must be 0.0-1.0, got {self.min_retention}"
        )


@dataclass
class TelemetryConfig:
    db_path: str = "segpilot.db"
    # Mirror every compression to JSONL as well, for the trace viewer.
    trace_path: str = "compress_trace.jsonl"
    enabled: bool = True


@dataclass
class SegpilotConfig:
    paritok: ParitokBackendConfig = field(default_factory=ParitokBackendConfig)
    upstream: UpstreamConfig = field(default_factory=UpstreamConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)

    @classmethod
    def _merge(cls, target, overrides: dict):
        valid = set(target.__dataclass_fields__.keys())
        unknown = set(overrides) - valid
        if unknown:
            raise ValueError(
                f"Unknown config key(s) {sorted(unknown)} in "
                f"{type(target).__name__}. Valid: {sorted(valid)}"
            )
        return type(target)(**{**target.__dict__, **overrides})

    @classmethod
    def from_dict(cls, data: dict) -> "SegpilotConfig":
        cfg = cls()
        for key in ("paritok", "upstream", "policy", "telemetry"):
            if key in data and data[key]:
                setattr(cfg, key, cls._merge(getattr(cfg, key), data[key]))
        return cfg

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SegpilotConfig":
        """Load defaults, then YAML if present, then environment overrides."""
        cfg = cls()
        if path is None and Path("segpilot.yaml").exists():
            path = "segpilot.yaml"
        if path and Path(path).exists():
            import yaml

            with open(path, encoding="utf-8") as f:
                cfg = cls.from_dict(yaml.safe_load(f) or {})

        # --- environment overrides (always win) ---
        if v := os.environ.get("PARITOK_API_KEY"):
            cfg.paritok.api_key = v
        if v := os.environ.get("PARITOK_BASE_URL"):
            cfg.paritok.base_url = v
        if v := os.environ.get("GEMINI_API_KEY"):
            cfg.upstream.api_key = v
        if v := os.environ.get("SEGPILOT_UPSTREAM_MODEL"):
            cfg.upstream.model = v
        if v := os.environ.get("SEGPILOT_POLICY"):
            cfg.policy = cls._merge(cfg.policy, {"mode": v})
        return cfg

    def to_paritok_config(self):
        """Build the Paritok config object our pipeline runs on.

        We deliberately set `compression.min_tokens` to 0 and enforce our own
        gate instead: SEGPILOT decides per segment whether a compression is
        worth it, and needs Paritok to honour the level we pass rather than
        silently skipping.
        """
        from paritok.config import ParitokConfig

        return ParitokConfig.from_dict(
            {
                "use_gpu_server": self.paritok.use_gpu_server,
                "gpu_server": {
                    "base_url": self.paritok.base_url,
                    "model": self.paritok.model,
                    "api_key": self.paritok.api_key,
                    "timeout": self.paritok.timeout,
                },
                "compression": {
                    "min_tokens": 0,
                    "max_tokens": self.policy.max_tokens,
                    # We evaluate effectiveness ourselves via the retention
                    # guard; Paritok's blanket refusal threshold would discard
                    # legitimately small-but-safe wins.
                    "refusal_threshold": 0.0,
                },
            }
        )
