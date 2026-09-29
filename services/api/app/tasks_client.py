"""Постановка джобы в очередь (или инлайн-выполнение в dev: EXEC_INLINE=1)."""
from __future__ import annotations

import logging

from core.config import CELERY_BROKER_URL, EXEC_INLINE

log = logging.getLogger("tasks_client")
_client = None

TASK_NAME = "worker.tasks.run_search_job"


def _celery():
    global _client
    if _client is None:
        from celery import Celery

        _client = Celery(broker=CELERY_BROKER_URL, backend=CELERY_BROKER_URL)
    return _client


def enqueue_search_job(job_id: int) -> str:
    if EXEC_INLINE:
        # Dev/test-режим без Redis: воркер должен быть доступен на PYTHONPATH.
        from worker.runner import run_job  # отложенный импорт

        run_job(job_id)
        return "inline"

    _celery().send_task(TASK_NAME, kwargs={"job_id": job_id}, queue="search")
    return "queued"
