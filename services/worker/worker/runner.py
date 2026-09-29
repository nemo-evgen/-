"""Раннер поискового джобы: маршрутизация → коллекторы → нормализация → БД.

Вызывается из Celery-задачи или напрямую (тесты / EXEC_INLINE=1).
"""
from __future__ import annotations

import logging

from core import (  # noqa: F401  (регистрация коллекторов в реестре)
    dorks_collector,
    photo_collector,
    tg_collector,
    username_collector,
    vk_collector,
)
from core.collector_base import REGISTRY
from core.contract import CollectorInput
from core.db import init_db, session
from core.models import AuditLog, Fact, SearchJob, utcnow
from core.normalize import ingest
from core.routing import collectors_for

log = logging.getLogger("runner")


def run_job(job_id: int) -> dict:
    init_db()
    try:
        return _run(job_id)
    except Exception as exc:  # noqa: BLE001 — джоба не должна «повисать» в running
        log.exception("job %s failed", job_id)
        with session() as s:
            job = s.get(SearchJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error = f"{exc.__class__.__name__}: {exc}"
                job.finished_at = utcnow()
        return {"error": str(exc)}


def _run(job_id: int) -> dict:
    with session() as s:
        job = s.get(SearchJob, job_id)
        if job is None:
            return {"error": f"job {job_id} not found"}
        if job.status == "done":
            return {"skipped": True, "summary": job.summary}

        job.status = "running"
        job.started_at = utcnow()
        s.add(
            AuditLog(
                case_id=job.case_id,
                action="job.started",
                detail={"job_id": job.id, "input_type": job.input_type},
            )
        )
        s.commit()

        names = collectors_for(job.input_type, job.collectors)
        warnings: list[str] = []
        results = []

        for name in names:
            fn = REGISTRY.get(name)
            if fn is None:
                warnings.append(f"коллектор «{name}» не зарегистрирован")
                continue
            payload = CollectorInput(
                case_id=job.case_id,
                job_id=job.id,
                input_type=job.input_type,
                value=job.input_value,
                hints=job.hints or {},
            )
            try:
                result = fn(payload)
            except Exception as exc:  # noqa: BLE001 — изоляция падений источников
                warnings.append(f"{name}: {exc.__class__.__name__}: {exc}")
                log.exception("collector %s failed (job %s)", name, job_id)
                continue
            results.append(result)
            warnings.extend(result.warnings)  # коллекторы сами пишут свой префикс

        summary = ingest(s, job, results) if results else {}
        job.warnings = warnings
        job.summary = summary
        if results:
            job.status = "done"
            job.error = None
        else:
            job.status = "failed"
            job.error = "; ".join(warnings) or "нет результатов: неизвестный вход"
        job.finished_at = utcnow()
        # резолвер работает в отдельной сессии → данные джобы должны быть закоммичены
        s.commit()

        # entity resolution: мердж/скоринг/граф/очередь проверки (best-effort)
        if job.status == "done":
            try:
                from core.resolver import resolve_case

                res_summary = resolve_case(job.case_id)
                summary = {**summary, "resolver": res_summary}
                job.summary = summary
            except Exception:  # noqa: BLE001 — резолвер не должен ломать джобу
                log.exception("resolver failed (job %s)", job_id)

        s.add(
            AuditLog(
                case_id=job.case_id,
                action="job.finished",
                detail={"job_id": job.id, "status": job.status, "summary": summary},
            )
        )
        s.flush()

        # полнотекстовая индексация (best-effort; при выключенном OpenSearch — no-op)
        if job.status == "done":
            try:
                from core.search import index_facts

                new_facts = (
                    s.query(Fact).filter(Fact.job_id == job.id).all()
                )
                index_facts(new_facts)
            except Exception:  # noqa: BLE001 — поиск не должен ломать джобу
                log.exception("indexing failed (job %s)", job_id)

        return {"status": job.status, "summary": summary, "warnings": warnings}
