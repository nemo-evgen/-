"""API-шлюз (FastAPI): кейсы, поисковые джобы, досье, загрузка фото, статика мини-UI."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from core import config
from core.db import init_db, session
from core.models import Account, AuditLog, Case, Fact, Person, ReviewItem, SearchJob
from core.routing import INPUT_TYPES, collectors_for
from services.api.app import rbac, schemas
from services.api.app.tasks_client import enqueue_search_job

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="OSINT Person Search",
    version="0.3.0",
    description="Поиск информации об человеке из открытых источников (OSINT).",
)


@app.middleware("http")
async def _rbac_middleware(request, call_next):
    """Ролевой доступ по API_KEYS (см. services/api/app/rbac.py)."""
    deny = rbac.enforce(request)
    if deny is not None:
        return deny
    return await call_next(request)


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
    return {"status": "ok", "exec_inline": config.EXEC_INLINE}


def _start_job(
    case_id: int,
    input_type: str,
    input_value: str,
    hints: dict,
    collectors: list[str] | None = None,
) -> schemas.JobOut:
    """Создаёт джобу в БД (транзакция закрыта) и ставит в очередь."""
    with session() as s:
        case = s.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "кейс не найден")
        job = SearchJob(
            case_id=case_id,
            input_type=input_type,
            input_value=input_value,
            hints=hints,
            collectors=collectors,
            status="pending",
        )
        s.add(job)
        s.add(
            AuditLog(
                case_id=case_id,
                action="search.created",
                detail={"input_type": input_type, "input_value": input_value[:300]},
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


# ---------- cases ----------
@app.post("/api/cases", response_model=schemas.CaseOut)
def create_case(body: schemas.CaseCreate) -> schemas.CaseOut:
    with session() as s:
        case = Case(name=body.name.strip(), legal_basis=body.legal_basis)
        s.add(case)
        s.flush()
        s.add(AuditLog(case_id=case.id, action="case.created", detail={"name": case.name}))
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
                artifacts=f.artifacts or [],
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
                meta=p.meta or {},
                accounts=acc_by_person.get(p.id, []),
                facts=facts_by_person.get(p.id, []),
            )
            for p in persons
        ]
        # «бесхабные» факты показываем при первом человеке кейса
        if person_outs and (facts_orphan or acc_orphan):
            person_outs[0].accounts.extend(acc_orphan)
            person_outs[0].facts.extend(facts_orphan)

        reviews = (
            s.query(ReviewItem)
            .filter(ReviewItem.case_id == case_id, ReviewItem.status == "pending")
            .order_by(ReviewItem.id.desc())
            .all()
        )
        review_outs = [
            schemas.ReviewOut(
                id=r.id,
                kind=r.kind,
                payload=r.payload or {},
                status=r.status,
                created_at=_iso(r.created_at) or "",
            )
            for r in reviews
        ]

        return schemas.DossierOut(
            case=_case_out(case),
            jobs=[_job_out(j) for j in jobs],
            persons=person_outs,
            reviews=review_outs,
        )


# ---------- entity resolution / граф / проверки ----------
@app.post("/api/cases/{case_id}/resolve")
def resolve_case(case_id: int) -> dict:
    """Ручной запуск сшивки: мердж, скоринг, граф, очередь проверки."""
    from core.resolver import resolve_case as run_resolve

    with session() as s:
        if s.get(Case, case_id) is None:
            raise HTTPException(404, "кейс не найден")
    return run_resolve(case_id)


@app.get("/api/cases/{case_id}/graph")
def get_graph(case_id: int) -> dict:
    from core.resolver import graph_payload

    with session() as s:
        if s.get(Case, case_id) is None:
            raise HTTPException(404, "кейс не найден")
    return graph_payload(case_id)


@app.post("/api/reviews/{review_id}/decision")
def review_decision(review_id: int, body: schemas.DecisionIn) -> dict:
    from core.resolver import decide_review

    try:
        return decide_review(review_id, body.action)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


# ---------- searches ----------
@app.post("/api/cases/{case_id}/searches", response_model=schemas.JobOut)
def create_search(case_id: int, body: schemas.SearchCreate) -> schemas.JobOut:
    if body.input_type not in INPUT_TYPES:
        raise HTTPException(400, f"неизвестный тип входа: {body.input_type}")
    if body.input_type in ("name",) and not (body.hints.get("full_name") or body.input_value):
        raise HTTPException(400, "для поиска по ФИО укажите input_value или hints.full_name")
    if body.collectors is not None and not collectors_for(body.input_type, body.collectors):
        raise HTTPException(400, "ни один из указанных коллекторов не подходит")

    hints = dict(body.hints or {})
    if body.input_type == "name" and not hints.get("full_name"):
        hints["full_name"] = body.input_value
    return _start_job(case_id, body.input_type, body.input_value.strip(), hints, body.collectors)


_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_SAFE_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|jpeg|png|webp|gif)$")


@app.post("/api/cases/{case_id}/photos", response_model=schemas.JobOut)
async def upload_photo(case_id: int, file: UploadFile = File(...)) -> schemas.JobOut:
    """Загрузка фото → файл в UPLOAD_DIR → джоба input_type=photo."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(400, f"недопустимый формат файла: {ext or 'без расширения'}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "пустой файл")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"файл больше {config.MAX_UPLOAD_BYTES} байт")

    with session() as s:
        if s.get(Case, case_id) is None:
            raise HTTPException(404, "кейс не найден")

    upload_dir = Path(config.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (upload_dir / name).write_bytes(data)
    return _start_job(case_id, "photo", f"upload://{name}", {})


@app.get("/api/files/{name}")
def get_file(name: str) -> FileResponse:
    """Отдача загруженных фото UI (строго свои имена: hex32 + ext)."""
    if not _SAFE_NAME.match(name):
        raise HTTPException(404, "не найдено")
    path = Path(config.UPLOAD_DIR) / name
    if not path.is_file():
        raise HTTPException(404, "не найдено")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: int) -> schemas.JobOut:
    with session() as s:
        job = s.get(SearchJob, job_id)
        if job is None:
            raise HTTPException(404, "джоб не найден")
        return _job_out(job)


