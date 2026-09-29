"""Локальная face-верификация: YuNet (детект) + SFace (embedding), ONNX через OpenCV.

Модели (~1.7 МБ + ~37 МБ) скачиваются один раз в MODELS_DIR; при недоступности
сети функции возвращают None и коллектор помечает warning — пайплайн не падает.
Никаких внешних face-сервисов: вычисление только локально.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from core import config

log = logging.getLogger("faceverify")

_cv2 = None
_detector = None
_recognizer = None
_models_state: dict = {"attempted": False, "ok": False, "reason": None}


def _ensure_cv2():
    global _cv2
    if _cv2 is None:
        import cv2  # noqa: PLC0415 — лениво, чтобы не тянуть в тесты без cv2

        _cv2 = cv2
    return _cv2


def _download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1000:
        return True
    try:
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(1 << 16):
                fh.write(chunk)
        tmp.rename(dest)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("model download failed: %s (%s)", url, exc)
        return False


def _ensure_models() -> bool:
    if _models_state["attempted"]:
        return _models_state["ok"]
    _models_state["attempted"] = True
    models_dir = Path(config.MODELS_DIR)
    yunet = models_dir / "face_detection_yunet_2023mar.onnx"
    sface = models_dir / "face_recognition_sface_2021dec.onnx"
    ok = _download(config.FACE_MODEL_YUNET, yunet) and _download(config.FACE_MODEL_SFACE, sface)
    _models_state["ok"] = ok
    if not ok:
        _models_state["reason"] = "ONNX-модели недоступны (сеть) — face-сравнение отключено"
    return ok


def reset_state() -> None:
    """Для тестов."""
    global _detector, _recognizer
    _detector = None
    _recognizer = None
    _models_state.update(attempted=False, ok=False, reason=None)


def embed_image(source) -> list | None:
    """Детект лица → embedding SFace (128-d). None — лица нет / cv2/моделей нет."""
    try:
        cv2 = _ensure_cv2()
    except ImportError:
        _models_state.update(attempted=True, ok=False, reason="opencv не установлен")
        return None
    if not _ensure_models():
        return None
    models_dir = Path(config.MODELS_DIR)
    try:
        from PIL import Image
        import numpy as np

        if isinstance(source, (bytes, bytearray)):
            arr = np.frombuffer(source, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            pil = Image.open(source).convert("RGB")
            bgr = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
        if bgr is None:
            return None

        global _detector, _recognizer
        h, w = bgr.shape[:2]
        if _detector is None:
            _detector = cv2.FaceDetectorYN.create(
                str(models_dir / "face_detection_yunet_2023mar.onnx"), "", (w, h)
            )
        _detector.setInputSize((w, h))
        _, faces = _detector.detect(bgr)
        if faces is None or len(faces) == 0:
            return None
        # самый крупный кроп
        face = max(faces, key=lambda f: f[2] * f[3])
        if _recognizer is None:
            _recognizer = cv2.FaceRecognizerSF.create(
                str(models_dir / "face_recognition_sface_2021dec.onnx")
            )
        aligned = _recognizer.alignCrop(bgr, face)
        feat = _recognizer.feature(aligned)
        return [float(x) for x in feat.ravel().tolist()]
    except Exception as exc:  # noqa: BLE001
        log.warning("embed_image failed: %s", exc)
        return None


def available() -> bool:
    """True, если cv2 + модели готовы (без попытки скачать, если уже пробовали)."""
    try:
        _ensure_cv2()
    except ImportError:
        return False
    return _ensure_models()
