import redis
from app.config import settings

redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis():
    # always read the module global at call time (tests replace it with fakeredis)
    return redis_client
