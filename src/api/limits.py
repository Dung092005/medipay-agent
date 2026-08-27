from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Protocol


class RateLimiter(Protocol):
    async def allow(self, key: str) -> bool:
        ...

    async def close(self) -> None:
        ...


class CostQuota(Protocol):
    async def allow(self, key: str, units: int) -> bool:
        ...

    async def close(self) -> None:
        ...


class InMemoryRateLimiter:
    """Small single-process guard for local/dev deployments."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            if len(self._events) > 2048:
                self._events = {
                    name: values for name, values in self._events.items() if values and values[-1] > cutoff
                }
            return True

    async def close(self) -> None:
        return None


class RedisRateLimiter:
    """Distributed sliding-window limiter for multi-replica deployments."""

    _SCRIPT = """
    local now = tonumber(ARGV[1])
    local cutoff = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local ttl = tonumber(ARGV[4])
    redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff)
    local count = redis.call('ZCARD', KEYS[1])
    if count >= limit then return 0 end
    redis.call('ZADD', KEYS[1], now, ARGV[1] .. ':' .. redis.call('INCR', KEYS[2]))
    redis.call('EXPIRE', KEYS[1], ttl)
    redis.call('EXPIRE', KEYS[2], ttl)
    return 1
    """

    def __init__(self, *, url: str, limit: int, window_seconds: int) -> None:
        import redis.asyncio as redis

        self.limit = limit
        self.window_seconds = window_seconds
        self.client = redis.from_url(url, decode_responses=False)

    async def allow(self, key: str) -> bool:
        now = time.time_ns() // 1_000_000
        window = self.window_seconds * 1000
        result = await self.client.eval(
            self._SCRIPT,
            2,
            f"medipay:rate:{key}",
            f"medipay:rate-sequence:{key}",
            now,
            now - window,
            self.limit,
            self.window_seconds + 1,
        )
        return bool(result)

    async def close(self) -> None:
        await self.client.aclose()


class InMemoryCostQuota:
    """Bounded daily usage units for local/dev deployments."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[tuple[float, int]]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str, units: int) -> bool:
        if units <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0][0] <= cutoff:
                events.popleft()
            used = sum(item[1] for item in events)
            if used + units > self.limit:
                return False
            events.append((now, units))
            return True

    async def close(self) -> None:
        return None


class RedisCostQuota:
    """Atomic distributed usage quota backed by one Redis counter per key."""

    _SCRIPT = """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local units = tonumber(ARGV[1])
    local limit = tonumber(ARGV[2])
    if current + units > limit then return 0 end
    redis.call('INCRBY', KEYS[1], units)
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
    return 1
    """

    def __init__(self, *, url: str, limit: int, window_seconds: int) -> None:
        import redis.asyncio as redis

        self.limit = limit
        self.window_seconds = window_seconds
        self.client = redis.from_url(url, decode_responses=False)

    async def allow(self, key: str, units: int) -> bool:
        result = await self.client.eval(
            self._SCRIPT,
            1,
            f"medipay:cost:{key}",
            int(units),
            self.limit,
            self.window_seconds,
        )
        return bool(result)

    async def close(self) -> None:
        await self.client.aclose()
