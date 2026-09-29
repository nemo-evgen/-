"""Тесты Этапа 2b: снимки (storage) + полнотекстовый поиск (search)."""
import json
import json as _json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "worker"))

_tmp = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/storage_test.db")
os.environ.setdefault("UPLOAD_DIR", f"{_tmp}/uploads")
os.environ.setdefault("SNAPSHOT_DIR", f"{_tmp}/snapshots")
os.environ.setdefault("MODELS_DIR", f"{_tmp}/models")
os.environ.pop("OPENSEARCH_URL", None)   # по умолчанию — SQL-fallback
os.environ.pop("MINIO_ENDPOINT", None)   # по умолчанию — file-режим

from core import config  # noqa: E402
from core.storage import open_snapshot, put_snapshot  # noqa: E402


# ---------------- storage (file-режим) ----------------

def test_snapshot_roundtrip():
    ref = put_snapshot(1, "vk_page", "<html>привет</html>".encode())
    assert ref and ref.startswith("file://"), ref
    got = open_snapshot(ref)
    assert got is not None and "привет".encode() in got[0]
    assert got[1] == "text/html"
    # json-снимок → правильный content-type
    ref2 = put_snapshot(1, "vk_api_user", json.dumps({"id": 1}).encode(), suffix=".json")
    assert open_snapshot(ref2)[1] == "application/json"
    print("test_snapshot_roundtrip OK", ref)


def test_snapshot_traversal_blocked():
    ref = "file://../../etc/passwd"
    assert open_snapshot(ref) is None
    assert open_snapshot("file://missing/nope.html") is None
    print("test_snapshot_traversal_blocked OK")


def test_snapshot_too_big():
    from core import config as cfg

    old = cfg.SNAPSHOT_LIMIT_BYTES
    cfg.SNAPSHOT_LIMIT_BYTES = 10
    try:
        assert put_snapshot(1, "big", b"x" * 100) is None
    finally:
        cfg.SNAPSHOT_LIMIT_BYTES = old
    print("test_snapshot_too_big OK")


# ---------------- артефакты в коллекторах ----------------

def test_vk_scrape_attaches_snapshot():
    from core.contract import CollectorInput
    from core.vk_collector import run

    html = '<meta property="og:title" content="Иван Тест | VK">'
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.url = "https://vk.com/ivantest"
    resp.text = html
    with patch("core.vk_collector.requests.get", return_value=resp):
        res = run(CollectorInput(case_id=1, job_id=1, input_type="vk", value="ivantest"))
    prof = next(f for f in res.facts if f.kind == "account.profile")
    assert prof.artifacts and prof.artifacts[0].startswith("file://"), prof.artifacts
    assert open_snapshot(prof.artifacts[0]) is not None
    print("test_vk_scrape_attaches_snapshot OK")


def test_tg_attaches_snapshots():
    from core.contract import CollectorInput
    from core.tg_collector import run

    def fake_get(url, **kw):
        m = MagicMock()
        m.raise_for_status = lambda: None
        if "/s/" in url:
            m.status_code, m.text = 200, (
                '<div class="tgme_widget_message" data-post="https://t.me/chan/1">'
                '<div class="tgme_widget_message_text">пост</div>'
                '<time datetime="2026-01-01T00:00:00+00:00"></time></div>'
            )
        else:
            m.status_code, m.text = 200, (
                '<meta property="og:title" content="Канал">'
                '<meta property="og:description" content="описание">'
            )
        return m

    with patch("core.tg_collector.requests.get", side_effect=fake_get):
        res = run(CollectorInput(case_id=1, job_id=1, input_type="telegram", value="chan"))
    prof = next(f for f in res.facts if f.kind == "account.profile")
    assert len(prof.artifacts) == 2, prof.artifacts  # профиль + лента
    for a in prof.artifacts:
        assert open_snapshot(a) is not None
    print("test_tg_attaches_snapshots OK")


# ---------------- поиск: SQL-fallback ----------------

