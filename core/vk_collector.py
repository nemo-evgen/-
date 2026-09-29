"""VK-коллектор: официальный VK API (при VK_TOKEN) + пассивный og:* fallback.

API-путь (Этап 1.1):
    users.get    → имя, domain, bdate (+возраст), city, universities, schools,
                   status, about, counters, photo_200
    friends.get  → graph.friend-факты (ограничение VK_FRIENDS_LIMIT)
    wall.get     → profile.post-факты (VK_WALL_LIMIT)
Fallback (без токена / при ошибке API):
    публичная страница профиля → og:* метаданные (как раньше).
"""
from __future__ import annotations

import datetime as dt
import re

import requests

from core import config
from core.collector_base import collector
from core.contract import CollectorFact, CollectorInput, CollectorResult
from core.pagemeta import parse_meta


class VKApiError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None):
        super().__init__(msg)
        self.code = code


def canonicalize(value: str) -> tuple[str, str]:
    """'https://vk.com/durov' | 'vk.com/durov' | 'durov' → (url, handle)."""
    v = value.strip().rstrip("/")
    if not re.match(r"^https?://", v, re.IGNORECASE):
        v = v if "vk.com" in v.lower() else f"https://vk.com/{v}"
        if not v.startswith("http"):
            v = f"https://{v}"
    m = re.search(r"vk\.com/([^/?#]+)", v, re.IGNORECASE)
    handle = m.group(1) if m else v.rstrip("/").split("/")[-1]
    return f"https://vk.com/{handle}", handle


