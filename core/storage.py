"""Хранилище снимков-доказательств (archival evidence).

Режимы (выбор по MINIO_ENDPOINT):
  * MinIO/S3  → minio://<bucket>/<name>     (прод, docker compose)
  * file      → file://<name>               (dev/тесты: SNAPSHOT_DIR)

Снимок = сырой HTML/JSON, который увидел коллектор, чтобы факт можно было
доказать и перепроверить позже. Best-effort: при ошибке — None, пайплайн жив.
"""
from __future__ import annotations

import io
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from core import config

log = logging.getLogger("storage")

_SAFE_KIND = re.compile(r"[^a-z0-9_]+")


def _minio_client():
    from minio import Minio  # лениво: пакет есть в requirements, но в тестах file-режим

    return Minio(
        config.MINIO_ENDPOINT,
        access_key=config.MINIO_ACCESS_KEY,
        secret_key=config.MINIO_SECRET_KEY,
        secure=config.MINIO_SECURE,
    )


def put_snapshot(case_id: int, kind: str, data: bytes, suffix: str = ".html") -> str | None:
    """Сохраняет снимок, возвращает ref (minio://… | file://…) или None."""
    if not data or len(data) > config.SNAPSHOT_LIMIT_BYTES:
        return None
    kind = _SAFE_KIND.sub("_", kind.lower()) or "snapshot"
    name = f"case{case_id}/{kind}/{uuid.uuid4().hex}{suffix}"
    try:
        if config.MINIO_ENDPOINT:
            client = _minio_client()
            if not client.bucket_exists(config.MINIO_BUCKET):
                client.make_bucket(config.MINIO_BUCKET)
            content_type = "application/json" if suffix == ".json" else "text/html"
            client.put_object(
                config.MINIO_BUCKET,
                name,
                io.BytesIO(data),
                len(data),
                content_type=content_type,
            )
            return f"minio://{config.MINIO_BUCKET}/{name}"
        base = Path(config.SNAPSHOT_DIR).resolve()
        path = (base / name).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"file://{name}"
    except Exception as exc:  # noqa: BLE001 — снимки best-effort
        log.warning("snapshot put failed: %s", exc)
        return None


def open_snapshot(ref: str) -> tuple[bytes, str] | None:
    """Читает снимок по ref → (data, content_type). None — не найден/ошибка."""
    try:
        parsed = urlparse(ref)
        if parsed.scheme == "file":
            # поддерживаем file://case1/x.html (netloc) и file:///case1/x.html
            name = f"{parsed.netloc}{parsed.path}".lstrip("/")
            base = Path(config.SNAPSHOT_DIR).resolve()
            path = (base / name).resolve()
            if base not in path.parents:
                return None  # anti-traversal
            if not path.is_file():
                return None
            ctype = "application/json" if path.suffix == ".json" else "text/html"
            return path.read_bytes(), ctype
        if parsed.scheme == "minio":
            client = _minio_client()
            bucket = parsed.netloc or config.MINIO_BUCKET
            key = parsed.path.lstrip("/")
            resp = client.get_object(bucket, key)
            try:
                data = resp.read()
            finally:
                resp.close()
                resp.release_conn()
            ctype = "application/json" if key.endswith(".json") else "text/html"
            return data, ctype
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot open failed: %s", exc)
    return None
