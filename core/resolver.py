"""Entity resolution v1 (Этап 3): скоринг по подсказкам, мердж, граф, очередь проверки.

Что делает resolve_case(case_id):
  1. АВТО-МЕРДЖ людей с одинаковым нормализованным именем (регистр/пробелы/ё).
  2. СКОРИНГ: подсказки кейса (ФИО, город, вуз, возраст) против фактов профилей
     → Person.confidence + Person.meta.signals (прозрачно, из чего сложилась оценка).
  3. ГРАФ: graph.friend-факты → таблица links (самоссылки отбрасываются).
  4. ОЧЕРЕДЬ ПРОВЕРКИ: сильные face-матчи и точные pHash-совпадения между
     РАЗНЫМИ людьми → ReviewItem (human-in-the-loop: approve/reject).
     merge_persons() переносит аккаунты/факты/связи и удаляет дубль.

Автоматически вызывается воркером после каждого джобы; доступен и вручную
(POST /api/cases/{id}/resolve). Идемпотентен.
"""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher

from sqlalchemy import select

from core.db import session
from core.models import (
    Account,
    AuditLog,
    Fact,
    Link,
    Person,
    ReviewItem,
    SearchJob,
    utcnow,
)

_PLACEHOLDERS = {"", "—", "-", "неизвестный", "unknown", "n/a"}


