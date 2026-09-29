#!/usr/bin/env bash
# Бэкап БД OSINT Person Search (Этап 4).
#   sqlite:   python-снимок (sqlite3 backup API) → backups/
#   postgres: pg_dump → backups/
# Использование: ./scripts/backup.sh [путь-назначения]
set -euo pipefail

cd "$(dirname "$0")/.."
DB_URL="${DATABASE_URL:-}"
mkdir -p backups
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ "$DB_URL" == postgresql* || "$DB_URL" == postgres* ]]; then
  OUT="${1:-backups/osint-$STAMP.sql.gz}"
  # DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db → pg-формат URL
  PG_URL="${DB_URL/\/\//\/\/}"          # без изменений
  PG_URL="${PG_URL/ql+psycopg:/ql:}"    # ql+psycopg: → ql:
  PG_URL="${PG_URL/postgresql:/postgres://}"
  if ! command -v pg_dump >/dev/null 2>&1; then
    echo "pg_dump не найден — установите postgresql-client" >&2
    exit 1
  fi
  PGPASSWORD="$(echo "$PG_URL" | sed -E 's#^postgres://[^:]+:([^@]+)@.*#\1#')" \
    pg_dump --no-owner --no-privileges \
    "$(echo "$PG_URL" | sed -E 's#^postgres://[^:]+:[^@]+@#postgres://#')" \
    | gzip > "$OUT"
  echo "OK: $OUT ($(du -h "$OUT" | cut -f1))"
else
  # sqlite: файл из DATABASE_URL или DATABASE_URL_OVERRIDE
  OUT="${1:-backups/osint-$STAMP.db}"
  SRC="${DATABASE_URL_OVERRIDE:-$DB_URL}"
  SRC="${SRC#*///}"                      # sqlite+pysqlite:///path → path
  SRC="${SRC#sqlite:////}"; SRC="${SRC#sqlite:///}"
  SRC="${SRC#sqlite://}"
  if [[ -z "$SRC" || ! -f "$SRC" ]]; then
    echo "sqlite-файл не найден (DATABASE_URL=$DB_URL)" >&2
    exit 1
  fi
  python3 - "$SRC" "$OUT" <<'PY'
import sqlite3, sys
src, out = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as a, sqlite3.connect(out) as b:
    a.backup(b)          # online backup: консистентно, без остановки сервиса
print(f"OK: {out}")
PY
  echo "$(du -h "$OUT" | cut -f1)"
fi
