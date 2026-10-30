import json
import logging
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from app.redis_client import get_redis

log = logging.getLogger(__name__)
PREFIX = "cache:events:"


def cache_get(key: str):
    try:
        v = get_redis().get(key)
        return json.loads(v) if v else None
    except RedisError:
        return None


def cache_set(key: str, data, ttl: int = 300) -> None:
    try:
        get_redis().setex(key, ttl, json.dumps(jsonable_encoder(data)))
    except RedisError:
        log.warning("cache set failed")


def clear_event_cache() -> None:
    try:
        r = get_redis()
        for k in r.scan_iter(PREFIX + "*"):
            r.delete(k)
    except RedisError:
        log.warning("cache clear failed")