def test_search_sql_fallback():
    from core.db import init_db, session
    from core.models import Case, Fact
    from core.search import search

    init_db()
    with session() as s:
        c1 = Case(name="кейс1", legal_basis="t")
        c2 = Case(name="кейс2", legal_basis="t")
        s.add_all([c1, c2])
        s.flush()
        s.add(Fact(case_id=c1.id, kind="profile.demo",
                   value={"city": "Москва", "university": "МГУ"},
                   source_url="https://vk.com/a", payload_hash="s1"))
        s.add(Fact(case_id=c2.id, kind="profile.demo",
                   value={"city": "Казань"}, source_url="https://vk.com/b", payload_hash="s2"))
        s.flush()
        c1_id = c1.id

    out = search("Москва")
    assert out["backend"] == "sql", out
    assert out["results"] and out["results"][0]["value"]["city"] == "Москва", out

    # фильтр по кейсу: кейс2 не содержит Москвы
    out2 = search("Москва", case_id=c1_id + 100)  # несуществующий кейс
    assert out2["results"] == [], out2

    # по source_url
    out3 = search("vk.com/b")
    assert out3["results"] and out3["results"][0]["case_id"] != c1_id, out3
    print("test_search_sql_fallback OK")


# ---------------- поиск: OpenScience-путь с моком ----------------

def test_opensearch_index_and_search_mocked():
    config.OPENSEARCH_URL = "http://opensearch:9200"
    try:
        from core.db import init_db, session
        from core.models import Fact
        from core.search import _search_os, index_facts

        init_db()
        with session() as s:
            f = Fact(case_id=1, kind="profile.demo", value={"city": "Тверь"},
                     source_url="https://x", payload_hash="os1")
            s.add(f)
            s.flush()
            fid = f.id

        captured = {}

        def fake_post(url, params=None, data=None, json=None, **kw):
            m = MagicMock()
            m.raise_for_status = lambda: None
            captured["url"] = url
            captured["params"] = params
            captured["data"] = data
            captured["json"] = json
            m.json = lambda: {"errors": False, "hits": {"hits": [
                {"_id": str(fid), "_score": 2.5,
                 "_source": {"case_id": 1, "kind": "profile.demo", "confidence": 0.9,
                             "source_url": "https://x", "text": "Тверь",
                             "value_json": _json.dumps({"city": "Тверь"}, ensure_ascii=False)}}
            ]}}
            return m

        with session() as s:
            facts = s.query(Fact).filter(Fact.id == fid).all()
        with patch("core.search.requests.post", side_effect=fake_post):
            n = index_facts(facts)
            assert n == 1
            assert captured["url"].endswith("/_bulk")
            ndjson = captured["data"].decode()
            assert '"index"' in ndjson and "Тверь" in ndjson

            out = _search_os("Тверь", None, 10)
        assert out and out[0]["fact_id"] == fid and out[0]["value"]["city"] == "Тверь", out
        assert out[0]["score"] == 2.5
        print("test_opensearch_index_and_search_mocked OK")
    finally:
        config.OPENSEARCH_URL = ""


def test_search_backend_selection():
    """Выключенный OS → sql; включенный с упавшей сетью → деградация на sql."""
    from core.search import search

    out = search("что-нибудь")
    assert out["backend"] in ("sql", "none")

    config.OPENSEARCH_URL = "http://opensearch:9200"
    try:
        with patch("core.search.requests.post", side_effect=ConnectionError("down")):
            out2 = search("что-нибудь")
        assert out2["backend"] == "sql", out2  # graceful degradation
    finally:
        config.OPENSEARCH_URL = ""
    print("test_search_backend_selection OK")


if __name__ == "__main__":
    test_snapshot_roundtrip()
    test_snapshot_traversal_blocked()
    test_snapshot_too_big()
    test_vk_scrape_attaches_snapshot()
    test_tg_attaches_snapshots()
    test_search_sql_fallback()
    test_opensearch_index_and_search_mocked()
    test_search_backend_selection()
    print("ALL STORAGE/SEARCH TESTS PASSED")
