"""Celery-задачи воркера."""
from __future__ import annotations

from worker.celery_app import app


@app.task(name="worker.tasks.run_search_job", bind=True)
def run_search_job(self, job_id: int) -> dict:  # noqa: ANN001
    from worker.runner import run_job

    return run_job(job_id)
