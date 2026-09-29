"""Тесты Этап 4: RBAC (API_KEYS), /api/audit, /metrics, /api/admin/backup.

Запуск: .venv/bin/python tests/test_rbac.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite+pysqlite:///./test_rbac.db")
os.environ.pop("API_KEYS", None)  # старт в открытом режиме

from fastapi.testclient import TestClient  # noqa: E402

from core.db import init_db, session  # noqa: E402
from core.models import AuditLog  # noqa: E402

DB = ROOT / "test_rbac.db"


def reset_db():
    from core import db

    if db._engine is not None:
        db._engine.dispose()
        db._engine = None
        db._SessionFactory = None
    for p in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
        if p.exists():
            p.unlink()
    init_db()


def test_open_mode_and_endpoints():
    reset_db()
    from services.api.app.main import app

    client = TestClient(app, base_url="http://test")
    # открытый режим: без ключей всё доступно
    r = client.post("/api/cases", json={"name": "Открытый", "legal_basis": "тест"})
    assert r.status_code == 200, r.text  # POST /api/cases → 200
    r = client.get("/api/cases")
    assert r.status_code == 200 and len(r.json()) == 1
    r = client.get("/api/audit")
    assert r.status_code == 200
    actions = [e["action"] for e in r.json()["entries"]]
    assert "case.created" in actions, actions
    assert r.json()["entries"][0]["case_id"] == 1, "audit привязан к кейсу"
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "osint_cases_total 1" in body
    assert 'osint_jobs_total{status="done"}' in body
    assert "osint_reviews_pending 0" in body
    print("ok: open_mode_and_endpoints")


def test_rbac_enforcement():
    reset_db()
    os.environ["API_KEYS"] = "admin:secret-A,analyst:secret-B,viewer:secret-C"
    try:
        from services.api.app.main import app

        client = TestClient(app, base_url="http://test")

        # статика/UI и служебные пути публичны даже с включённым RBAC
        assert client.get("/healthz").status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/").status_code == 200

        # без ключа → 401
        r = client.get("/api/cases")
        assert r.status_code == 401, r.text
        # неверный ключ → 401
        r = client.get("/api/cases", headers={"X-API-Key": "wrong"})
        assert r.status_code == 401
        # неверный формат в API_KEYS игнорируется, не ломает сервис
        assert client.get("/healthz").status_code == 200

        H = lambda k: {"X-API-Key": k}  # noqa: E731

        # viewer: чтение ок, запись → 403
        r = client.get("/api/cases", headers=H("secret-C"))
        assert r.status_code == 200, r.text
        r = client.post("/api/cases", json={"name": "X", "legal_basis": "t"},
                        headers=H("secret-C"))
        assert r.status_code == 403, r.text
        r = client.post("/api/reviews/1/decision", json={"action": "approve"},
                        headers=H("secret-C"))
        assert r.status_code == 403

        # analyst: создание кейса/поиск ок; admin-путь → 403
        r = client.post("/api/cases", json={"name": "Аналитик", "legal_basis": "t"},
                        headers=H("secret-B"))
        assert r.status_code == 200, r.text
        r = client.post("/api/cases/1/resolve", headers=H("secret-B"))
        assert r.status_code == 200, r.text
        r = client.post("/api/admin/backup", headers=H("secret-B"))
        assert r.status_code == 403, r.text

        # admin: бэкап работает (sqlite online backup)
        r = client.post("/api/admin/backup", headers=H("secret-A"))
        assert r.status_code == 200, r.text
        fname = r.json()["file"]
        assert (ROOT / "backups" / fname).is_file(), fname

        # аудит читает viewer; там есть resolver.run и admin.backup
        r = client.get("/api/audit", headers=H("secret-C"))
        assert r.status_code == 200
        actions = {e["action"] for e in r.json()["entries"]}
        assert "case.created" in actions, actions
        assert "admin.backup" in actions, actions

        # ?api_key= работает как альтернатива заголовка
        r = client.get("/api/cases?api_key=secret-C")
        assert r.status_code == 200
        # фильтр аудита по кейсу
        r = client.get("/api/audit?case_id=999", headers=H("secret-C"))
        assert r.status_code == 200 and r.json()["entries"] == []

        # повторный бэкап → новый файл (без коллизии имён)
        r2 = client.post("/api/admin/backup", headers=H("secret-A"))
        assert r2.status_code == 200
        assert r2.json()["file"] != fname or r2.json()["bytes"] >= 0
    finally:
        os.environ.pop("API_KEYS", None)
    print("ok: rbac_enforcement")


if __name__ == "__main__":
    test_open_mode_and_endpoints()
    test_rbac_enforcement()
    print("ALL OK (2): Этап 4 — RBAC/аудит/метрики/бэкап")
