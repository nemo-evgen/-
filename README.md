# OSINT Person Search

> ⚠️ **Дисклеймер**: инструмент предназначен **исключительно для сбора информации из открытых источников (OSINT)** в законных целях — журналистские расследования, due diligence, кибербезопасность, поиск пропавших (по запросу правоохранительных органов), академические исследования. Использование для слежки, домогательств, шантажа или любого нарушения прав субъектов персональных данных **запрещено**. Ответственность за законность использования несёт оператор системы.

Полигональная OSINT-платформа для поиска информации о человеке по открытым источникам. Система принимает разрозненные идентификаторы (Telegram, VK, фотографию, ФИО + возраст + город + университет) и собирает, нормализует и связывает воедино всё, что человек опубликовал публично.

## Статус: Этапы 0–3 ✅

Реализовано и проверено (`tests/smoke_runner.py`, `tests/test_collectors.py`, `tests/test_photo.py`, `tests/test_storage_search.py`):

- ✅ `docker compose up` → API + мини-UI кейсов на `http://localhost:8000`
- ✅ Кейсы → поисковые джобы → очередь (Redis/Celery) → коллекторы → нормализация → досье
- ✅ Контракт коллектора-плагина + маршрутизация по типу входа
- ✅ 4 коллектора:
  - **`vk_profile`** — официальный **VK API** при `VK_TOKEN` (имя, bdate+возраст, город, университет, школы, друзья, стена); без токена — публичный og:* scrape
  - **`tg_profile`** — пассивный Telegram: `t.me/{nick}` (имено/описание/аватар) + `t.me/s/{nick}` (лента без входа)
  - **`dorks`** — генератор запросов Google/Yandex/Bing + **авто-выполнение** при `SERPER_API_KEY` / `SERPAPI_KEY` или `GOOGLE_API_KEY`+`GOOGLE_CSE_ID` (факты `search.result`)
  - **`username`** — Maigret (3000+ сайтов) + fallback на dorks
  - **`photo`** — загрузка/URL фото → EXIF (камера/дата/GPS), DCT-pHash-индекс,
    обратные ссылки (Яндекс/Lens/Bing), **сравнение с аватарками кейса**:
    pHash-матчи + локальная face-верификация (YuNet+SFace, ONNX скачивается в `models/`,
    без внешних face-сервисов; при недоступности сетей — честный warning)
- ✅ Модель: Case / SearchJob / Person / Account / Fact / AuditLog / Photo
- ✅ **Снимки-доказательства**: коллекторы сохраняют сырой HTML/JSON (MinIO в compose,
  file-режим без Docker), у фактов — `artifacts`, в UI кнопка «📑 снимок»
- ✅ **Полнотекстовый поиск**: OpenSearch (в compose) с SQL-fallback — эндпоинт
  `/api/search` + карточка «Полный поиск по фактам» в UI (работает по кириллице)
- ✅ **Entity resolution (Этап 3)**: авто-мердж людей с одинаковым нормализованным
  именем, скоринг по подсказкам (ФИО/город/вуз/возраст) с прозрачными сигналами
  в `Person.meta.signals`, граф связей (друзья → таблица `links` + UI-канвас),
  **очередь проверки**: сильные face- и точные pHash-совпадения между разными
  людьми → исследователь решает «сшить/отклонить» (human-in-the-loop)
- ✅ Аудит запусков, идемпотентность джоб, дедупликация фактов

## Быстрый старт

```bash
git clone <this-repo> && cd <repo>
docker compose up -d --build
# → UI: http://localhost:8000   API-доки: http://localhost:8000/docs
```

Далее в UI: создайте **кейс** (укажите правовое основание) → выберите тип входа → **Запустить поиск** → смотрите досье (аккаунты + факты со ссылками-источниками).

### Вариант без Docker (разработка)

```bash
python3 -m venv .venv && .venv/bin/pip install -r services/api/requirements.txt requests celery
export DATABASE_URL=sqlite:////tmp/osint.db EXEC_INLINE=1
export PYTHONPATH=$PWD:$PWD/services/worker
.venv/bin/uvicorn services.api.app.main:app --port 8000   # EXEC_INLINE=1 — без Redis
.venv/bin/python tests/smoke_runner.py                     # смоук-тест конвейера
```

