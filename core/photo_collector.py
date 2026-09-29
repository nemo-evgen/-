"""Фото-коллектор (Этап 2): EXIF → pHash → локальная face-верификация → матчи.

Вход:  upload://<file> (через POST /api/cases/{id}/photos)  или  https://… картинка.

Что делает (всё локально, без внешних face-сервисов):
  1. EXIF  → photo.exif        (камера, дата, GPS → координаты)
  2. pHash → Photo-строка      (индекс кейса для дедупликации)
  3. если у фото публичный URL → photo.reverse_link (Яндекс/Lens/Bing, кликабельно)
  4. сравчивает фото со всеми фото кейса (аватарки VK/TG и т.д.):
     pHash  Hamming ≤ PHASH_MAX_DISTANCE      → photo.match
     face   cosine ≥ FACE_POSSIBLE            → photo.face_match (вердикт)
Скачивание аватарок кешируется в таблице photos — повторно не грузит.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from urllib.parse import quote_plus

import requests
from PIL import Image

from core import config, faceverify
from core.collector_base import collector
from core.contract import CollectorFact, CollectorInput, CollectorResult
from core.db import session
from core.imaging import cosine, extract_exif, hamming, open_image, phash, phash_hex
from core.models import Fact, Photo


def _resolve(value: str) -> tuple[Path | None, str | None, str | None, bytes | None]:
    """→ (local_path, public_url, error, raw_bytes)."""
    v = (value or "").strip()
    if v.startswith("upload://"):
        name = v[len("upload://"):]
        base = Path(config.UPLOAD_DIR).resolve()
        path = (base / name).resolve()
        if base not in path.parents and path != base:
            return None, None, f"недопустимое имя файла: {name}", None
        if not path.is_file():
            return None, None, f"файл не найден: {name}", None
        return path, None, None, None
    if v.startswith("http://") or v.startswith("https://"):
        try:
            resp = requests.get(
                v,
                timeout=config.PHOTO_DOWNLOAD_TIMEOUT,
                headers={"User-Agent": config.HTTP_USER_AGENT},
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            return None, None, f"не скачать изображение: {exc.__class__.__name__}", None
        data = resp.content
        tmp = tempfile.NamedTemporaryFile(suffix=".img", delete=False)
        tmp.write(data)
        tmp.close()
        return Path(tmp.name), v, None, data
    return None, None, f"непонятный вход (нужен upload:// или https://): {v[:80]}", None


def _reverse_link_facts(public_url: str) -> list[CollectorFact]:
    links = {
        "yandex": f"https://yandex.ru/images/search?rpt=imageview&url={quote_plus(public_url)}",
        "google_lens": f"https://lens.google.com/uploadbyurl?url={quote_plus(public_url)}",
        "bing": f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{quote_plus(public_url)}",
    }
    return [
        CollectorFact(
            kind="photo.reverse_link",
            value={"engine": engine, "link": link},
            source_url=public_url,
            confidence=0.4,
        )
        for engine, link in links.items()
    ]


def _fetch_avatars(case_id: int, job_id: int, urls: list[str], res: CollectorResult) -> int:
    """Скачивает новые аватарки (не закешированные) → Photo-строки. → сколько обработано."""
    added = 0
    failed = 0
    with session() as s:
        known = {
            row.ref
            for row in s.query(Photo).filter(Photo.case_id == case_id).all()
        }
    for url in urls:
        if url in known:
            continue
        if added >= config.PHOTO_TARGET_LIMIT:
            res.warnings.append(f"photo: лимит аватарок {config.PHOTO_TARGET_LIMIT} исчерпан")
            break
        try:
            resp = requests.get(
                url,
                timeout=config.PHOTO_DOWNLOAD_TIMEOUT,
                headers={"User-Agent": config.HTTP_USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.content
            img = open_image(data)
            h = phash_hex(phash(img))
            face = faceverify.embed_image(data)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        try:
            with session() as s:
                exists = s.query(Photo).filter(Photo.case_id == case_id, Photo.ref == url).first()
                if not exists:
                    s.add(
                        Photo(
                            case_id=case_id,
                            job_id=job_id,
                            kind="avatar",
                            ref=url,
                            source_url=url,
                            phash=h,
                            face_embedding=face,
                        )
                    )
                    added += 1
        except Exception:  # noqa: BLE001 — race с уникальным constraint и т.п.
            pass
    if failed:
        res.warnings.append(f"photo: не скачалось/не открылось аватарок: {failed}")
    return added


@collector("photo")
def run(job: CollectorInput) -> CollectorResult:
    res = CollectorResult(collector="photo")

    local, public_url, err, raw = _resolve(job.value)
    if err:
        res.warnings.append(f"photo: {err}")
        res.ok = False
        return res
    assert local is not None

    raw_bytes = raw if raw is not None else local.read_bytes()
    filename = local.name if job.value.startswith("upload://") else (public_url or "")
    upload_fact = CollectorFact(
        kind="photo.upload",
        value={
            "ref": job.value,
            "filename": filename,
            "bytes": len(raw_bytes),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
        source_url=public_url,
        confidence=0.9,
    )
    res.facts.append(upload_fact)

    # 1) EXIF
    exif = extract_exif(str(local))
    if exif:
        res.facts.append(
            CollectorFact(
                kind="photo.exif",
                value=exif,
                source_url=public_url or job.value,
                confidence=0.85,
            )
        )
    else:
        res.warnings.append("photo: EXIF отсутствует (соцсети обычно чистят метаданные)")

    # 2) pHash запроса
    try:
        img = open_image(raw_bytes)
        query_hash = phash(img)
    except Exception as exc:  # noqa: BLE001
        res.warnings.append(f"photo: не открыть изображение ({exc})")
        res.ok = False
        return res

    # 3) face embedding запроса (лениво; без моделей — просто None)
    query_face = faceverify.embed_image(raw_bytes)
    if query_face is None:
        reason = faceverify._models_state.get("reason") or "лицо не найдено / cv2 нет"
        res.warnings.append(f"photo: face-верификация недоступна ({reason})")

    with session() as s:
        existing = s.query(Photo).filter(
            Photo.case_id == job.case_id, Photo.ref == job.value
        ).first()
        if existing:
            existing.phash = phash_hex(query_hash)
            existing.face_embedding = query_face
            existing.job_id = job.job_id
        else:
            s.add(
                Photo(
                    case_id=job.case_id,
                    job_id=job.job_id,
                    kind="query",
                    ref=job.value,
                    source_url=public_url,
                    phash=phash_hex(query_hash),
                    face_embedding=query_face,
                )
            )

    # 4) обратные ссылки (только для публичного URL)
    if public_url:
        res.facts.extend(_reverse_link_facts(public_url))
    else:
        res.warnings.append(
            "photo: для обратного поиска нужна публичная ссылка (загрузка сохранит только индексы)"
        )

    # 5) цели сравнения: аватарки из фактов кейса (кашируем в photos)
    with session() as s:
        avatar_facts = (
            s.query(Fact)
            .filter(Fact.case_id == job.case_id, Fact.kind == "photo.avatar")
            .all()
        )
    avatar_urls = []
    seen: set[str] = set()
    for f in avatar_facts:
        u = (f.value or {}).get("url")
        if u and u not in seen:
            seen.add(u)
            avatar_urls.append(u)
    if avatar_urls:
        _fetch_avatars(job.case_id, job.job_id, avatar_urls, res)

    with session() as s:
        targets = (
            s.query(Photo)
            .filter(Photo.case_id == job.case_id, Photo.ref != job.value)
            .all()
        )

    if not targets:
        res.warnings.append("photo: в кейсе нет других фото/аватарок для сравнения")
        return res

    # 6) сравнение
    matches = face_matches = 0
    for t in targets:
        label = t.source_url or t.ref
        if t.phash:
            dist = hamming(int(t.phash, 16), query_hash)
            if dist <= config.PHASH_MAX_DISTANCE:
                matches += 1
                res.facts.append(
                    CollectorFact(
                        kind="photo.match",
                        value={
                            "target": label,
                            "target_kind": t.kind,
                            "method": "phash",
                            "distance": dist,
                        },
                        source_url=public_url or job.value,
                        confidence=0.9 if dist == 0 else 0.7,
                    )
                )
        if t.face_embedding and query_face:
            c = cosine(query_face, t.face_embedding)
            if c >= config.FACE_POSSIBLE:
                face_matches += 1
                verdict = "strong" if c >= config.FACE_STRONG else "possible"
                res.facts.append(
                    CollectorFact(
                        kind="photo.face_match",
                        value={
                            "target": label,
                            "target_kind": t.kind,
                            "method": "sface",
                            "cosine": round(c, 4),
                            "verdict": verdict,
                        },
                        source_url=public_url or job.value,
                        confidence=round(min(0.95, c), 2),
                    )
                )

    res.warnings.append(
        f"photo: сравнено с {len(targets)} фото → "
        f"phash-совпадений: {matches}, face-совпадений: {face_matches}"
    )
    return res
