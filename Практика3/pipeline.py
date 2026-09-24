"""
Практика DRP: пайплайн двойников — генерация покрытия, фильтр существования, отсмотр.

Скрипт почти готов. Ваши задачи — сделать несколько небольших правок и запустить
его на СВОЁМ целевом домене:

  1) ниже в TARGET впишите свой домен (реальный бренд/сайт);
  2) допишите filter_existing() — оставить только живые домены;
  3) в coverage.py найдите и добавьте свои сервисы-площадки (см. # TODO там).

Затем:
  python pipeline.py --demo     # проверка без ключа urlscan (заглушки вместо скринов)
  python pipeline.py            # боевой прогон: ключ из .env → report.html со скринами

Сдать нужно: report.html по вашему домену и его скриншот.
"""
import os, json, argparse, html
from urlscan_helper import scan_domains

# Ключ urlscan из .env (URLSCAN_API_KEY=...). Работает с python-dotenv и без него.
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    from pathlib import Path
    _env = Path(".env")
    if _env.exists():
        for _l in _env.read_text(encoding="utf-8").splitlines():
            _l = _l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k, _v = _l.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ================== НАСТРОЙКА (это и меняете) ==================
TARGET = "goldapple.ru"     # ← ВПИШИТЕ СВОЙ ДОМЕН (реальный, напр. ozon.ru)
CAP = 40                    # сколько существующих доменов отсматривать через urlscan
# ==============================================================


def filter_existing(candidates):
    """Фильтрация живых доменов из всего сгенерированного списка."""
    out = []
    for c in candidates:
        # # TODO Подумайте что тут указать вместо знаков "???" (На выбор: dns_a, dns_mx, dns_nap или dns_ns)  a = [x for x in c["dns_a"] if x and not x.startswith("!")]
        if a:                       
            out.append(c)
    return out


def build_html(results, target, out="report.html"):
    """Список существующих двойников со скриншотами из urlscan."""
    rows = ""
    for r in results:
        dom = html.escape(r["domain"])
        if r["screenshot_url"]:
            shot = f'<img src="{html.escape(r["screenshot_url"])}" width="240" loading="lazy">'
        else:
            shot = '<span style="color:#999">нет скрина</span>'
        note = html.escape(r["note"] or "")
        if r.get("malicious"):
            note += ' <b style="color:#b00">⚠</b>'
        if r["status"] != "done":
            note = f'<i style="color:#999">{html.escape(r["status"])}</i> {note}'
        link = f'<a href="{html.escape(r["report_url"])}">скан</a>' if r["report_url"] else "—"
        rows += f"<tr><td>{dom}</td><td>{shot}</td><td>{note}</td><td>{link}</td></tr>\n"
    page = f"""<!doctype html><meta charset="utf-8"><title>Двойники {html.escape(target)}</title>
<style>body{{font:14px system-ui;margin:24px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ddd;padding:8px;vertical-align:top}}img{{border:1px solid #ccc}}</style>
<h1>Существующие двойники {html.escape(target)} — со скриншотами</h1>
<table><tr><th>Домен</th><th>Скриншот</th><th>Что на домене</th><th>Скан</th></tr>
{rows}</table>"""
    open(out, "w", encoding="utf-8").write(page)
    print(f"Отчёт сохранён: {out}")


def summarize(results):
    done = [r for r in results if r["status"] == "done"]
    withshot = [r for r in done if r["screenshot_url"]]
    print(f"ВЫВОД: проверено {len(done)} существующих двойников, "
          f"со скринами {len(withshot)}. Откройте report.html — отметьте копии витрин "
          "и домены с формой входа как кандидатов на разбор человеком.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="urlscan офлайн (без ключа)")
    args = ap.parse_args()

    import coverage
    print(f"Строю покрытие для {TARGET} (~1–2 минуты)...")
    candidates = coverage.build_candidates(TARGET)
    print(f"Сгенерировано с покрытием: {len(candidates)}")

    existing = filter_existing(candidates)
    print(f"Существует после фильтра: {len(existing)}")
    if not existing:
        print("  Пусто. Причины: (1) filter_existing() ещё не дописана (ваш TODO); "
              "(2) домен малоизвестный — возьмите крупный бренд, у него больше двойников.")
        return

    results = scan_domains([c["domain"] for c in existing], cap=CAP, demo=args.demo)
    build_html(results, TARGET)
    summarize(results)


if __name__ == "__main__":
    main()
