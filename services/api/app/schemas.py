"""Pydantic-схемы API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InputType = Literal["vk", "username", "name", "telegram", "photo"]


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    legal_basis: str | None = Field(default=None, description="Правовое основание расследования")


class CaseOut(BaseModel):
    id: int
    name: str
    legal_basis: str | None
    created_at: str


class SearchCreate(BaseModel):
    input_type: InputType
    input_value: str = Field(min_length=1, max_length=1000, description="URL/ник/ФИО")
    hints: dict = Field(
        default_factory=dict,
        description="Подсказки: full_name, age, city, university, nick",
    )
    collectors: list[str] | None = Field(
        default=None, description="Явный набор коллекторов (override маршрутизации)"
    )


class JobOut(BaseModel):
    id: int
    case_id: int
    input_type: str
    input_value: str
    hints: dict
    status: str
    error: str | None
    warnings: list
    summary: dict
    created_at: str
    started_at: str | None
    finished_at: str | None


class AccountOut(BaseModel):
    id: int
    platform: str
    handle: str
    url: str | None
    confidence: float


class FactOut(BaseModel):
    id: int
    kind: str
    value: dict
    source_url: str | None
    confidence: float
    captured_at: str
    job_id: int | None


class PersonOut(BaseModel):
    id: int
    display_name: str
    confidence: float
    accounts: list[AccountOut]
    facts: list[FactOut]


class DossierOut(BaseModel):
    case: CaseOut
    jobs: list[JobOut]
    persons: list[PersonOut]
