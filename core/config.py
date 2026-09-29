"""Общая конфигурация (читается из окружения, см. .env.example)."""
from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://osint:osint@postgres:5432/osint",
)
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")

# Разработка: выполнять джобы прямо в процессе API без Redis/Celery.
EXEC_INLINE = os.getenv("EXEC_INLINE", "0") == "1"

# Секунды на один запуск коллектора (жёсткий таймаут процесса/сети).
COLLECTOR_TIMEOUT = _int("COLLECTOR_TIMEOUT", 120)
MAIGRET_TIMEOUT = _int("MAIGRET_TIMEOUT", 150)
VK_FETCH_TIMEOUT = _int("VK_FETCH_TIMEOUT", 12)

HTTP_USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "OSINT-Person-Search/0.1 (+research; contact: admin@example.com)",
)

# --- VK API (Этап 1.1) ---
# Токен приложения: https://dev.vk.com → «Создать приложение» → сервисный ключ доступа.
VK_TOKEN = os.getenv("VK_TOKEN", "").strip()
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.131")
VK_API_TIMEOUT = _int("VK_API_TIMEOUT", 15)
VK_FRIENDS_LIMIT = _int("VK_FRIENDS_LIMIT", 50)   # 0 = не тянуть друзей
VK_WALL_LIMIT = _int("VK_WALL_LIMIT", 10)         # 0 = не тянуть стену

# --- Авто-выполнение dorks (Этап 1.1) ---
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "").strip()  # serper.dev (приоритет)
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()          # https://serpapi.com
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()    # Google Custom Search
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "").strip()
DORK_MAX_EXEC = _int("DORK_MAX_EXEC", 10)      # сколько запросов на джоб (квоты!)
DORK_EXEC_TIMEOUT = _int("DORK_EXEC_TIMEOUT", 15)

# --- Telegram (Этап 1.1, только пассивно) ---
TG_TIMEOUT = _int("TG_TIMEOUT", 12)
TG_POSTS_LIMIT = _int("TG_POSTS_LIMIT", 15)

# --- Фото (Этап 2) ---
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "data/uploads")   # куда падают загрузки
MODELS_DIR = os.getenv("MODELS_DIR", "models")         # ONNX-модели лиц
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_BYTES", 15 * 1024 * 1024)
PHASH_MAX_DISTANCE = _int("PHASH_MAX_DISTANCE", 6)     # Hamming ≤ → совпадение
FACE_STRONG = float(os.getenv("FACE_STRONG", "0.55"))  # cosine ≥ → likely same
FACE_POSSIBLE = float(os.getenv("FACE_POSSIBLE", "0.45"))
PHOTO_TARGET_LIMIT = _int("PHOTO_TARGET_LIMIT", 30)    # сколько аватарок обработать
PHOTO_DOWNLOAD_TIMEOUT = _int("PHOTO_DOWNLOAD_TIMEOUT", 15)
FACE_MODEL_YUNET = (
    "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
FACE_MODEL_SFACE = (
    "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx"
)
