"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set) → mọi worker / instance dùng chung,
    vẫn còn sau khi restart agent
"""
import hashlib
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _redis_key(key: str) -> str:
    # Không lưu API key thô trong Redis — chỉ lưu hash.
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:32]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    rkey = _redis_key(key)
    now = time.time()

    # Bước 1: xoá request cũ ngoài cửa sổ + đếm (atomic trong 1 transaction)
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)
    pipe.zcard(rkey)
    _, count = pipe.execute()

    if count >= limit:
        oldest = r.zrange(rkey, 0, 0, withscores=True)
        retry = WINDOW_SECONDS
        if oldest:
            retry = max(1, int(oldest[0][1] + WINDOW_SECONDS - now) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {limit} requests / {WINDOW_SECONDS}s",
            headers={"Retry-After": str(retry)},
        )

    # Bước 2: ghi nhận request này
    pipe = r.pipeline(transaction=True)
    pipe.zadd(rkey, {f"{now}-{uuid.uuid4().hex[:8]}": now})
    pipe.expire(rkey, WINDOW_SECONDS + 5)
    pipe.execute()