## Типы входов

| Тип | Коллекторы | Результат |
|---|---|---|
| `vk` — ссылка/ник VK | `vk_profile` | **API**: профиль, bdate/возраст, город, **университет**, школы, друзья, посты стены · **fallback**: og:* метаданные |
| `name` — ФИО + возраст/город/вуз | `dorks` | ~30 запросов со ссылками + (при ключе) результаты выдачи |
| `username` — общий ник | `username` | досье Maigret (3000+ сайтов) + пассивные dorks |
| `telegram` — @ник (**только пассивно**) | `tg_profile` + `dorks` + `username` | профиль/канал с t.me, посты из `t.me/s/`, запросы по t.me, переиспользование нику |
| `photo` — файл (UI) или URL картинки | `photo` | EXIF+GPS, pHash, обратные ссылки, `photo.match`/`photo.face_match` против аватарок кейса |

## Структура репозитория

```
├── docker-compose.yml          # postgres, redis, api, worker
├── .env.example                # секреты и настройки (не коммитить .env)
├── core/                       # общее ядро (общий код API и воркера)
│   ├── config.py, db.py, models.py
│   ├── contract.py             # контракт коллектора (input → facts)
│   ├── routing.py              # тип входа → коллекторы
│   ├── normalize.py            # нормализация + наивная сшивка сущностей
│   ├── vk_collector.py, dorks_collector.py, username_collector.py
│   └── collector_base.py       # реестр плагинов
├── services/
│   ├── api/                    # FastAPI-шлюз + static/index.html (мини-UI)
│   └── worker/                 # Celery-воркер: runner + задачи
├── tests/smoke_runner.py       # смоук-тест без Docker (sqlite)
└── docs/
    ├── ARCHITECTURE.md         # архитектура, конвейеры, модель данных, роадмап
    └── TOOLS.md                # каталог OSINT-инструментов (лицензии, Docker)
```

## API (основное)

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/api/cases` | создать кейс `{name, legal_basis}` |
| `POST` | `/api/cases/{id}/searches` | запустить поиск `{input_type, input_value, hints}` |
| `POST` | `/api/cases/{id}/photos` | multipart-загрузка фото → джоба `photo` |
| `GET` | `/api/files/{name}` | отдать загруженное фото (hex32+ext, anti-traversal) |
| `GET` | `/api/search?q=…&case_id=` | полнотекстовый поиск по фактам (OS или SQL) |
| `GET` | `/api/snapshots?ref=…` | отдать снимок-доказательство (file:// / minio://) |
| `POST` | `/api/cases/{id}/resolve` | вручную прогнать сшивку (мердж/скоринг/граф/очередь) |
| `GET` | `/api/cases/{id}/graph` | узлы и рёбра графа связей для UI |
| `POST` | `/api/reviews/{id}/decision` | решение исследователя `{action: approve\|reject}` |
| `GET` | `/api/cases/{id}` | досье: джобы + люди + аккаунты + факты + ожидающие проверки |
| `GET` | `/api/jobs/{id}` | статус джобы |
| `GET` | `/healthz` | здоровье |
| `GET` | `/docs` | Swagger (FastAPI) |

## Планы (см. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))

- **Этап 2** ✅: photo-collector, снимки-доказательства (MinIO/file), полнотекст (OpenSearch/SQL)
- **Этап 3** ✅: entity resolution (автомердж + скоринг со сигналами), граф связей, очередь проверки (approve/reject) в UI
- **Этап 4**: RBAC, аудит-вью, метрики, бэкапы, K8s

## Правовые основы

- Сбор **только** из открытых источников; обход CAPTCHA, paywall и авторизации чужих аккаунтов не производится.
- Аудит всех поисковых запросов (`AuditLog`).
- Персональные данные — только в объёме кейса, с возможностью удаления (152-ФЗ / GDPR).

## Лицензия

MIT. Сторонние инструменты сохраняют собственные лицензии — см. [`docs/TOOLS.md`](docs/TOOLS.md).
