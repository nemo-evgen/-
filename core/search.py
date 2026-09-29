"""Полнотекстовый поиск по фактам.

Backend выбирается по OPENSEARCH_URL:
  * задан  → OpenSearch (multi_match по text/kind/source_url, фильтр case_id)
  * пустой → SQL-fallback: ILIKE/LIKE по kind, source_url и тексту value —
    работает без Docker (dev/тесты) и как деградация в проде.

Индексирование: worker вызывает index_facts() после успешного джобы.
"""
from __future__ import annotations

import json
import logging

import requests
from sqlalchemy import Text, and_, or_, cast
from sqlalchemy.orm import Session

from core import config
from core.models import Fact

log = logging.getLogger("search")


def os_enabled() -> bool:
    return bool(config.OPENSEARCH_URL)


def _auth() -> dict | None:
    if config.OPENSEARCH_USER:
        return (config.OPENSEARCH_USER, config.OPENSEARCH_PASS)
    return None


def fact_to_doc(f: Fact) -> dict:
    """Документ индекса: плоский текст для multi_match."""
    value = f.value or {}
    parts: list[str] = []
    for v in value.values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, (int, float)):
            parts.append(str(v))
        elif isinstance(v, list):
            parts.extend(str(x) for x in v[:20])
        elif isinstance(v, dict):
            parts.append(json.dumps(v, ensure_ascii=False)[:2000])
    return {
        "case_id": f.case_id,
        "person_id": f.person_id,
        "job_id": f.job_id,
        "kind": f.kind,
        "source_url": f.source_url,
        "confidence": f.confidence,
        "captured_at": f.captured_at.isoformat() if f.captured_at else None,
        "text": " ".join(parts)[:20000],
        "value_json": json.dumps(value, ensure_ascii=False)[:20000],
    }


def index_facts(facts: list[Fact]) -> int:
    """Bulk-индексация в OpenSearch. Возвращает число проиндексированных (0 — выкл)."""
    if not os_enabled() or not facts:
        return 0
    lines: list[str] = []
    for f in facts:
        lines.append(json.dumps({"index": {"_id": str(f.id)}}))
        lines.append(json.dumps(fact_to_doc(f), ensure_ascii=False))
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    try:
        resp = requests.post(
            f"{config.OPENSEARCH_URL}/_bulk",
            params={"index": config.OPENSEARCH_INDEX},
            data=payload,
            headers={"Content-Type": "application/x-ndjson"},
            auth=_auth(),
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            log.warning("opensearch bulk has errors")
        return len(facts)
    except Exception as exc:  # noqa: BLE001 — поиск не должен ронять джобу
        log.warning("opensearch index failed: %s", exc)
        return 0


def _search_os(query: str, case_id: int | None, size: int) -> list[dict]:
    body: dict = {
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": ["text^2", "kind", "source_url"],
                            "type": "best_fields",
                            "fuzziness": "AUTO",
                        }
                    }
                ]
            }
        },
        "size": size,
        "sort": ["_score", {"captured_at": "desc"}],
    }
    if case_id:
        body["query"]["bool"]["filter"] = [{"term": {"case_id": case_id}}]
    resp = requests.post(
        f"{config.OPENSEARCH_URL}/{config.OPENSEARCH_INDEX}/_search",
        json=body,
        auth=_auth(),
        timeout=15,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", {}).get("hits", [])
    out = []
    for h in hits:
        src = h.get("_source", {})
        try:
            value = json.loads(src.get("value_json") or "{}")
        except json.JSONDecodeError:
            value = {}
        out.append(
            {
                "fact_id": int(h["_id"]),
                "score": h.get("_score"),
                "case_id": src.get("case_id"),
                "person_id": src.get("person_id"),
                "kind": src.get("kind"),
                "source_url": src.get("source_url"),
                "confidence": src.get("confidence"),
                "value": value,
            }
        )
    return out


def _search_sql(session: Session, query: str, case_id: int | None, size: int) -> list[dict]:
    q = f"%{query.strip()}%"
    cond = or_(
        Fact.kind.ilike(q),
        cast(Fact.source_url, Text).ilike(q),
        cast(Fact.value, Text).ilike(q),
    )
    if case_id:
        cond = and_(Fact.case_id == case_id, cond)
    rows = (
        session.query(Fact)
        .filter(cond)
        .order_by(Fact.id.desc())
        .limit(size)
        .all()
    )
    return [
        {
            "fact_id": f.id,
            "score": None,
            "case_id": f.case_id,
            "person_id": f.person_id,
            "kind": f.kind,
            "source_url": f.source_url,
            "confidence": f.confidence,
            "value": f.value or {},
        }
        for f in rows
    ]


def search(query: str, case_id: int | None = None, size: int | None = None) -> dict:
    """→ {backend, results: [...]}"""
    size = size or config.SEARCH_LIMIT
    query = (query or "").strip()
    if not query:
        return {"backend": "none", "results": []}
    if os_enabled():
        try:
            return {"backend": "opensearch", "results": _search_os(query, case_id, size)}
        except Exception as exc:  # noqa: BLE001 — деградация на SQL
            log.warning("opensearch search failed, fallback to sql: %s", exc)
    from core.db import session as db_session  # лениво, чтобы не циклить

    with db_session() as s:
        return {"backend": "sql", "results": _search_sql(s, query, case_id, size)}
