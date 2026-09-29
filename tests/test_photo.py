"""Тесты фото-конвейера (Этап 2): EXIF, pHash, матчинг, face-банды, API-загрузка."""
import io
import os
import random
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "worker"))

_tmp = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/photo_test.db")
os.environ.setdefault("UPLOAD_DIR", f"{_tmp}/uploads")
os.environ.setdefault("MODELS_DIR", f"{_tmp}/models")

from PIL import Image  # noqa: E402


def make_image(w=200, h=200, seed=42, color=(30, 120, 200)) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), color)
    px = img.load()
    for x in range(0, w, 4):
        for y in range(0, h, 4):
            px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    return img


def image_bytes(img: Image.Image, fmt="JPEG", exif=None) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, exif=exif) if exif else img.save(buf, format=fmt)
    return buf.getvalue()


# ---------------- EXIF ----------------

def test_extract_exif():
    from core.imaging import extract_exif

    img = make_image()
    exif = Image.Exif()
    exif[0x010F] = "TestMake"
    exif[0x0110] = "TestModel"
    exif[0x9003] = "2024:05:12 10:00:00"  # DateTimeOriginal
    try:
        gps = exif.get_ifd(0x8825)
        gps[1] = "N"
        gps[2] = (55.0, 45.0, 0.0)   # 55°45'00"N
        gps[3] = "E"
        gps[4] = (37.0, 37.0, 0.0)   # 37°37'00"E
    except Exception:  # noqa: BLE001
        gps = None

    path = os.path.join(_tmp, "exif.jpg")
    img.save(path, exif=exif)
    out = extract_exif(path)
    assert out.get("make") == "TestMake", out
    assert out.get("model") == "TestModel", out
    assert out.get("datetime") == "2024:05:12 10:00:00", out
    if gps is not None and "gps" in out:
        # 55°45'N 37°37'E → Москва
        assert 55.7 < out["gps"]["lat"] < 55.8, out["gps"]
        assert 37.6 < out["gps"]["lon"] < 37.7, out["gps"]
    print("test_extract_exif OK", out)


def test_extract_exif_empty():
    from core.imaging import extract_exif

    path = os.path.join(_tmp, "noexif.png")
    make_image().save(path)
    assert extract_exif(path) == {}
    print("test_extract_exif_empty OK")


# ---------------- pHash ----------------

def test_phash_stability():
    from core.imaging import hamming, phash

    img = make_image(seed=7)
    h1 = phash(img)
    h2 = phash(img.resize((640, 640)))          # масштабирование
    h3 = phash(img.convert("L"))                # градации серого
    assert hamming(h1, h2) <= 6, hamming(h1, h2)
    assert hamming(h1, h3) <= 10, hamming(h1, h3)

    other = make_image(seed=99, color=(250, 10, 10))
    h4 = phash(other)
    assert hamming(h1, h4) > 10, hamming(h1, h4)
    print(f"test_phash_stability OK (resize={hamming(h1, h2)}, gray={hamming(h1, h3)}, diff={hamming(h1, h4)})")


def test_cosine_bands():
    from core.imaging import cosine
    from core import config

    a = [1.0] + [0.0] * 7
    b = [1.0] + [0.0] * 7
    c = [0.0, 1.0] + [0.0] * 6
    mixed = [0.7, 0.7] + [0.0] * 6
    assert cosine(a, b) == 1.0
    assert cosine(a, c) == 0.0
    assert cosine(a, mixed) >= config.FACE_POSSIBLE  # ~0.707 → possible/strong
    assert cosine(a, a) >= config.FACE_STRONG
    print("test_cosine_bands OK")


# ---------------- e2e: job с матчингом аватарки ----------------

def test_photo_job_match():
    from core import faceverify
    from core.db import init_db, session
    from core.models import Case, Fact, Person, SearchJob, Photo, utcnow
    from worker.runner import run_job

    init_db()
    avatar_img = make_image(seed=11, color=(200, 40, 40))

    # загрузка query-файла (чуть другой размер — phash должен совпасть)
    uploads = Path(os.environ["UPLOAD_DIR"])
    uploads.mkdir(parents=True, exist_ok=True)
    name = "a" * 32 + ".jpg"
    avatar_img.resize((320, 320)).save(uploads / name, quality=95)

    with session() as s:
        case = Case(name="photo-case", legal_basis="test")
        s.add(case)
        s.flush()
        person = Person(case_id=case.id, display_name="Тест")
        s.add(person)
        s.flush()
        s.add(Fact(
            case_id=case.id, person_id=person.id, kind="photo.avatar",
            value={"platform": "vk", "url": "https://example.com/av.jpg"},
            source_url="https://vk.com/x", payload_hash="h1", confidence=0.8,
        ))
        job = SearchJob(
            case_id=case.id, input_type="photo",
            input_value=f"upload://{name}", hints={}, status="pending",
        )
        s.add(job)
        s.flush()
        job_id, case_id = job.id, case.id

    avatar_bytes = image_bytes(avatar_img)

    class FakeResp:
        status_code = 200
        content = avatar_bytes

        def raise_for_status(self):
            return None

    with patch.object(faceverify, "_download", return_value=False):
        with patch("core.photo_collector.requests.get", return_value=FakeResp()):
            out = run_job(job_id)

    print("[photo-match]", out)
    assert out.get("status") == "done", out

    with session() as s:
        facts = s.query(Fact).filter(Fact.case_id == case_id).all()
        kinds = [f.kind for f in facts]
        assert "photo.upload" in kinds, kinds
        matches = [f for f in facts if f.kind == "photo.match"]
        assert matches, kinds
        from core import config as cfg
        assert matches[0].value["distance"] <= cfg.PHASH_MAX_DISTANCE, matches[0].value
        assert matches[0].value["target"] == "https://example.com/av.jpg"
        photos = s.query(Photo).filter(Photo.case_id == case_id).all()
        assert len(photos) == 2, [(p.kind, p.ref) for p in photos]  # query + avatar
        # повторный запуск того же джоба идемпотентен
    out2 = run_job(job_id)
    assert out2.get("skipped"), out2
    print("test_photo_job_match OK")


def test_photo_no_targets_warning():
    from core import faceverify
    from core.db import init_db, session
    from core.models import Case, Photo, SearchJob
    from worker.runner import run_job

    init_db()
    uploads = Path(os.environ["UPLOAD_DIR"])
    name = "b" * 32 + ".jpg"
    make_image(seed=5).save(uploads / name)

    with session() as s:
        case = Case(name="photo-empty", legal_basis="test")
        s.add(case)
        s.flush()
        job = SearchJob(
            case_id=case.id, input_type="photo",
            input_value=f"upload://{name}", hints={}, status="pending",
        )
        s.add(job)
        s.flush()
        job_id = job.id

    with patch.object(faceverify, "_download", return_value=False):
        out = run_job(job_id)
    print("[photo-empty]", out)
    assert out.get("status") == "done", out
    warnings = out.get("warnings") or []
    assert any("нет других фото" in w for w in warnings), warnings
    print("test_photo_no_targets_warning OK")


if __name__ == "__main__":
    test_extract_exif()
    test_extract_exif_empty()
    test_phash_stability()
    test_cosine_bands()
    test_photo_job_match()
    test_photo_no_targets_warning()
    print("ALL PHOTO TESTS PASSED")
