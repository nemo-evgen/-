"""Маршрутизация: тип входа → набор коллекторов (переопределяется на джобе)."""
from __future__ import annotations

INPUT_TYPES = ("vk", "username", "name", "telegram")

# Этап 1: приоритет S2 (VK) + S4 (ФИО-дорки) + username (Maigret).
# S1 (Telegram) — только пассивные методы: tg_profile (t.me/*), dorks, username.
DEFAULT_COLLECTORS: dict[str, list[str]] = {
    "vk": ["vk_profile"],
    "username": ["username"],
    "name": ["dorks"],
    "telegram": ["tg_profile", "dorks", "username"],
}


def collectors_for(input_type: str, override: list[str] | None = None) -> list[str]:
    if override:
        return [c for c in override if isinstance(c, str) and c]
    return list(DEFAULT_COLLECTORS.get(input_type, []))
