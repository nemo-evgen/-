"""API-шлюз (FastAPI): кейсы, поисковые джобы, досье, статика мини-UI."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from core.config import EXEC_INLINE
from core.db import init_db, session
from core.models import Account, AuditLog, Case, Fact, Person, SearchJob, utcnow
from core.routing import INPUT_TYPES, collectors_for
from services.api.app import schemas
from services.api.app.tasks_client import enqueue_search_job

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="OSINT Person Search",
    version="0.1.0",
    description="Поиск информации об человеке из открытых источников (OSINT).",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _case_out(c: Case) -> schemas.CaseOut:
    return schemas.CaseOut(
        id=c.id, name=c.name, legal_basis=c.legal_basis, created_at=_iso(c.created_at) or ""
    )


def _job_out(j: SearchJob) -> schemas.JobOut:
    return schemas.JobOut(
        id=j.id,
        case_id=j.case_id,
        input_type=j.input_type,
        input_value=j.input_value,
        hints=j.hints or {},
        status=j.status,
        error=j.error,
        warnings=j.warnings or [],
        summary=j.summary or {},
        created_at=_iso(j.created_at) or "",
        started_at=_iso(j.started_at),
        finished_at=_iso(j.finished_at),
    )


# ---------- health ----------
@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "exec_inline": EXEC_INLINE}


# ---------- cases ----------
@app.post("/api/cases", response_model=schemas.CaseOut)
def create_case(body: schemas.CaseCreate) -> schemas.CaseOut:
    with session() as s:
        case = Case(name=body.name.strip(), legal_basis=body.legal_basis)
        s.add(case)
        s.add(AuditLog(action="case.created", detail={"name": case.name}))
        s.flush()
        return _case_out(case)


@app.get("/api/cases", response_model=list[schemas.CaseOut])
def list_cases() -> list[schemas.CaseOut]:
    with session() as s:
        rows = s.execute(select(Case).order_by(Case.id.desc())).scalars().all()
        return [_case_out(c) for c in rows]


@app.get("/api/cases/{case_id}", response_model=schemas.DossierOut)
def get_dossier(case_id: int) -> schemas.DossierOut:
    with session() as s:
        case = s.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "кейс не найден")
        jobs = (
            s.execute(select(SearchJob).where(SearchJob.case_id == case_id).order_by(SearchJob.id.desc()))
            .scalars()
            .all()
        )
        persons = (
            s.execute(select(Person).where(Person.case_id == case_id).order_by(Person.id))
            .scalars()
            .all()
        )
        accounts = (
            s.execute(select(Account).where(Account.case_id == case_id)).scalars().all()
        )
        facts = s.execute(select(Fact).where(Fact.case_id == case_id).order_by(Fact.id.desc())).scalars().all()

        acc_by_person: dict[int, list[schemas.AccountOut]] = {}
        acc_orphan: list[schemas.AccountOut] = []
        for a in accounts:
            out = schemas.AccountOut(
                id=a.id, platform=a.platform, handle=a.handle, url=a.url, confidence=a.confidence
            )
            if a.person_id:
                acc_by_person.setdefault(a.person_id, []).append(out)
            else:
                acc_orphan.append(out)

        facts_by_person: dict[int, list[schemas.FactOut]] = {}
        facts_orphan: list[schemas.FactOut] = []
        for f in facts:
            out = schemas.FactOut(
                id=f.id,
                kind=f.kind,
                value=f.value or {},
                source_url=f.source_url,
                confidence=f.confidence,
                captured_at=_iso(f.captured_at) or "",
                job_id=f.job_id,
            )
            if f.person_id:
                facts_by_person.setdefault(f.person_id, []).append(out)
            else:
                facts_orphan.append(out)

        person_outs = [
            schemas.PersonOut(
                id=p.id,
                display_name=p.display_name,
                confidence=p.confidence,
                accounts=acc_by_person.get(p.id, []),
                facts=facts_by_person.get(p.id, []),
            )
            for p in persons
        ]
        # «бесхабные» факты показываем при первом человеке кейса
        if person_outs and (facts_orphan or acc_orphan):
            person_outs[0].accounts.extend(acc_orphan)
            person_outs[0].facts.extend(facts_orphan)

        return schemas.DossierOut(
            case=_case_out(case),
            jobs=[_job_out(j) for j in jobs],
            persons=person_outs,
        )


# ---------- searches ----------
@app.post("/api/cases/{case_id}/searches", response_model=schemas.JobOut)
def create_search(case_id: int, body: schemas.SearchCreate) -> schemas.JobOut:
    if body.input_type not in INPUT_TYPES:
        raise HTTPException(400, f"неизвестный тип входа: {body.input_type}")
    if body.input_type in ("name",) and not (body.hints.get("full_name") or body.input_value):
        raise HTTPException(400, "для поиска по ФИО укажите input_value или hints.full_name")
    if body.collectors is not None and not collectors_for(body.input_type, body.collectors):
        raise HTTPException(400, "ни один из указанных коллекторов не подходит")

    with session() as s:
        case = s.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "кейс не найден")
        hints = dict(body.hints or {})
        if body.input_type == "name" and not hints.get("full_name"):
            hints["full_name"] = body.input_value
        job = SearchJob(
            case_id=case_id,
            input_type=body.input_type,
            input_value=body.input_value.strip(),
            hints=hints,
            collectors=body.collectors,
            status="pending",
        )
        s.add(job)
        s.add(
            AuditLog(
                case_id=case_id,
                action="search.created",
                detail={"input_type": body.input_type, "input_value": job.input_value},
            )
        )
        s.flush()
        job_id = job.id
        out = _job_out(job)

    mode = enqueue_search_job(job_id)  # вне транзакции
    if mode == "inline":
        with session() as s:
            j = s.get(SearchJob, job_id)
            if j is not None:
                out = _job_out(j)
    return out


@app.get("/api/jobs/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: int) -> schemas.JobOut:
    with session() as s:
        job = s.get(SearchJob, job_id)
        if job is None:
            raise HTTPException(404, "джоб не найден")
        return _job_out(job)


# ---------- UI ----------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