# ---------- полнотекстовый поиск и снимки ----------
@app.get("/api/search")
def fulltext_search(q: str, case_id: int | None = None, limit: int = 50) -> dict:
    """Поиск по всем фактам: OpenSearch (если включён) или SQL-fallback."""
    from core.search import search as run_search

    return run_search(q, case_id=case_id, size=min(limit, 200))


@app.get("/api/snapshots")
def get_snapshot(ref: str):
    """Отдача снимка-доказательства по ref (file://… | minio://…)."""
    from fastapi.responses import Response

    from core.storage import open_snapshot

    data_ctype = open_snapshot(ref)
    if data_ctype is None:
        raise HTTPException(404, "снимок не найден")
    data, ctype = data_ctype
    return Response(content=data, media_type=ctype)


# ---------- аудит, метрики, бэкапы (Этап 4) ----------
@app.get("/api/audit")
def audit_log(case_id: int | None = None, limit: int = 100) -> dict:
    """Журнал действий (просмотр — роль viewer)."""
    limit = max(1, min(limit, 500))
    with session() as s:
        stmt = select(AuditLog)
        if case_id is not None:
            stmt = stmt.where(AuditLog.case_id == case_id)
        rows = (
            s.execute(stmt.order_by(AuditLog.id.desc()).limit(limit)).scalars().all()
        )
        return {
            "entries": [
                {
                    "id": r.id,
                    "case_id": r.case_id,
                    "action": r.action,
                    "detail": r.detail or {},
                    "created_at": _iso(r.created_at),
                }
                for r in rows
            ]
        }


@app.get("/metrics", response_class=Response)
def metrics() -> Response:
    """Prometheus text format (без аутентификации — по соглашению)."""
    with session() as s:
        jobs = dict(
            s.execute(
                select(SearchJob.status, func.count(SearchJob.id)).group_by(SearchJob.status)
            ).all()
        )
        counts = {}
        for label, model in (
            ("cases", Case),
            ("persons", Person),
            ("facts", Fact),
            ("accounts", Account),
        ):
            counts[label] = s.execute(select(func.count(model.id))).scalar() or 0
        reviews_pending = (
            s.execute(
                select(func.count(ReviewItem.id)).where(ReviewItem.status == "pending")
            ).scalar()
            or 0
        )
        resolver_runs = (
            s.execute(
                select(func.count(AuditLog.id)).where(AuditLog.action == "resolver.run")
            ).scalar()
            or 0
        )
    lines = [
        "# HELP osint_cases_total Кейсы в БД",
        "# TYPE osint_cases_total gauge",
        f"osint_cases_total {counts['cases']}",
        "# HELP osint_jobs_total Джобы поиска по статусам",
        "# TYPE osint_jobs_total gauge",
    ]
    for status in ("pending", "running", "done", "failed"):
        lines.append(f'osint_jobs_total{{status="{status}"}} {jobs.get(status, 0)}')
    lines += [
        "# HELP osint_persons_total Сшитые сущности-люди",
        "# TYPE osint_persons_total gauge",
        f"osint_persons_total {counts['persons']}",
        "# HELP osint_facts_total Факты",
        "# TYPE osint_facts_total gauge",
        f"osint_facts_total {counts['facts']}",
        "# HELP osint_accounts_total Привязанные аккаунты",
        "# TYPE osint_accounts_total gauge",
        f"osint_accounts_total {counts['accounts']}",
        "# HELP osint_reviews_pending Элементы очереди проверки, ожидающие решения",
        "# TYPE osint_reviews_pending gauge",
        f"osint_reviews_pending {reviews_pending}",
        "# HELP osint_resolver_runs Прогоны entity resolution",
        "# TYPE osint_resolver_runs counter",
        f"osint_resolver_runs {resolver_runs}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.post("/api/admin/backup")
def admin_backup() -> dict:
    """Бэкап БД в backups/ (sqlite: online-backup API; postgres: scripts/backup.sh)."""
    import sqlite3
    from datetime import datetime

    from core.db import get_engine

    eng = get_engine()
    if eng.dialect.name != "sqlite":
        raise HTTPException(
            400, "Postgres: используйте scripts/backup.sh (pg_dump)"
        )
    src_path = eng.url.database
    if not src_path or src_path == ":memory:" or not Path(src_path).exists():
        raise HTTPException(400, "sqlite-файл не найден — бэкап невозможен")

    out_dir = Path("backups")
    out_dir.mkdir(exist_ok=True)
    dest = out_dir / f"osint-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    raw = eng.raw_connection()  # sqlite3.Connection (возвращается в пул при close)
    dest_conn = None
    try:
        dest_conn = sqlite3.connect(dest)
        raw.backup(dest_conn)  # online backup: консистентный снимок без остановки
    finally:
        if dest_conn is not None:
            dest_conn.close()
        raw.close()
    with session() as s:
        s.add(
            AuditLog(
                action="admin.backup",
                detail={"file": dest.name, "bytes": dest.stat().st_size},
            )
        )
    return {"file": dest.name, "bytes": dest.stat().st_size}


# ---------- UI ----------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
