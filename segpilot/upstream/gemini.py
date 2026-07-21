"""Upstream adapter for Gemini's OpenAI-compatible endpoint.

WHY THIS EXISTS
---------------
Paritok's own proxy cannot target Gemini. `proxy/server.py:418` builds the
upstream URL as `f"{openai_base_url}/v1/chat/completions"`, hardcoding the
version segment. Gemini's OpenAI-compatible base is already versioned:

    https://generativelanguage.googleapis.com/v1beta/openai

so the proxy would request `.../v1beta/openai/v1/chat/completions` and get a
404. The same breaks several OpenRouter, Groq and Together deployments. Filed as
finding #6; this adapter is the workaround.

It is deliberately thin -- one POST, no SDK. The request and response are both
already OpenAI-shaped, so there is nothing to translate; treating the base URL
as a complete prefix is the entire fix.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from segpilot.config import UpstreamConfig


class UpstreamError(RuntimeError):
    """Upstream refused or failed. Carries the status so callers can react to
    rate limiting (429) differently from a genuine bad request (400)."""

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429

    @property
    def is_retryable(self) -> bool:
        return self.status is not None and (self.status == 429 or self.status >= 500)


@dataclass
class UpstreamResult:
    """One completion, plus what it cost."""

    message: dict                      # the assistant message, OpenAI-shaped
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def tool_calls(self) -> list[dict]:
        return self.message.get("tool_calls") or []

    @property
    def content(self) -> str:
        return self.message.get("content") or ""

    @property
    def finish_reason(self) -> str:
        try:
            return self.raw["choices"][0].get("finish_reason") or ""
        except (KeyError, IndexError, TypeError):
            return ""


class GeminiUpstream:
    """Minimal OpenAI-compatible client pointed at Gemini.

    Retries only on rate limits and 5xx. A 400 means the request is malformed
    and retrying would just burn quota, which matters on a free tier.
    """

    def __init__(self, config: UpstreamConfig, *, max_retries: int = 4):
        self.config = config
        self.max_retries = max_retries
        if not config.api_key:
            raise UpstreamError(
                "No upstream API key. Set GEMINI_API_KEY in .env or the environment."
            )

    @property
    def endpoint(self) -> str:
        # The base URL is a complete prefix. No version segment is appended --
        # that is the bug this adapter exists to avoid.
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> UpstreamResult:
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        last: UpstreamError | None = None
        for attempt in range(self.max_retries):
            t0 = time.time()
            try:
                resp = httpx.post(
                    self.endpoint, json=payload, headers=headers,
                    timeout=self.config.timeout,
                )
            except httpx.RequestError as e:
                last = UpstreamError(f"upstream unreachable: {e}")
            else:
                if resp.status_code == 200:
                    return self._parse(resp.json(), time.time() - t0)
                last = UpstreamError(
                    f"upstream returned {resp.status_code}",
                    status=resp.status_code,
                    body=resp.text[:500],
                )
                if not last.is_retryable:
                    raise last

            if attempt < self.max_retries - 1:
                time.sleep(self._backoff(last, attempt))

        raise last or UpstreamError("upstream failed")

    @staticmethod
    def _backoff(error: UpstreamError, attempt: int) -> float:
        """How long to wait before retrying.

        Rate limits on Gemini's free tier are enforced per MINUTE, so the usual
        2/4/8-second exponential backoff exhausts all retries inside a single
        window and gives up while still rate limited. A 429 therefore waits out
        most of a minute; ordinary 5xx keeps the short exponential schedule.
        """
        if error.is_rate_limit:
            return min(35.0 + 20.0 * attempt, 75.0)
        return min(2.0 ** attempt * 2.0, 30.0)

    @staticmethod
    def _parse(data: dict, elapsed: float) -> UpstreamResult:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise UpstreamError(f"unexpected response shape: {e}") from e
        usage = data.get("usage") or {}
        return UpstreamResult(
            message=message,
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
            raw=data,
            elapsed_s=round(elapsed, 3),
        )

    def check(self) -> tuple[bool, str]:
        """Cheap liveness probe. Returns (ok, message)."""
        try:
            res = self.complete([{"role": "user", "content": "reply with: ok"}],
                                max_tokens=10)
            return True, f"{self.config.model} responded: {res.content.strip()[:40]!r}"
        except UpstreamError as e:
            return False, f"{e} {e.body}".strip()
