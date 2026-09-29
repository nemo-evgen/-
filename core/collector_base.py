"""База и реестр коллекторов: плагин = функция registered by name."""
from __future__ import annotations

from typing import Callable

from core.contract import CollectorInput, CollectorResult

REGISTRY: dict[str, Callable[[CollectorInput], CollectorResult]] = {}


def collector(name: str) -> Callable:
    def deco(fn: Callable[[CollectorInput], CollectorResult]) -> Callable:
        REGISTRY[name] = fn
        return fn

    return deco
