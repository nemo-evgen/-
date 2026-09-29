"""Разбор OG/ description-метаданных публичных страниц (общий для коллекторов)."""
from __future__ import annotations

import re

_OG = re.compile(
    r'<meta\s+(?:property|name)=["\'](og:title|og:image|og:description|description)["\']'
    r'\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_OG_REV = re.compile(
    r'<meta\s+content=["\']([^"\']*)["\']\s+(?:property|name)=["\']'
    r'(og:title|og:image|og:description|description)["\']',
    re.IGNORECASE,
)


def parse_meta(html: str) -> dict[str, str]:
    """→ {og:title, og:image, og:description, description} (что нашлось)."""
    meta: dict[str, str] = {}
    for m in _OG.finditer(html):
        meta[m.group(1).lower()] = m.group(2).strip()
    for m in _OG_REV.finditer(html):
        meta.setdefault(m.group(2).lower(), m.group(1).strip())
    return meta


def strip_tags(html_fragment: str) -> str:
    """HTML-фрагмент → плоский текст."""
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
