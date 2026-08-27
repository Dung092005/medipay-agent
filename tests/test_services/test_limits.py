import pytest

from src.api.limits import InMemoryCostQuota, InMemoryRateLimiter


@pytest.mark.asyncio
async def test_cost_quota_rejects_usage_over_daily_budget():
    quota = InMemoryCostQuota(limit=100, window_seconds=3600)
    assert await quota.allow("user-1", 60)
    assert await quota.allow("user-1", 40)
    assert not await quota.allow("user-1", 1)
    assert await quota.allow("user-2", 100)


@pytest.mark.asyncio
async def test_request_rate_and_cost_limits_are_independent():
    rate = InMemoryRateLimiter(limit=1, window_seconds=3600)
    quota = InMemoryCostQuota(limit=100, window_seconds=3600)
    assert await rate.allow("key")
    assert not await rate.allow("key")
    assert await quota.allow("key", 100)
