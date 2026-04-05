"""Tests for the in-process rate limiter."""
import asyncio
import time
import pytest
from shared.rate_limiter import SimpleRateLimiter


@pytest.mark.asyncio
async def test_acquire_within_capacity():
    """Should acquire immediately when tokens are available."""
    limiter = SimpleRateLimiter(rate=10.0, capacity=10)
    start = time.monotonic()
    await limiter.acquire(1)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_acquire_respects_rate():
    """Should wait when bucket is exhausted."""
    limiter = SimpleRateLimiter(rate=10.0, capacity=2)
    await limiter.acquire(2)  # Exhaust bucket
    start = time.monotonic()
    await limiter.acquire(1)  # Should wait ~0.1s
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # At least 50ms


@pytest.mark.asyncio
async def test_acquire_timeout():
    """Should raise TimeoutError when tokens not available in time."""
    limiter = SimpleRateLimiter(rate=0.1, capacity=1)
    await limiter.acquire(1)  # Exhaust
    with pytest.raises(TimeoutError):
        await limiter.acquire(1, timeout=0.01)


@pytest.mark.asyncio
async def test_acquire_multiple_tokens():
    """Should be able to acquire multiple tokens at once."""
    limiter = SimpleRateLimiter(rate=100.0, capacity=10)
    start = time.monotonic()
    await limiter.acquire(5)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_refill_over_time():
    """Tokens should refill over time."""
    limiter = SimpleRateLimiter(rate=100.0, capacity=5)
    await limiter.acquire(5)  # Exhaust
    await asyncio.sleep(0.05)  # Wait for refill (5 tokens at 100/s = 0.05s)
    # Should succeed without timeout
    await limiter.acquire(1, timeout=1.0)
