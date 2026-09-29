"""Celery-приложение воркера."""
from __future__ import annotations

from celery import Celery

from core.config import CELERY_BROKER_URL

app = Celery(
    "osint_person_search",
    broker=CELERY_BROKER_URL,
    backend=CELERY_BROKER_URL,
)

app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=600,
    task_soft_time_limit=540,
    task_default_queue="search",
    result_expires=3600 * 24,
)

from worker import tasks  # noqa: E402,F401  (регистрация задач)
