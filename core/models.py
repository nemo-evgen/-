"""Модель данных (Этап 1): Case → SearchJob → Person/Account/Fact + AuditLog."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Case(Base):
    """Расследование (кейс) — правовой контекр хранения данных."""

    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    legal_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    jobs: Mapped[list["SearchJob"]] = relationship(back_populates="case")
    persons: Mapped[list["Person"]] = relationship(back_populates="case")


class SearchJob(Base):
    """Один запуск конвейера поиска по входу."""

    __tablename__ = "search_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    input_type: Mapped[str] = mapped_column(String(32))  # vk | username | name
    input_value: Mapped[str] = mapped_column(String(1000))
    hints: Mapped[dict] = mapped_column(JSON, default=dict)  # age, city, university...
    collectors: Mapped[list | None] = mapped_column(JSON, nullable=True)  # override
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/done/failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    case: Mapped[Case] = relationship(back_populates="jobs")


class Person(Base):
    """Сущность-человек: объединение аккаунтов (v0 — наивная сшивка)."""

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(500), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="persons")
    accounts: Mapped[list["Account"]] = relationship(back_populates="person")
    facts: Mapped[list["Fact"]] = relationship(back_populates="person")


class Account(Base):
    """Профиль на площадке (VK, Telegram, GitHub, форум...)."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("case_id", "platform", "handle", name="uq_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(100))
    handle: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    person: Mapped[Person | None] = relationship(back_populates="accounts")


class Fact(Base):
    """Атомарный факт с источником и доказательством."""

    __tablename__ = "facts"
    __table_args__ = (UniqueConstraint("case_id", "kind", "payload_hash", name="uq_fact"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("search_jobs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    captured_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    payload_hash: Mapped[str] = mapped_column(String(64))

    person: Mapped[Person | None] = relationship(back_populates="facts")


class AuditLog(Base):
    """Журнал действий исследователя (обязателен — см. ARCHITECTURE §9)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