def vk_api(method: str, **params):
    """Вызов метода VK API. Бросает VKApiError при ошибке."""
    if not config.VK_TOKEN:
        raise VKApiError("VK_TOKEN не задан")
    params.setdefault("v", config.VK_API_VERSION)
    params["access_token"] = config.VK_TOKEN
    resp = requests.get(
        f"https://api.vk.com/method/{method}",
        params=params,
        timeout=config.VK_API_TIMEOUT,
        headers={"User-Agent": config.HTTP_USER_AGENT},
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        err = data["error"] or {}
        raise VKApiError(str(err.get("error_msg", "vk error")), err.get("error_code"))
    return data["response"]


def _age_from_bdate(bdate: str) -> int | None:
    parts = (bdate or "").split(".")
    if len(parts) != 3:
        return None
    try:
        birth = dt.date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None
    today = dt.date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _run_api(job: CollectorInput, url: str, handle: str) -> CollectorResult | None:
    """Полный API-путь. None → вызывающий уходит в fallback."""
    res = CollectorResult(collector="vk_profile")
    users = vk_api("users.get", user_ids=handle, fields=(
        "photo_200,domain,about,bdate,city,universities,schools,status,counters,deactivated"
    ))
    if not users:
        res.warnings.append("vk_profile: VK API не вернул пользователя")
        res.ok = False
        return res
    u = users[0]
    if u.get("deactivated"):
        res.warnings.append(f"vk_profile: профиль деактивирован/заблокирован ({u['deactivated']})")

    domain = u.get("domain") or str(u["id"])
    profile_url = f"https://vk.com/{domain}"
    name = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()

    res.facts.append(
        CollectorFact(
            kind="account.profile",
            value={"platform": "vk", "handle": domain, "url": profile_url, "name": name},
            source_url=profile_url,
            confidence=0.95,
        )
    )
    if u.get("photo_200"):
        res.facts.append(
            CollectorFact(
                kind="photo.avatar",
                value={"platform": "vk", "handle": domain, "url": u["photo_200"]},
                source_url=profile_url,
                confidence=0.9,
            )
        )

    demo: dict = {}
    if u.get("bdate"):
        demo["bdate"] = u["bdate"]
        age = _age_from_bdate(u["bdate"])
        if age is not None:
            demo["age"] = age
    if u.get("city"):
        demo["city"] = u["city"].get("title")
    if u.get("universities"):
        demo["universities"] = [
            {
                "name": uni.get("name"),
                "faculty": uni.get("faculty"),
                "chair": uni.get("chair"),
                "graduation_year": uni.get("graduation_year"),
            }
            for uni in u["universities"]
        ]
    if u.get("schools"):
        demo["schools"] = [
            {"name": s.get("name"), "year_graduated": s.get("year_graduated")}
            for s in u["schools"]
        ]
    if u.get("status"):
        demo["status"] = u["status"][:500]
    if demo:
        res.facts.append(
            CollectorFact(
                kind="profile.demo",
                value=demo,
                source_url=profile_url,
                confidence=0.9,  # самое ценное для сценария S4
            )
        )

    bio_bits = {"about": (u.get("about") or "")[:1000]}
    if u.get("counters"):
        bio_bits["counters"] = {
            k: v for k, v in u["counters"].items() if k in ("photos", "friends", "groups", "videos", "audios")
        }
    if bio_bits.get("about") or bio_bits.get("counters"):
        res.facts.append(
            CollectorFact(kind="profile.bio", value=bio_bits, source_url=profile_url, confidence=0.85)
        )

    uid = u["id"]

    if config.VK_FRIENDS_LIMIT > 0:
        try:
            fr = vk_api(
                "friends.get",
                user_id=uid,
                count=config.VK_FRIENDS_LIMIT,
                fields="screen_name,photo_200",
            )
            for f in fr.get("items", []):
                f_handle = f.get("screen_name") or str(f["id"])
                res.facts.append(
                    CollectorFact(
                        kind="graph.friend",
                        value={
                            "platform": "vk",
                            "friend_id": f["id"],
                            "handle": f_handle,
                            "name": f"{f.get('first_name', '')} {f.get('last_name', '')}".strip(),
                            "url": f"https://vk.com/{f_handle}",
                        },
                        source_url=profile_url,
                        confidence=0.6,
                    )
                )
        except VKApiError as exc:
            # обычные причины: приватные друзья, ограничения для чужих токенов
            res.warnings.append(f"vk_profile: друзья недоступны ({exc})")

    if config.VK_WALL_LIMIT > 0:
        try:
            wall = vk_api("wall.get", owner_id=uid, count=config.VK_WALL_LIMIT)
            for post in wall.get("items", []):
                text = (post.get("text") or "").strip()
                if not text or post.get("from_id") != uid:
                    continue
                res.facts.append(
                    CollectorFact(
                        kind="profile.post",
                        value={
                            "date": post.get("date"),
                            "text": text[:700],
                            "url": f"{profile_url}?w=wall{uid}_{post['id']}",
                        },
                        source_url=profile_url,
                        confidence=0.6,
                    )
                )
        except VKApiError as exc:
            res.warnings.append(f"vk_profile: стена недоступна ({exc})")

    return res


def _run_scrape(job: CollectorInput, url: str, handle: str) -> CollectorResult:
    """Fallback: публичная страница без авторизации (og:*)."""
    res = CollectorResult(collector="vk_profile")
    try:
        resp = requests.get(
            url,
            timeout=config.VK_FETCH_TIMEOUT,
            headers={"User-Agent": config.HTTP_USER_AGENT},
            allow_redirects=True,
        )
        resp.raise_for_status()
        meta = parse_meta(resp.text)
    except Exception as exc:  # noqa: BLE001 — источник может быть недоступен
        res.warnings.append(f"vk_profile: страница недоступна ({exc.__class__.__name__})")
        res.facts.append(
            CollectorFact(
                kind="account.profile",
                value={
                    "platform": "vk",
                    "handle": handle,
                    "url": url,
                    "name": None,
                    "simulated": True,
                },
                source_url=url,
                confidence=0.1,
            )
        )
        return res

    title = meta.get("og:title") or ""
    name = title.split("|")[0].strip() or None
    res.facts.append(
        CollectorFact(
            kind="account.profile",
            value={"platform": "vk", "handle": handle, "url": str(resp.url), "name": name},
            source_url=str(resp.url),
            confidence=0.85,
        )
    )
    if meta.get("og:image"):
        res.facts.append(
            CollectorFact(
                kind="photo.avatar",
                value={"platform": "vk", "handle": handle, "url": meta["og:image"]},
                source_url=str(resp.url),
                confidence=0.8,
            )
        )
    desc = meta.get("og:description") or meta.get("description")
    if desc:
        res.facts.append(
            CollectorFact(
                kind="profile.bio",
                value={"text": desc[:2000]},
                source_url=str(resp.url),
                confidence=0.75,
            )
        )
    if not (name or desc):
        res.warnings.append("vk_profile: публичные метаданные пусты (закрытый профиль?)")
    return res


@collector("vk_profile")
def run(job: CollectorInput) -> CollectorResult:
    url, handle = canonicalize(job.value)
    if not handle or handle.lower() in ("wall", "photo", "videos", "event", "club"):
        res = CollectorResult(collector="vk_profile")
        res.warnings.append(f"vk_profile: не удалось выделить handle из «{job.value}»")
        res.ok = False
        return res

    if config.VK_TOKEN:
        try:
            result = _run_api(job, url, handle)
            if result is not None:
                return result
        except Exception as exc:  # noqa: BLE001 — любая ошибка API → graceful fallback
            fallback = _run_scrape(job, url, handle)
            fallback.warnings.insert(0, f"vk_profile: VK API не сработал ({exc}), fallback на scrape")
            return fallback

    return _run_scrape(job, url, handle)
