from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "sofia_school_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Sofia",
    enable_utc=True,
    beat_schedule={
        # Scheduled scraping tasks will be added in Phase 2
    },
)
