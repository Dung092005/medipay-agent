from __future__ import annotations

import pytest

from src.services.circuit import AsyncCircuitBreaker, CircuitOpenError


@pytest.mark.asyncio
async def test_circuit_opens_after_bounded_failures() -> None:
    breaker = AsyncCircuitBreaker(failure_threshold=2, cooldown_seconds=60)

    async def fail():
        raise RuntimeError("provider")

    with pytest.raises(RuntimeError):
        await breaker.call(fail)
    with pytest.raises(RuntimeError):
        await breaker.call(fail)
    with pytest.raises(CircuitOpenError):
        await breaker.call(fail)


@pytest.mark.asyncio
async def test_success_resets_circuit_failures() -> None:
    breaker = AsyncCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return calls

    assert await breaker.call(operation) == 1
    assert not breaker.is_open
