"""Работа с изображениями: EXIF, pHash (DCT-64), метрики сравнения.

Зависимости: Pillow + numpy (обе уже в worker/requirements).
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ExifTags

# --- EXIF ---

_IFD_EXIF = 0x8769
_IFD_GPS = 0x8825
_TAG_DATETIME_ORIGINAL = 36867
_TAG_DATETIME = 306
_TAG_MAKE = 271
_TAG_MODEL = 272
_TAG_LENS = 42036

_GPS_TAGS = {v: k for k, v in ExifTags.GPSTAGS.items()} if hasattr(ExifTags, "GPSTAGS") else {}


def _rational_to_float(value) -> float | None:
    try:
        if hasattr(value, "numerator"):
            return float(value.numerator) / float(value.denominator or 1)
        if isinstance(value, tuple) and len(value) == 2:
            return float(value[0]) / float(value[1] or 1)
        return float(value)
    except (TypeError, ZeroDivisionError, ValueError):
        return None


def _dms_to_deg(dms, ref: str | None) -> float | None:
    if not dms or not isinstance(dms, (tuple, list)) or len(dms) < 3:
        return None
    parts = [_rational_to_float(x) for x in dms[:3]]
    if any(p is None for p in parts):
        return None
    deg = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
    if ref and str(ref).upper() in ("S", "W"):
        deg = -deg
    return round(deg, 6)


def extract_exif(path: str) -> dict:
    """Ключевые EXIF: устройство, дата, GPS. Пустой dict, если EXIF нет."""
    out: dict = {}
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return out
            exif_ifd = {}
            try:
                exif_ifd = exif.get_ifd(_IFD_EXIF)
            except Exception:  # noqa: BLE001
                pass

            def pick(*ids: int):
                for i in ids:
                    for src in (exif_ifd, exif):
                        v = src.get(i)
                        if v:
                            return str(v)
                return None

            make = pick(_TAG_MAKE)
            model = pick(_TAG_MODEL)
            dt = pick(_TAG_DATETIME_ORIGINAL, _TAG_DATETIME)
            lens = pick(_TAG_LENS)
            if make:
                out["make"] = make
            if model:
                out["model"] = model
            if dt:
                out["datetime"] = dt
            if lens:
                out["lens"] = lens

            try:
                gps_ifd = exif.get_ifd(_IFD_GPS)
            except Exception:  # noqa: BLE001
                gps_ifd = {}
            if gps_ifd:
                lat = _dms_to_deg(gps_ifd.get(2), gps_ifd.get(1))
                lon = _dms_to_deg(gps_ifd.get(4), gps_ifd.get(3))
                alt = _rational_to_float(gps_ifd.get(6))
                gps: dict = {}
                if lat is not None:
                    gps["lat"] = lat
                if lon is not None:
                    gps["lon"] = lon
                if alt is not None:
                    gps["alt"] = round(alt, 1)
                if gps:
                    out["gps"] = gps
            if gps_ifd and "gps" not in out:
                out["gps_raw"] = {str(k): str(v) for k, v in list(gps_ifd.items())[:8]}
    except Exception:  # noqa: BLE001 — битое изображение
        return out
    return out


# --- pHash (DCT, 64 бита) ---

def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n).reshape(-1, 1)
    nn = np.arange(n)
    m = np.cos(np.pi * (2 * nn + 1) * k / (2 * n))
    m *= math.sqrt(2.0 / n)
    m[0] *= 1.0 / math.sqrt(2.0)
    return m


_DCT_CACHE: dict[int, np.ndarray] = {}


def _dct_cached(n: int) -> np.ndarray:
    m = _DCT_CACHE.get(n)
    if m is None:
        m = _dct_matrix(n)
        _DCT_CACHE[n] = m
    return m


def phash(img: Image.Image, hash_size: int = 8, highfreq_factor: int = 4) -> int:
    """64-битный DCT-pHash: resize N×N → полный DCT → низкочастотные hash_size²."""
    size = hash_size * highfreq_factor
    gray = img.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    arr = np.asarray(gray, dtype=np.float64)
    c = _dct_cached(size)
    dct = c @ arr @ c.T
    low = dct[:hash_size, :hash_size].flatten()
    med = float(np.median(low[1:]))      # медиана без DC
    bits = low > med
    h = 0
    for b in bits:
        h = (h << 1) | int(bool(b))
    return h


def phash_hex(h: int) -> str:
    return f"{h:016x}"


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def open_image(source) -> Image.Image:
    """Pillow-картинка из пути или bytes."""
    if isinstance(source, (bytes, bytearray)):
        import io

        return Image.open(io.BytesIO(source))
    return Image.open(source)


# --- векторы ---

def cosine(a, b) -> float:
    va = np.asarray(a, dtype=np.float64).ravel()
    vb = np.asarray(b, dtype=np.float64).ravel()
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
