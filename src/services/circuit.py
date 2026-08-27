"""Small async circuit breaker for provider stages."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    """Raised while a provider is cooling down after repeated failures."""


class AsyncCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.1, float(cooldown_seconds))
        self._failures = 0
        self._opened_until = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self._opened_until

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            if self.is_open:
                raise CircuitOpenError("provider circuit is open")
        try:
            result = await operation()
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._lock:
                self._failures += 1
                if self._failures >= self.failure_threshold:
                    self._opened_until = time.monotonic() + self.cooldown_seconds
            raise
        async with self._lock:
            self._failures = 0
            self._opened_until = 0.0
        return result
