"""Token-bucket rate limiter in Redis, made atomic with a Lua script.

Why Lua: "read tokens -> decide -> write tokens" as three separate commands is a race. Two
concurrent requests could both read "1 token left" and both be allowed. Redis runs a Lua
script as one atomic step, so the check-and-decrement can't interleave.

The script uses Redis' own clock (``TIME``), so API servers with skewed clocks agree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from redis.asyncio import Redis

TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_sec = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])

local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000

local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now

tokens = math.min(capacity, tokens + (now - ts) * refill_per_sec)
local allowed = 0
local retry_after = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after = (cost - tokens) / refill_per_sec
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, math.ceil(capacity / refill_per_sec * 1000) + 1000)
return {allowed, tostring(tokens), tostring(retry_after)}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class TokenBucketLimiter:
    def __init__(self, redis: Redis, capacity: int, refill_per_sec: float) -> None:
        self._script = redis.register_script(TOKEN_BUCKET_LUA)
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec

    async def hit(self, identity: str, cost: int = 1) -> RateLimitResult:
        allowed, tokens, retry = await self._script(
            keys=[f"ratelimit:{identity}"], args=[self.capacity, self.refill_per_sec, cost]
        )
        return RateLimitResult(
            allowed=bool(int(allowed)),
            remaining=int(float(tokens)),
            retry_after_seconds=math.ceil(float(retry)),
        )
