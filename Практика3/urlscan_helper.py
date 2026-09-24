"""
urlscan_helper.py  —  ГОТОВАЯ обвязка urlscan.io (менять не нужно).

Прячет всё «неинтересное»: сабмит, ожидание результата по UUID,
обработку лимитов, пустых скриншотов и ошибок. Вы вызываете одну функцию
scan_domains(...) и получаете аккуратные записи для отчёта.

Скриншот берётся из ПЕСОЧНИЦЫ urlscan, а не вашим браузером — то есть на
живой двойник вы со своего IP не заходите. Это принципиально: локально
скринить живой фишинг нельзя.

Два режима:
  * боевой  — нужен бесплатный API-ключ urlscan (переменная URLSCAN_API_KEY);
  * демо    — demo=True, работает офлайн на заранее заготовленных данных.
              Пригодится, если urlscan недоступен или кончилась квота.
"""
import os, time

# ---- заготовка для демо-режима / фолбэка (реальные домены, правдоподобные исходы) ----
_DEMO = {
    "goldapple.live":  {"outcome": "паркинг",  "score": 0,  "malicious": False},
    "goldapple.net":   {"outcome": "редирект", "score": 0,  "malicious": False},
    "goldapple.com":   {"outcome": "наш/редирект на бренд", "score": 0, "malicious": False},
    "goldapple.shop":  {"outcome": "копия витрины", "score": 6, "malicious": True},
    "goldapple.top":   {"outcome": "пусто / заглушка", "score": 0, "malicious": False},
    "goldapple.store": {"outcome": "редирект", "score": 0, "malicious": False},
    "goldapple.site":  {"outcome": "паркинг", "score": 0, "malicious": False},
    "golclapple.ru":   {"outcome": "паркинг", "score": 0, "malicious": False},
    "goldaple.ru":     {"outcome": "пусто / недоступен", "score": 0, "malicious": False},
    "qoldapple.ru":    {"outcome": "паркинг", "score": 0, "malicious": False},
}
# серый плейсхолдер вместо картинки в демо-режиме (в бою тут реальный скрин urlscan)
_DEMO_IMG = ("data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='240' height='150'>"
    "<rect width='240' height='150' fill='%23e8e8ee'/>"
    "<text x='120' y='78' font-size='13' fill='%23888' text-anchor='middle'>demo screenshot</text></svg>")


def _record(domain, status, uuid="", screenshot_url="", report_url="",
            score=None, malicious=None, note=""):
    return {"domain": domain, "status": status, "uuid": uuid,
            "screenshot_url": screenshot_url, "report_url": report_url,
            "score": score, "malicious": malicious, "note": note}


def _scan_one_demo(domain):
    d = _DEMO.get(domain) or {"outcome": "паркинг", "score": 0, "malicious": False}
    return _record(domain, "done",
                   uuid="demo-" + domain,
                   screenshot_url=_DEMO_IMG,
                   report_url="https://urlscan.io/",
                   score=d["score"], malicious=d["malicious"],
                   note=d["outcome"])


def _scan_one_live(client, domain):
    """Один домен через официальный клиент urlscan-python."""
    try:
        res = client.scan_and_get_result("http://" + domain, visibility="public")
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "rate" in msg or "quota" in msg:
            return _record(domain, "rate_limited", note="лимит urlscan — попробуйте позже")
        return _record(domain, "error", note=f"{type(e).__name__}: {e}")
    task = res.get("task", {}) or {}
    verd = (res.get("verdicts", {}) or {}).get("overall", {}) or {}
    shot = task.get("screenshotURL", "")            # https://urlscan.io/screenshots/<uuid>.png
    return _record(domain, "done",
                   uuid=task.get("uuid", ""),
                   screenshot_url=shot,             # может быть пустым — обработайте в отчёте
                   report_url=task.get("reportURL", ""),
                   score=verd.get("score"),
                   malicious=verd.get("malicious"),
                   note="" if shot else "скриншот не сохранён")


def scan_domains(domains, api_key=None, cap=10, demo=False, pause=2.0):
    """
    Прогоняет список доменов через urlscan и возвращает список записей.
    cap  — максимум доменов (бережём квоту и время; лишние помечаются 'skipped').
    demo — офлайн-режим на заготовленных данных.
    """
    domains = list(dict.fromkeys(domains))          # дедуп, порядок сохраняется
    picked, skipped = domains[:cap], domains[cap:]
    out = []

    if demo:
        for d in picked:
            out.append(_scan_one_demo(d))
    else:
        key = api_key or os.environ.get("URLSCAN_API_KEY")
        if not key:
            raise RuntimeError("Нет ключа urlscan. Задайте URLSCAN_API_KEY или demo=True.")
        import urlscan
        with urlscan.Client(key) as client:
            for i, d in enumerate(picked):
                out.append(_scan_one_live(client, d))
                if i < len(picked) - 1:
                    time.sleep(pause)               # вежливая пауза между сабмитами

    for d in skipped:
        out.append(_record(d, "skipped", note=f"за пределами cap={cap}"))
    return out
