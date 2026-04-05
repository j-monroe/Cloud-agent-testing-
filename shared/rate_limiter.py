"""
Token bucket rate limiter backed by Redis.
Ensures the platform stays within SEC fair access limits (≤10 req/sec).
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional

import redis.asyncio as aioredis

from shared.logger import get_logger

logger = get_logger(__name__)


class TokenBucketRateLimiter:
    """
    Async Redis-backed token bucket rate limiter.
    Tokens refill at `rate` per second up to `capacity`.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        key: str = "edgar:rate_limit:global",
        rate: float = 8.0,
        capacity: int = 10,
    ) -> None:
        self.redis = redis_client
        self.key = key
        self.rate = rate
        self.capacity = capacity
        self._script_sha: Optional[str] = None

    # Lua script for atomic token bucket operations
    LUA_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1]) or capacity
local last_refill = tonumber(data[2]) or now

local elapsed = math.max(0, now - last_refill)
local new_tokens = math.min(capacity, tokens + elapsed * rate)

if new_tokens >= requested then
    new_tokens = new_tokens - requested
    redis.call('HMSET', key, 'tokens', new_tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 60)
    return 1
else
    redis.call('HMSET', key, 'tokens', new_tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 60)
    return 0
end
"""

    async def _load_script(self) -> str:
        if self._script_sha is None:
            self._script_sha = await self.redis.script_load(self.LUA_SCRIPT)
        return self._script_sha

    async def acquire(self, tokens: int = 1, timeout: float = 30.0) -> None:
        """
        Acquire `tokens` from the bucket, blocking until available.
        Raises TimeoutError if tokens are not available within `timeout` seconds.
        """
        sha = await self._load_script()
        deadline = time.monotonic() + timeout
        while True:
            now = time.time()
            result = await self.redis.evalsha(
                sha,
                1,
                self.key,
                self.capacity,
                self.rate,
                now,
                tokens,
            )
            if result == 1:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Rate limit: could not acquire {tokens} tokens within {timeout}s"
                )
            wait = tokens / self.rate
            await asyncio.sleep(min(wait, 0.5))


class SimpleRateLimiter:
    """
    In-process token bucket for environments without Redis.
    Not suitable for multi-process deployments.
    """

    def __init__(self, rate: float = 8.0, capacity: int = 10) -> None:
        self.rate = rate
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("Rate limit timeout")
                wait = (tokens - self._tokens) / self.rate
                await asyncio.sleep(min(wait, 0.1))
