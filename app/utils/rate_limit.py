from fastapi import HTTPException
from redis.exceptions import RedisError
from app.redis_client import get_redis


def hit(key: str, limit: int, window: int = 60) -> None:
    r = get_redis()
    try:
        count = r.incr(key)
        if count == 1:
            r.expire(key, window)
        if count > limit:
            ttl = r.ttl(key)
            ttl = ttl if ttl and ttl > 0 else window
            raise HTTPException(
                429,
                {"message": f"Too many requests. Try again in {ttl} seconds", "retry_after_seconds": ttl},
                headers={"Retry-After": str(ttl)},
            )
    except RedisError:
        pass
