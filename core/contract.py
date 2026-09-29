"""Контракт коллектора: единый формат входа/выхода для всех плагинов-источников."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@dataclass
class CollectorInput:
    """Задание, которое получает коллектор."""

    case_id: int
    job_id: int
    input_type: str          # vk | username | name
    value: str               # ссылка / ник / ФИО
    hints: dict = field(default_factory=dict)   # age, city, university, full_name


@dataclass
class CollectorFact:
    """Сырой факт до нормализации."""

    kind: str                       # account.profile | photo.avatar | search.query | ...
    value: dict
    source_url: str | None = None
    confidence: float = 0.7
    captured_at: dt.datetime = field(default_factory=utcnow)
    artifacts: list[str] = field(default_factory=list)  # ref снимков: file:// | minio://

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "captured_at": self.captured_at.isoformat(),
            "artifacts": self.artifacts,
        }


@dataclass
class CollectorResult:
    """Результат работы одного коллектора."""

    collector: str
    facts: list[CollectorFact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict:
        return {
            "collector": self.collector,
            "ok": self.ok,
            "warnings": self.warnings,
            "facts": [f.to_dict() for f in self.facts],
        }
