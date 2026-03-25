"""Sliding-window token rate limiter.

Tracks tokens sent in the last 60 seconds. Before each request, calculates
how long to sleep so the window clears enough capacity for the new tokens.
This prevents hitting the API rate limit rather than recovering from it.

Usage:
    limiter = TokenRateLimiter(tokens_per_minute=50_000)
    limiter.acquire(est_tokens)   # sleeps if needed, then returns
    # ... make API call ...
    limiter.record(actual_tokens) # update with real token count
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Entry:
    timestamp: float
    tokens: int


class TokenRateLimiter:
    def __init__(self, tokens_per_minute: int = 50_000, window_seconds: float = 60.0):
        self.limit = tokens_per_minute
        self.window = window_seconds
        self._log: deque[_Entry] = deque()
        self._reserved: int = 0  # tokens we've announced but not confirmed

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.window
        while self._log and self._log[0].timestamp < cutoff:
            self._log.popleft()

    def _used(self) -> int:
        self._prune()
        return sum(e.tokens for e in self._log) + self._reserved

    def acquire(self, tokens: int, label: str = "") -> None:
        """Block until there is capacity for `tokens` tokens, then reserve them."""
        while True:
            self._prune()
            used = self._used()
            available = self.limit - used
            if tokens <= available:
                self._reserved += tokens
                return

            # Find how long until enough tokens expire to fit our request
            needed = tokens - available
            accumulated = 0
            sleep_until: float | None = None
            for entry in self._log:
                accumulated += entry.tokens
                if accumulated >= needed:
                    sleep_until = entry.timestamp + self.window
                    break

            if sleep_until is None:
                # Reserved tokens are the bottleneck — just wait a bit
                sleep_until = time.monotonic() + 5.0

            wait = max(0.0, sleep_until - time.monotonic())
            if wait > 0:
                tag = f" [{label}]" if label else ""
                print(f"        rate limit{tag}: used {used}/{self.limit} tpm — sleeping {wait:.0f}s")
                time.sleep(wait + 0.5)  # +0.5s buffer

    def record(self, actual_tokens: int) -> None:
        """Call after the request completes with the real token count."""
        self._reserved = max(0, self._reserved - actual_tokens)
        self._log.append(_Entry(timestamp=time.monotonic(), tokens=actual_tokens))


# Global limiter instances, keyed by model name
_limiters: dict[str, TokenRateLimiter] = {}

# Approximate TPM limits by model family
_TPM_LIMITS: dict[str, int] = {
    "haiku": 50_000,
    "sonnet": 30_000,
    "opus": 10_000,
}


def get_limiter(model: str) -> TokenRateLimiter:
    """Return (or create) a shared limiter for the given model."""
    if model not in _limiters:
        tpm = 50_000  # default
        model_lower = model.lower()
        for name, limit in _TPM_LIMITS.items():
            if name in model_lower:
                tpm = limit
                break
        _limiters[model] = TokenRateLimiter(tokens_per_minute=tpm)
    return _limiters[model]
