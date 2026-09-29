"""Username-коллектор: Maigret (3000+ сайтов) + пассивный fallback.

Maigret запускается как CLI-подпроцесс в том же контейнере (пакет в requirements).
Если бинарник недоступен / таймаут — откатываемся на генерацию dorks по нику,
чтобы джоба всегда давала исследуемый результат.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from core import config
from core.collector_base import collector
from core.contract import CollectorFact, CollectorInput, CollectorResult


def _fallback_dorks(handle: str) -> list[CollectorFact]:
    from core.dorks_collector import build_queries, NICK_TEMPLATES

    facts: list[CollectorFact] = []
    for engine, q, url in build_queries({"name": handle, "nick": handle}, NICK_TEMPLATES):
        facts.append(
            CollectorFact(
                kind="search.query",
                value={"engine": engine, "query": q, "url": url},
                source_url=url,
                confidence=0.4,
            )
        )
    return facts


def _extract_found(report: dict) -> list[dict]:
    """Аккуратно вытаскиваем найденные аккаунты из отчёта maigret (схема может меняться)."""
    found: list[dict] = []
    sites = report.get("sites") or {}
    if not isinstance(sites, dict):
        return found
    for site, info in sites.items():
        if not isinstance(info, dict):
            continue
        status = str(info.get("status") or "").lower()
        url = info.get("url_user") or info.get("url") or info.get("link")
        if status in ("claimed", "yes", "true") or (url and status not in ("available", "no")):
            found.append({"platform": site, "url": url, "status": info.get("status")})
    return found


@collector("username")
def run(job: CollectorInput) -> CollectorResult:
    res = CollectorResult(collector="username")
    handle = (job.value or "").strip().lstrip("@")
    if not handle:
        res.warnings.append("username: пустой ник")
        res.ok = False
        return res

    res.facts.extend(_fallback_dorks(handle))  # всегда: пассивные запросы

    binary = shutil.which("maigret")
    if not binary:
        res.warnings.append("username: maigret не установлен в образе — только dorks")
        return res

    with tempfile.TemporaryDirectory() as tmp:
        report_path = os.path.join(tmp, "maigret.json")
        cmd = [binary, handle, "--json", report_path]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config.MAIGRET_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            res.warnings.append(
                f"username: maigret превысил таймаут {config.MAIGRET_TIMEOUT}s (результат неполный)"
            )
            return res
        except OSError as exc:
            res.warnings.append(f"username: не удалось запустить maigret ({exc})")
            return res

        if not os.path.exists(report_path):
            tail = (proc.stderr or proc.stdout or "")[-500:]
            res.warnings.append(f"username: maigret не отчитался (rc={proc.returncode}): {tail}")
            return res

        try:
            with open(report_path, encoding="utf-8") as fh:
                report = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            res.warnings.append(f"username: отчёт maigret не читается ({exc})")
            return res

    found = _extract_found(report)
    for item in found:
        res.facts.append(
            CollectorFact(
                kind="account.profile",
                value={
                    "platform": str(item.get("platform") or "unknown"),
                    "handle": handle,
                    "url": item.get("url"),
                    "name": None,
                },
                source_url=item.get("url") or f"https://www.google.com/search?q={handle}",
                confidence=0.7,
            )
        )
    res.facts.append(
        CollectorFact(
            kind="username.dossier",
            value={"handle": handle, "sites_found": len(found), "tool": "maigret"},
            source_url="https://github.com/soxoj/maigret",
            confidence=0.6,
        )
    )
    if not found:
        res.warnings.append("username: maigret не нашёл подтверждённых аккаунтов (или все закрыты)")
    return res
