from taskiq_redis import ListQueueBroker

from src.core.config import settings

broker = ListQueueBroker(
    url=settings.redis_url,
    queue_name=settings.TASKIQ_QUEUE_NAME,
)
