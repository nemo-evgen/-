"""Нормализация и наивная сшивка сущностей (entity resolution, v0).

Этап 1: один человек = набор аккаунтов, сматченных по (platform, handle)
внутри кейса; факты вешаются на «основного» человека кейса.
Более умный скоринг (ФИО+город+вуз, pHash фото) — Этап 3.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.contract import CollectorFact, CollectorResult
from core.models import Account, Fact, Person, SearchJob, utcnow

# kind'ы, которые несут привязку к профилю площадки
ACCOUNT_KIND = "account.profile"


def payload_hash(kind: str, value: dict) -> str:
    blob = json.dumps({"kind": kind, "value": value}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _find_or_create_person(
    session: Session,
    case_id: int,
    display_name: str,
    confidence: float = 0.5,
    meta: dict | None = None,
) -> Person:
    person = session.execute(
        select(Person).where(Person.case_id == case_id, Person.display_name == display_name)
    ).scalar_one_or_none()
    if person:
        return person
    person = Person(
        case_id=case_id,
        display_name=display_name or "—",
        confidence=confidence,
        meta=meta or {},
    )
    session.add(person)
    session.flush()
    return person


def _find_or_create_account(
    session: Session,
    job: SearchJob,
    person: Person,
    platform: str,
    handle: str,
    url: str | None,
    confidence: float,
) -> Account:
    account = session.execute(
        select(Account).where(
            Account.case_id == job.case_id,
            Account.platform == platform,
            Account.handle == handle,
        )
    ).scalar_one_or_none()
    if account:
        # подняли привязку, если раньше был без человека
        if account.person_id is None:
            account.person_id = person.id
        return account
    account = Account(
        case_id=job.case_id,
        person_id=person.id,
        platform=platform,
        handle=handle,
        url=url,
        confidence=confidence,
    )
    session.add(account)
    session.flush()
    return account


def _store_fact(
    session: Session,
    job: SearchJob,
    fact: CollectorFact,
    person: Person | None,
) -> bool:
    """Вставляет факт, пропуская дубликаты. True — если записан новый."""
    h = payload_hash(fact.kind, fact.value)
    exists = session.execute(
        select(Fact.id).where(
            Fact.case_id == job.case_id,
            Fact.kind == fact.kind,
            Fact.payload_hash == h,
        )
    ).first()
    if exists:
        return False
    session.add(
        Fact(
            case_id=job.case_id,
            person_id=person.id if person else None,
            job_id=job.id,
            kind=fact.kind,
            value=fact.value,
            source_url=fact.source_url,
            artifacts=list(fact.artifacts or []),
            captured_at=fact.captured_at or utcnow(),
            confidence=fact.confidence,
            payload_hash=h,
        )
    )
    return True


def ingest(session: Session, job: SearchJob, results: list[CollectorResult]) -> dict:
    """Нормализует результаты коллекторов и пишет в БД. Возвращает сводку."""
    new_facts = 0
    new_accounts = 0
    primary_person: Person | None = None

    hints = job.hints or {}
    if job.input_type == "vk":
        # человек может быть без имени (закрытый профиль) — показываем handle, не URL
        fallback_name = str(job.input_value).rstrip("/").split("/")[-1] or job.input_value
    elif job.input_type in ("username", "telegram"):
        fallback_name = str(job.input_value).lstrip("@")
    else:
        fallback_name = hints.get("full_name") or ""
    fallback_name = fallback_name or "Неизвестный"

    # проход 1: аккаунты → люди
    account_facts: list[tuple[CollectorFact, dict]] = []
    other_facts: list[CollectorFact] = []
    for res in results:
        for fact in res.facts:
            if fact.kind == ACCOUNT_KIND:
                account_facts.append((fact, fact.value))
            else:
                other_facts.append(fact)

    for fact, value in account_facts:
        platform = str(value.get("platform") or "unknown")
        handle = str(value.get("handle") or "").strip()
        if not handle:
            continue
        display = str(value.get("name") or "").strip() or None
        person = None
        if display:
            person = _find_or_create_person(session, job.case_id, display, confidence=0.5)
        # если профиль без имени — привязываем к основному человеку кейса
        if person is None:
            if primary_person is None:
                primary_person = _find_or_create_person(
                    session, job.case_id, fallback_name, confidence=0.3
                )
            person = primary_person
        before = session.query(Account).count()
        _find_or_create_account(
            session,
            job,
            person,
            platform=platform,
            handle=handle,
            url=value.get("url"),
            confidence=float(value.get("confidence") or fact.confidence),
        )
        if session.query(Account).count() > before:
            new_accounts += 1
        if primary_person is None:
            primary_person = person

    # гарантируем наличие человека для «бесхабных» фактов
    if primary_person is None:
        primary_person = _find_or_create_person(
            session, job.case_id, fallback_name, confidence=0.3
        )

    # проход 2: остальные факты
    for fact in other_facts:
        if _store_fact(session, job, fact, primary_person):
            new_facts += 1

    summary = {
        "persons": 1 if primary_person else 0,
        "new_accounts": new_accounts,
        "new_facts": new_facts,
        "person_id": primary_person.id if primary_person else None,
    }
    return summary
