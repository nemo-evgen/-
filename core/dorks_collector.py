"""Коллектор dorks: генерация поисковых запросов + (опционально) авто-выполнение.

Этап 1:
  * всегда — ГОТОВЫЕ ссылки (Google/Yandex/Bing) как факты search.query;
  * при наличии SERPAPI_KEY или (GOOGLE_API_KEY + GOOGLE_CSE_ID) —
    исполняет первые DORK_MAX_EXEC запросов и кладёт search.result-факты.
"""
from __future__ import annotations

import string
from urllib.parse import quote_plus

import requests

from core import config
from core.collector_base import collector
from core.contract import CollectorFact, CollectorInput, CollectorResult

# {name} {city} {university} {age} {nick}
NAME_TEMPLATES: list[str] = [
    '"{name}"',
    '"{name}" "{city}"',
    '"{name}" "{city}" "{university}"',
    'site:vk.com "{name}" "{city}"',
    'site:t.me "{name}"',
    'site:linkedin.com/in "{name}"',
    'inurl:resume "{name}"',
    '"{name}" "{university}" filetype:pdf',
    '"{name}" "{city}" filetype:pdf',
    '"{name}" {age} лет',
]

NICK_TEMPLATES: list[str] = [
    '"{nick}" site:vk.com',
    '"{nick}" site:t.me',
    '"{nick}" site:github.com',
    '"{nick}" site:instagram.com',
    '"{nick}"',
    '"{nick}" email OR пароль',
]

ENGINES: dict[str, str] = {
    "google": "https://www.google.com/search?q={q}",
    "yandex": "https://yandex.ru/search/?text={q}",
    "bing": "https://www.bing.com/search?q={q}",
}


def _render(template: str, params: dict[str, str]) -> str | None:
    """Рендер шаблона; шаблон пропускается, если какие-то поля пустые."""
    fields = [
        fname
        for _, fname, _, _ in string.Formatter().parse(template)
        if fname
    ]
    if not fields:
        return None
    if any(not str(params.get(f, "")).strip() for f in fields):
        return None
    try:
        query = template.format(**params)
    except (KeyError, IndexError, ValueError):
        return None
    if "{" in query or "}" in query:
        return None
    return query


def build_queries(params: dict[str, str], templates: list[str]) -> list[tuple[str, str, str]]:
    """→ [(engine, query, url), ...]"""
    out: list[tuple[str, str, str]] = []
    for tpl in templates:
        q = _render(tpl, params)
        if not q:
            continue
        for engine, url_tpl in ENGINES.items():
            out.append((engine, q, url_tpl.format(q=quote_plus(q))))
    return out


# ---------- авто-выполнение (нужен ключ Serper/SerpAPI/Google CSE) ----------

def _provider() -> str | None:
    if config.SERPER_API_KEY:
        return "serper"
    if config.SERPAPI_KEY:
        return "serpapi"
    if config.GOOGLE_API_KEY and config.GOOGLE_CSE_ID:
        return "google_cse"
    return None


def _search_serper(query: str) -> list[dict]:
    """serper.dev — POST https://google.serper.dev/search, заголовок X-API-KEY."""
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={
            "X-API-KEY": config.SERPER_API_KEY,
            "Content-Type": "application/json",
        },
        json={"q": query, "num": 10},
        timeout=config.DORK_EXEC_TIMEOUT,
    )
    resp.raise_for_status()
    return [
        {
            "rank": r.get("position"),
            "title": r.get("title"),
            "link": r.get("link"),
            "snippet": r.get("snippet"),
        }
        for r in resp.json().get("organic", [])[:10]
    ]


def _search_serpapi(query: str) -> list[dict]:
    resp = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": "google",
            "q": query,
            "api_key": config.SERPAPI_KEY,
            "num": 10,
        },
        timeout=config.DORK_EXEC_TIMEOUT,
        headers={"User-Agent": config.HTTP_USER_AGENT},
    )
    resp.raise_for_status()
    return [
        {
            "rank": r.get("position"),
            "title": r.get("title"),
            "link": r.get("link"),
            "snippet": r.get("snippet"),
        }
        for r in resp.json().get("organic_results", [])[:10]
    ]


def _search_google_cse(query: str) -> list[dict]:
    resp = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={
            "q": query,
            "cx": config.GOOGLE_CSE_ID,
            "key": config.GOOGLE_API_KEY,
            "num": 10,
        },
        timeout=config.DORK_EXEC_TIMEOUT,
        headers={"User-Agent": config.HTTP_USER_AGENT},
    )
    resp.raise_for_status()
    return [
        {
            "rank": i + 1,
            "title": item.get("title"),
            "link": item.get("link"),
            "snippet": item.get("snippet"),
        }
        for i, item in enumerate(resp.json().get("items", [])[:10])
    ]


def execute_queries(queries: list[str]) -> tuple[list[CollectorFact], list[str]]:
    """Исполняет до DORK_MAX_EXEC уникальных запросов. → (facts, warnings)."""
    provider = _provider()
    if provider is None or not queries:
        return [], []

    facts: list[CollectorFact] = []
    warnings: list[str] = []
    failures = 0
    for q in queries[: config.DORK_MAX_EXEC]:
        try:
            if provider == "serper":
                items = _search_serper(q)
            elif provider == "serpapi":
                items = _search_serpapi(q)
            else:
                items = _search_google_cse(q)
        except Exception as exc:  # noqa: BLE001 — квота/сеть, не роняем джоб
            failures += 1
            warnings.append(f"dorks: запрос не выполнен ({exc.__class__.__name__})")
            if failures >= 3:
                warnings.append("dorks: 3 подряд ошибки — авто-выполнение остановлено")
                break
            continue
        for item in items:
            if not item.get("link"):
                continue
            facts.append(
                CollectorFact(
                    kind="search.result",
                    value={
                        "query": q,
                        "provider": provider,
                        "rank": item.get("rank"),
                        "title": item.get("title"),
                        "snippet": item.get("snippet"),
                    },
                    source_url=item["link"],
                    confidence=0.5,
                )
            )
    if provider:
        warnings.append(
            f"dorks: авто-выполнение включено ({provider}, "
            f"{min(len(queries), config.DORK_MAX_EXEC)} запросов из квоты)"
        )
    return facts, warnings


@collector("dorks")
def run(job: CollectorInput) -> CollectorResult:
    res = CollectorResult(collector="dorks")
    hints = job.hints or {}
    params = {
        "name": hints.get("full_name") or job.value,
        "city": hints.get("city") or "",
        "university": hints.get("university") or "",
        "age": str(hints.get("age") or ""),
        "nick": hints.get("nick") or job.value,
    }
    if not params["name"] and not params["nick"]:
        res.warnings.append("dorks: нет ни ФИО, ни ника")
        res.ok = False
        return res

    templates = NICK_TEMPLATES if job.input_type in ("username", "telegram") else NAME_TEMPLATES
    seen: set[tuple[str, str]] = set()
    unique_queries: list[str] = []
    for engine, q, url in build_queries(params, templates):
        if (engine, q) in seen:
            continue
        seen.add((engine, q))
        if q not in unique_queries:
            unique_queries.append(q)
        res.facts.append(
            CollectorFact(
                kind="search.query",
                value={"engine": engine, "query": q, "url": url},
                source_url=url,
                confidence=0.4,  # это запрос, а не подтверждённый факт
            )
        )

    exec_facts, exec_warn = execute_queries(unique_queries)
    res.facts.extend(exec_facts)
    res.warnings.extend(exec_warn)
    return res
