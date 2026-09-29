"""Подключение к БД: ленивый engine, сессии, инициализация схемы.

DATABASE_URL поддерживает postgres (прод) и sqlite (локальная разработка/тесты).
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm import declarative_base

from core.config import DATABASE_URL

Base = declarative_base()


def _json_dumps(value) -> str:
    """ensure_ascii=False: кириллица в JSON хранится как есть → находится SQL LIKE
    (SQL-fallback полнотекстового поиска). Документированный способ SA:
    create_engine(json_serializer=...)."""
    return json.dumps(value, ensure_ascii=False, default=str)


_engine = None
_SessionFactory: sessionmaker | None = None


def make_engine(url: str | None = None):
    url = url or DATABASE_URL
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            json_serializer=_json_dumps,
        )
    return create_engine(url, pool_pre_ping=True, json_serializer=_json_dumps)


def get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine(os.getenv("DATABASE_URL_OVERRIDE") or None)
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def session() -> Iterator[Session]:
    factory = get_session_factory()
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db() -> None:
    """Создаёт таблицы (в проде желателен Alembic — этап 4)."""
    from core import models  # noqa: F401  (регистрация моделей)

    Base.metadata.create_all(get_engine())
