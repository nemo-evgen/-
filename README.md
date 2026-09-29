# OSINT Person Search

> ⚠️ **Дисклеймер**: инструмент предназначен **исключительно для сбора информации из открытых источников (OSINT)** в законных целях — журналистские расследования, due diligence, кибербезопасность, поиск пропавших (по запросу правоохранительных органов), академические исследования. Использование для слежки, домогательств, шантажа или любого нарушения прав субъектов персональных данных **запрещено**. Ответственность за законность использования несёт оператор системы.

Полигональная OSINT-платформа для поиска информации о человеке по открытым источникам. Система принимает разрозненные идентификаторы (Telegram, VK, фотографию, ФИО + возраст + город + университет) и собирает, нормализует и связывает воедино всё, что человек опубликовал публично.

## Статус: Этапы 0–4 ✅

Реализовано и проверено (`tests/smoke_runner.py`, `tests/test_collectors.py`, `tests/test_photo.py`, `tests/test_storage_search.py`, `tests/test_resolver.py`, `tests/test_rbac.py`):

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
- ✅ **Снимки-доказательства**: коллекторы сохраняют сырой HTML/JSON (file-режим по
  умолчанию; MinIO — в профиле `compose full`), у фактов — `artifacts`, в UI кнопка «📑 снимок»
- ✅ **Полнотекстовый поиск**: SQL-fallback по умолчанию; OpenSearch — в профиле `compose full` — эндпоинт
  `/api/search` + карточка «Полный поиск по фактам» в UI (работает по кириллице)
- ✅ **Entity resolution (Этап 3)**: авто-мердж людей с одинаковым нормализованным
  именем, скоринг по подсказкам (ФИО/город/вуз/возраст) с прозрачными сигналами
  в `Person.meta.signals`, граф связей (друзья → таблица `links` + UI-канвас),
  **очередь проверки**: сильные face- и точные pHash-совпадения между разными
  людьми → исследователь решает «сшить/отклонить» (human-in-the-loop)
- ✅ **Эксплуатация (Этап 4)**: RBAC через `API_KEYS` (viewer/analyst/admin,
  заголовок `X-API-Key`), журнал аудита в UI (`GET /api/audit`),
  Prometheus-метрики (`GET /metrics`), бэкапы БД (`POST /api/admin/backup`
  для sqlite / `scripts/backup.sh` для postgres), эталонные K8s-манифесты
  (`deploy/k8s/`)
- ✅ Аудит запусков, идемпотентность джоб, дедупликация фактов

## Быстрый старт

```bash
git clone <this-repo> && cd <repo>
docker compose up -d --build
# → UI: http://localhost:8000   API-доки: http://localhost:8000/docs
```

> По умолчанию поднимаются только postgres, redis, api, worker — этого
> достаточно: полнотекстовый поиск работает на SQL-fallback, снимки-доказательства
> в файлы (`data/snapshots`). Опциональные opensearch+minio — профиль `full`:
> `docker compose --profile full up -d` (+ `OPENSEARCH_URL`/`MINIO_ENDPOINT` в `.env`);
> образ minio/minio удалён с Docker Hub (09.2026), поэтому в профиле используется
> сборка Chainguard (`cgr.dev/chainguard/minio`).

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
│   ├── api/                    # FastAPI-шлюз + static/index.html (мини-UI) + rbac.py
│   └── worker/                 # Celery-воркер: runner + задачи
├── scripts/backup.sh           # бэкап БД: sqlite online-backup / pg_dump
├── deploy/k8s/                 # эталонные манифесты Kubernetes (Этап 4)
├── tests/                      # смоук + юниты (collectors/photo/search/resolver/rbac)
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
| `GET` | `/api/audit?case_id=&limit=` | журнал действий исследователя (viewer) |
| `GET` | `/metrics` | Prometheus-метрики (без аутентификации — по соглашению) |
| `POST` | `/api/admin/backup` | снимок БД в `backups/` (только admin; sqlite) |
| `GET` | `/api/jobs/{id}` | статус джобы |
| `GET` | `/healthz` | здоровье |
| `GET` | `/docs` | Swagger (FastAPI) |

## Планы (см. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))

- **Этап 2** ✅: photo-collector, снимки-доказательства (MinIO/file), полнотекст (OpenSearch/SQL)
- **Этап 3** ✅: entity resolution (автомердж + скоринг со сигналами), граф связей, очередь проверки (approve/reject) в UI
- **Этап 4** ✅: RBAC (API_KEYS), журнал аудита в UI, метрики Prometheus, бэкапы, K8s-манифесты (`deploy/k8s/`, эталонные — в песочнице кластера не гонялись)
- **Дальше**: плагины сообщества, прогон K8s на kind/minikube, доведение эксплуатационных сценариев (вебхуки алертов, экспорт досье)

## Доступ и RBAC (Этап 4)

Задаётся одной переменной (в `.env` для compose или окружении):

```bash
API_KEYS=admin:секрет1,analyst:секрет2,viewer:секрет3
```

- **viewer** — только чтение (GET);
- **analyst** — + запуск поисков, фото, resolve, решения в очереди проверки;
- **admin** — + `/api/admin/*` (бэкапы).
- Ключ передаётся заголовком `X-API-Key` (или `?api_key=` для curl); в UI —
  поле «API-ключ» в шапке (сохраняется в localStorage).
- **`API_KEYS` пуст → открытый режим** (удобно для разработки и тестов).
- Без ключа при включённом RBAC → `401`; недостаточная роль → `403`.
- Публичны без ключа: `/`, `/static`, `/healthz`, `/metrics`, `/docs`.

```bash
curl -H "X-API-Key: секрет3" localhost:8000/api/cases            # viewer: 200
curl -X POST -H "X-API-Key: секрет3" localhost:8000/api/cases    # viewer: 403
curl -X POST -H "X-API-Key: секрет1" localhost:8000/api/admin/backup
./scripts/backup.sh          # cron/ручной бэкап: sqlite снимок или pg_dump
```

## Правовые основы

- Сбор **только** из открытых источников; обход CAPTCHA, paywall и авторизации чужих аккаунтов не производится.
- Аудит всех поисковых запросов (`AuditLog`).
- Персональные данные — только в объёме кейса, с возможностью удаления (152-ФЗ / GDPR).

## Лицензия

MIT. Сторонние инструменты сохраняют собственные лицензии — см. [`docs/TOOLS.md`](docs/TOOLS.md).
