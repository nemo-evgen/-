"""RBAC (Этап 4): ролевые API-ключи.

Конфигурация — переменная окружения API_KEYS в формате:
    API_KEYS=admin:секрет1,analyst:секрет2,viewer:секрет3

- Ключ передаётся заголовком `X-API-Key` (или `?api_key=` для curl/скриптов).
- Роли: viewer (только чтение) < analyst (запуск поисков, решения) < admin (+ /api/admin/*).
- API_KEYS не задан → открытый режим (dev): все запросы как admin.
- Без ключа при настроенных API_KEYS → 401; чужая роль → 403.
- Публичные пути: /healthz, /metrics, статика UI, /docs, /openapi.json
  (метрики скрейпятся без аутентификации по соглашению Prometheus).
"""
from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse

ROLE_ORDER = {"viewer": 1, "analyst": 2, "admin": 3}

PUBLIC_PREFIXES = ("/static", "/docs", "/redoc")
PUBLIC_PATHS = {"/", "/healthz", "/metrics", "/openapi.json"}


def load_keys() -> dict[str, str]:
    """`admin:key1,analyst:key2` → {ключ: роль}. Пусто → {} (открытый режим)."""
    keys: dict[str, str] = {}
    for part in os.getenv("API_KEYS", "").split(","):
        part = part.strip()
        if not part:
            continue
        role, _, key = part.partition(":")
        role, key = role.strip().lower(), key.strip()
        if role in ROLE_ORDER and key:
            keys[key] = role
    return keys


def _supplied_key(request: Request) -> str:
    return (
        request.headers.get("x-api-key")
        or request.query_params.get("api_key")
        or ""
    ).strip()


def _required_role(method: str, path: str) -> str:
    if path.startswith("/api/admin"):
        return "admin"
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    return "analyst"


def enforce(request: Request) -> JSONResponse | None:
    """None → пропустить; JSONResponse → отказ (401/403)."""
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return None

    keys = load_keys()
    if not keys:
        return None  # открытый режим (dev / тесты)

    key = _supplied_key(request)
    role = keys.get(key) if key else None
    if role is None:
        detail = "неверный API-ключ" if key else "требуется X-API-Key (см. API_KEYS)"
        return JSONResponse({"detail": detail}, status_code=401)

    need = _required_role(request.method, path)
    if ROLE_ORDER[role] < ROLE_ORDER[need]:
        return JSONResponse(
            {"detail": f"роль «{role}» не может: {request.method} {path} (нужна {need})"},
            status_code=403,
        )
    return None
