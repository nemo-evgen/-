"""Telegram-коллектор — ТОЛЬКО пассивные методы (согласовано: active = нет).

    GET https://t.me/{nick}     → og:* метаданные (имя/описание/аватар)
    GET https://t.me/s/{nick}   → публичная лента канала без входа (посты)

Никакой авторизации и Telethon — только то, что Telegram отдаёт анонимному
гостю по HTTPS. Для @user-профилей /s/ недоступен (404) — это нормально,
posts-факты просто не появятся.
"""
from __future__ import annotations

import html as html_lib
import re

import requests

from core import config
from core.collector_base import collector
from core.contract import CollectorFact, CollectorInput, CollectorResult
from core.pagemeta import parse_meta, strip_tags

_DATA_POST = re.compile(r'data-post="(?P<url>https://t\.me/[^"]+)"')
_TEXT_BLOCK = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(?P<text>.*?)</div>', re.S
)
_TIME = re.compile(r'<time[^>]*datetime="(?P<dt>[^"]+)"')


def normalize_nick(value: str) -> str:
    v = value.strip()
    v = re.sub(r"^https?://(www\.)?(t\.me|telegram\.me)/", "", v, flags=re.I)
    v = v.split("?")[0].split("/")[0]
    return v.lstrip("@")


def _get(url: str) -> str | None:
    try:
        resp = requests.get(
            url,
            timeout=config.TG_TIMEOUT,
            headers={"User-Agent": config.HTTP_USER_AGENT},
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def _parse_messages(page: str) -> list[dict]:
    """Сообщения из t.me/s/{nick}: {url, date, text}."""
    out: list[dict] = []
    # разбиваем по data-post — так не «съедаем» соседние сообщения
    parts = page.split('data-post="')
    for part in parts[1:]:
        if len(out) >= config.TG_POSTS_LIMIT:
            break
        url = part.split('"', 1)[0]
        window = part[:8000]
        tm = _TEXT_BLOCK.search(window)
        if not tm:
            continue
        text = strip_tags(tm.group("text"))
        if not text:
            continue
        dtm = _TIME.search(window)
        out.append(
            {
                "url": url,
                "date": dtm.group("dt") if dtm else None,
                "text": html_lib.unescape(text)[:700],
            }
        )
    return out


@collector("tg_profile")
def run(job: CollectorInput) -> CollectorResult:
    res = CollectorResult(collector="tg_profile")
    nick = normalize_nick(job.value)
    if not nick or len(nick) > 64 or " " in nick:
        res.warnings.append(f"tg_profile: некорректный ник «{job.value}»")
        res.ok = False
        return res

    profile_url = f"https://t.me/{nick}"
    page = _get(profile_url)
    if page is None:
        res.warnings.append(f"tg_profile: страница {profile_url} недоступна")
        res.ok = False
        return res

    meta = parse_meta(page)
    title = meta.get("og:title") or ""
    name: str | None = None
    if title and not title.lower().startswith("telegram: contact"):
        name = title.split("|")[0].strip() or None

    res.facts.append(
        CollectorFact(
            kind="account.profile",
            value={"platform": "telegram", "handle": nick, "url": profile_url, "name": name},
            source_url=profile_url,
            confidence=0.85,
        )
    )
    if meta.get("og:image"):
        res.facts.append(
            CollectorFact(
                kind="photo.avatar",
                value={"platform": "telegram", "handle": nick, "url": meta["og:image"]},
                source_url=profile_url,
                confidence=0.8,
            )
        )
    desc = meta.get("og:description") or meta.get("description")
    if desc:
        res.facts.append(
            CollectorFact(
                kind="profile.bio",
                value={"text": desc[:2000]},
                source_url=profile_url,
                confidence=0.75,
            )
        )

    # публичная лента (каналы/боты); у обычных юзеров — 404, это ок
    preview = _get(f"https://t.me/s/{nick}")
    if preview:
        messages = _parse_messages(preview)
        for msg in messages:
            res.facts.append(
                CollectorFact(
                    kind="profile.post",
                    value={"date": msg["date"], "text": msg["text"], "url": msg["url"]},
                    source_url=msg["url"],
                    confidence=0.6,
                )
            )
        if not messages:
            res.warnings.append("tg_profile: лента пуста (профиль пользователя, а не канала?)")
    else:
        res.warnings.append("tg_profile: /s/-лента недоступна — постов не будет")

    if not (name or desc):
        res.warnings.append("tg_profile: публичные метаданные пусты")
    return res