def _norm_name(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", s)


def _is_placeholder(name: str) -> bool:
    return _norm_name(name) in _PLACEHOLDERS


def _payload_hash(kind: str, payload: dict) -> str:
    blob = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------- мердж ----------------

def merge_persons(s, dst: Person, src: Person) -> None:
    """Переносит всё от src к dst и удаляет src (вызывается внутри сессии)."""
    if dst.id == src.id:
        return

    # аккаунты: конфликт уникальности (case, platform, handle) → дубль просто удаляем
    for acc in s.query(Account).filter(Account.person_id == src.id).all():
        dup = (
            s.query(Account)
            .filter(
                Account.case_id == acc.case_id,
                Account.platform == acc.platform,
                Account.handle == acc.handle,
                Account.person_id != src.id,
            )
            .first()
        )
        if dup:
            s.delete(acc)
        else:
            acc.person_id = dst.id

    s.query(Fact).filter(Fact.person_id == src.id).update(
        {Fact.person_id: dst.id}, synchronize_session=False
    )
    for link in s.query(Link).filter(
        (Link.src_person_id == src.id) | (Link.dst_person_id == src.id)
    ).all():
        if link.src_person_id == src.id:
            existing = (
                s.query(Link)
                .filter(
                    Link.case_id == link.case_id,
                    Link.src_person_id == dst.id,
                    Link.kind == link.kind,
                    Link.dst_platform == link.dst_platform,
                    Link.dst_handle == link.dst_handle,
                )
                .first()
            )
            if existing:
                s.delete(link)
            else:
                link.src_person_id = dst.id
        elif link.dst_person_id == src.id:
            link.dst_person_id = dst.id

    # имя: если у dst-заглушка — берём настоящее имя src
    if _is_placeholder(dst.display_name) and not _is_placeholder(src.display_name):
        dst.display_name = src.display_name
    dst.confidence = max(dst.confidence or 0.0, src.confidence or 0.0)
    s.delete(src)
    s.flush()


# ---------------- сбор подсказок кейса ----------------

def collect_case_hints(s, case_id: int) -> dict:
    """Union хинтов всех джоб кейса (позние перезаписывают ранние)."""
    hints: dict = {}
    jobs = (
        s.execute(
            select(SearchJob)
            .where(SearchJob.case_id == case_id)
            .order_by(SearchJob.id)
        )
        .scalars()
        .all()
    )
    for j in jobs:
        for k, v in (j.hints or {}).items():
            if v not in (None, ""):
                hints[k] = v
    return hints


# ---------------- скоринг ----------------

def score_person(person: Person, hints: dict, facts: list[Fact]) -> tuple[float, dict]:
    """→ (confidence, signals). Сигналы прозрачны и попадают в meta."""
    if not hints:
        return person.confidence or 0.5, {}

    signals: dict = {}
    score = 0.0

    accounts = [a for a in getattr(person, "accounts", []) or []]
    solid_accounts = [a for a in accounts if (a.confidence or 0) >= 0.5]
    if solid_accounts:
        score += 0.15
        signals["has_account"] = True

    # собираем текстовые подтверждения из фактов
    demo: dict = {}
    names: list[str] = [person.display_name or ""]
    for f in facts:
        v = f.value or {}
        if f.kind == "account.profile" and v.get("name"):
            names.append(str(v["name"]))
        elif f.kind == "profile.demo":
            demo.update(v)
        elif f.kind == "profile.bio" and v.get("text"):
            demo.setdefault("_bio", "")
            demo["_bio"] += " " + str(v["text"])[:500]

    # ФИО
    hint_name = _norm_name(str(hints.get("full_name") or ""))
    if hint_name:
        best = max(
            (SequenceMatcher(None, _norm_name(n), hint_name).ratio() for n in names if n),
            default=0.0,
        )
        if best >= 0.6:
            score += 0.30 * min(best / 0.9, 1.0)
            signals["name_ratio"] = round(best, 2)

    # город
    hint_city = _norm_name(str(hints.get("city") or ""))
    if hint_city:
        demo_city = _norm_name(str(demo.get("city") or ""))
        if demo_city and (demo_city == hint_city or hint_city in demo_city or demo_city in hint_city):
            score += 0.20
            signals["city"] = demo.get("city")

    # университет
    hint_uni = _norm_name(str(hints.get("university") or ""))
    if hint_uni:
        unis = [_norm_name(str(u.get("name") or "")) for u in (demo.get("universities") or [])]
        if any(u and (u == hint_uni or hint_uni in u or u in hint_uni) for u in unis):
            score += 0.25
            signals["university"] = hint_uni

    # возраст
    hint_age = hints.get("age")
    if hint_age not in (None, ""):
        try:
            want = int(str(hint_age))
            have = int(demo.get("age"))
            if abs(want - have) <= 1:
                score += 0.15
                signals["age"] = have
        except (TypeError, ValueError):
            pass

    return round(min(score, 0.95), 2), signals


# ---------------- граф ----------------

def build_links(s, case_id: int) -> int:
    """graph.friend-факты → Link. Возвращает число новых связей."""
    new_links = 0
    facts = (
        s.query(Fact)
        .filter(Fact.case_id == case_id, Fact.kind == "graph.friend")
        .all()
    )
    for f in facts:
        if not f.person_id:
            continue
        v = f.value or {}
        platform = str(v.get("platform") or "")
        handle = str(v.get("handle") or "")
        if not handle:
            continue
        existing = (
            s.query(Link)
            .filter(
                Link.case_id == case_id,
                Link.src_person_id == f.person_id,
                Link.kind == "friend",
                Link.dst_platform == platform,
                Link.dst_handle == handle,
            )
            .first()
        )
        if existing:
            # обогащаем dst_person, если появился профиль
            if existing.dst_person_id is None:
                acc = (
                    s.query(Account)
                    .filter(
                        Account.case_id == case_id,
                        Account.platform == platform,
                        Account.handle == handle,
                    )
                    .first()
                )
                if acc and acc.person_id and acc.person_id != f.person_id:
                    existing.dst_person_id = acc.person_id
            continue
        # ищем профиль друга в кейсе
        dst_person_id = None
        acc = (
            s.query(Account)
            .filter(
                Account.case_id == case_id,
                Account.platform == platform,
                Account.handle == handle,
            )
            .first()
        )
        if acc and acc.person_id and acc.person_id != f.person_id:
            dst_person_id = acc.person_id
        s.add(
            Link(
                case_id=case_id,
                src_person_id=f.person_id,
                dst_person_id=dst_person_id,
                kind="friend",
                dst_platform=platform,
                dst_handle=handle,
                dst_url=v.get("url"),
                dst_name=v.get("name"),
                weight=0.5,
                fact_id=f.id,
            )
        )
        new_links += 1
    s.flush()
    return new_links


# ---------------- очередь проверки ----------------

def queue_photo_reviews(s, case_id: int) -> int:
    """face- strong / pHash-0 совпадения между РАЗНЫМИ людьми → ReviewItem."""
    queued = 0
    # url аватарки → её человек
    avatar_person: dict[str, int | None] = {}
    for f in s.query(Fact).filter(Fact.case_id == case_id, Fact.kind == "photo.avatar").all():
        u = (f.value or {}).get("url")
        if u and f.person_id:
            avatar_person.setdefault(u, f.person_id)

    review_specs: list[tuple[str, dict]] = []
    for f in s.query(Fact).filter(Fact.case_id == case_id).all():
        if f.kind == "photo.face_match":
            v = f.value or {}
            if v.get("verdict") != "strong":
                continue
            review_kind = "face_link"
        elif f.kind == "photo.match":
            v = f.value or {}
            if v.get("distance") not in (0, "0"):
                continue
            review_kind = "photo_link"
        else:
            continue
        dst_person = avatar_person.get(v.get("target"))
        if not dst_person or not f.person_id or dst_person == f.person_id:
            continue
        payload = {
            "src_person_id": f.person_id,
            "dst_person_id": dst_person,
            "target": v.get("target"),
            "fact_id": f.id,
            **({"cosine": v.get("cosine"), "verdict": v.get("verdict")}
               if review_kind == "face_link" else {"distance": v.get("distance")}),
        }
        review_specs.append((review_kind, payload))

    for kind, payload in review_specs:
        h = _payload_hash(kind, {k: payload[k] for k in ("src_person_id", "dst_person_id", "target")})
        exists = (
            s.query(ReviewItem)
            .filter(
                ReviewItem.case_id == case_id,
                ReviewItem.kind == kind,
                ReviewItem.payload_hash == h,
            )
            .first()
        )
        if exists:
            continue
        s.add(
            ReviewItem(
                case_id=case_id,
                kind=kind,
                payload=payload,
                payload_hash=h,
                status="pending",
            )
        )
        queued += 1
    s.flush()
    return queued


# ---------------- главный проход ----------------

def resolve_case(case_id: int) -> dict:
    with session() as s:
        persons = (
            s.query(Person).filter(Person.case_id == case_id).order_by(Person.id).all()
        )
        if not persons:
            return {"merged": 0, "scored": 0, "links": 0, "reviews": 0}

        # 1) авто-мердж по нормализованному имени
        merged = 0
        by_name: dict[str, Person] = {}
        for p in list(persons):
            key = _norm_name(p.display_name)
            if _is_placeholder(p.display_name) or not key:
                continue
            if key in by_name:
                merge_persons(s, by_name[key], p)
                merged += 1
            else:
                by_name[key] = p

        persons = (
            s.query(Person).filter(Person.case_id == case_id).order_by(Person.id).all()
        )

        # 2) скоринг по подсказкам кейса
        hints = collect_case_hints(s, case_id)
        scored = 0
        for p in persons:
            facts = s.query(Fact).filter(Fact.person_id == p.id).all()
            conf, signals = score_person(p, hints, facts)
            if signals:
                p.confidence = conf
                meta = dict(p.meta or {})
                meta["signals"] = signals
                meta["hints"] = {k: hints[k] for k in hints if k != "full_name"} | (
                    {"full_name": hints["full_name"]} if hints.get("full_name") else {}
                )
                p.meta = meta
                scored += 1

        # 3) граф
        links = build_links(s, case_id)

        # 4) очередь проверки (face/pHash между людьми)
        reviews = queue_photo_reviews(s, case_id)

        s.add(
            AuditLog(
                case_id=case_id,
                action="resolver.run",
                detail={"merged": merged, "scored": scored, "links": links, "reviews": reviews},
            )
        )
        return {"merged": merged, "scored": scored, "links": links, "reviews": reviews}


# ---------------- решения по очереди ----------------

def decide_review(review_id: int, action: str) -> dict:
    if action not in ("approve", "reject"):
        raise ValueError("action должен быть approve или reject")
    with session() as s:
        item = s.get(ReviewItem, review_id)
        if item is None:
            raise LookupError("элемент проверки не найден")
        if item.status != "pending":
            raise ValueError(f"уже решён: {item.status}")
        if action == "approve":
            src = s.get(Person, item.payload.get("src_person_id"))
            dst = s.get(Person, item.payload.get("dst_person_id"))
            if src and dst and src.id != dst.id:
                merge_persons(s, dst, src)
                item.payload["merged_into"] = dst.id
        item.status = "approved" if action == "approve" else "rejected"
        item.decided_at = utcnow()
        s.add(
            AuditLog(
                case_id=item.case_id,
                action=f"review.{item.status}",
                detail={"review_id": item.id, "kind": item.kind},
            )
        )
        return {"id": item.id, "status": item.status}


def graph_payload(case_id: int) -> dict:
    """Узлы/рёбра для UI-графа."""
    with session() as s:
        persons = s.query(Person).filter(Person.case_id == case_id).all()
        links = s.query(Link).filter(Link.case_id == case_id).all()
        nodes = [
            {
                "id": f"p{p.id}",
                "label": p.display_name,
                "type": "person",
                "confidence": p.confidence,
            }
            for p in persons
        ]
        known_person_ids = {p.id for p in persons}
        edges = []
        seen_nodes = {n["id"] for n in nodes}
        for lk in links:
            if lk.dst_person_id and lk.dst_person_id in known_person_ids:
                dst_id = f"p{lk.dst_person_id}"
            else:
                dst_id = f"h{lk.dst_platform}:{lk.dst_handle}"
                if dst_id not in seen_nodes:
                    seen_nodes.add(dst_id)
                    nodes.append(
                        {
                            "id": dst_id,
                            "label": lk.dst_name or lk.dst_handle,
                            "type": "handle",
                            "confidence": None,
                        }
                    )
            edges.append(
                {
                    "src": f"p{lk.src_person_id}",
                    "dst": dst_id,
                    "kind": lk.kind,
                    "weight": lk.weight,
                }
            )
        return {"nodes": nodes, "edges": edges}
