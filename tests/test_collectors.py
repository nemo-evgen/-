"""Тесты коллекторов Этапа 1.1 с моками сети (без реальных API)."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "worker"))

from core import config  # noqa: E402
from core.contract import CollectorInput  # noqa: E402


def _input(value: str, input_type: str = "vk", hints: dict | None = None) -> CollectorInput:
    return CollectorInput(case_id=1, job_id=1, input_type=input_type, value=value, hints=hints or {})


# ---------------- VK API ----------------

VK_USERS = [{
    "id": 1, "first_name": "Павел", "last_name": "Дуров", "domain": "durov",
    "bdate": "10.03.1984", "city": {"id": 1, "title": "Санкт-Петербург"},
    "universities": [{"name": "СПбГУ", "faculty": "ФПМ", "graduation_year": 2006}],
    "schools": [{"name": "Лицей 239", "year_graduated": 2001}],
    "status": "Telegram", "about": "Создатель Telegram", "photo_200": "https://vk.ru/photo.jpg",
    "counters": {"friends": 5, "groups": 12},
}]
VK_FRIENDS = {"count": 1, "items": [
    {"id": 42, "first_name": "Иван", "last_name": "Иванов", "screen_name": "ivan42"},
]}
VK_WALL = {"count": 1, "items": [
    {"id": 10, "from_id": 1, "date": 1700000000, "text": "Привет, мир!"},
    {"id": 11, "from_id": 99, "date": 1700000001, "text": "чужой репост"},
]}


def test_vk_api_path():
    config.VK_TOKEN = "test-token"
    try:
        from core.vk_collector import run

        def fake_get(url, params=None, **kw):
            method = url.rsplit("/", 1)[-1]
            m = MagicMock()
            m.raise_for_status = lambda: None
            if method == "users.get":
                m.json = lambda: {"response": VK_USERS}
            elif method == "friends.get":
                m.json = lambda: {"response": VK_FRIENDS}
            elif method == "wall.get":
                m.json = lambda: {"response": VK_WALL}
            else:
                raise AssertionError(f"unexpected method {method}")
            return m

        with patch("core.vk_collector.requests.get", side_effect=fake_get):
            res = run(_input("https://vk.com/durov"))

        kinds = [f.kind for f in res.facts]
        assert res.ok and "account.profile" in kinds, kinds
        assert "profile.demo" in kinds and "graph.friend" in kinds and "profile.post" in kinds
        demo = next(f for f in res.facts if f.kind == "profile.demo")
        assert demo.value["city"] == "Санкт-Петербург"
        assert demo.value["universities"][0]["name"] == "СПбГУ"
        assert demo.value["bdate"] == "10.03.1984"
        assert isinstance(demo.value["age"], int) and demo.value["age"] > 30
        posts = [f for f in res.facts if f.kind == "profile.post"]
        assert len(posts) == 1 and "Привет" in posts[0].value["text"]  # чужой отфильтрован
        prof = next(f for f in res.facts if f.kind == "account.profile")
        assert prof.value["name"] == "Павел Дуров" and prof.value["handle"] == "durov"
        print("test_vk_api_path OK")
    finally:
        config.VK_TOKEN = ""


def test_vk_api_error_falls_back():
    """Ошибка API → og:* scrape-путь."""
    config.VK_TOKEN = "test-token"
    try:
        from core.vk_collector import run

        def boom(*a, **kw):
            raise ConnectionError("nope")

        scrape_html = (
            '<meta property="og:title" content="Павел Дуров | VK">'
            '<meta property="og:image" content="https://vk.ru/a.jpg">'
        )
        m = MagicMock()
        m.raise_for_status = lambda: None
        m.url = "https://vk.com/durov"
        m.text = scrape_html

        with patch("core.vk_collector.requests.get", side_effect=boom):
            res = run(_input("durov"))
        # API упал (ConnectionError не RequestException? — ловится как Exception в scrape)
        # VK API путь: requests.get бросил ConnectionError → requests.RequestException?
        # ConnectionError из unittest-мока — не requests-исключение → должно деградировать.
        assert any(f.kind == "account.profile" for f in res.facts), res.facts
        print("test_vk_api_error_falls_back OK")
    finally:
        config.VK_TOKEN = ""


# ---------------- Telegram ----------------

TG_PROFILE_HTML = """
<html><head>
<meta property="og:title" content="Мой канал | Telegram">
<meta property="og:image" content="https://cdn44.tg/image.jpg">
<meta property="og:description" content="Описание канала">
</head></html>
"""

TG_PREVIEW_HTML = """
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="https://t.me/mychannel/1">
    <div class="tgme_widget_message_text">Первый пост про OSINT</div>
    <time datetime="2026-09-01T10:00:00+00:00"></time>
  </div>
</div>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="https://t.me/mychannel/2">
    <div class="tgme_widget_message_text">Второй пост &amp; HTML</div>
    <time datetime="2026-09-02T11:00:00+00:00"></time>
  </div>
</div>
"""


def test_tg_profile():
    from core.tg_collector import run

    def fake_get(url, **kw):
        m = MagicMock()
        m.raise_for_status = lambda: None
        if url.endswith("/s/mychannel"):
            m.status_code, m.text = 200, TG_PREVIEW_HTML
        elif url.endswith("/mychannel"):
            m.status_code, m.text = 200, TG_PROFILE_HTML
        else:
            m.status_code, m.text = 404, ""
        return m

    with patch("core.tg_collector.requests.get", side_effect=fake_get):
        res = run(_input("@mychannel", input_type="telegram"))

    kinds = [f.kind for f in res.facts]
    assert "account.profile" in kinds and kinds.count("profile.post") == 2, kinds
    prof = next(f for f in res.facts if f.kind == "account.profile")
    assert prof.value["handle"] == "mychannel" and prof.value["name"] == "Мой канал"
    posts = [f for f in res.facts if f.kind == "profile.post"]
    assert posts[0].value["url"] == "https://t.me/mychannel/1"
    assert "HTML" in posts[1].value["text"] and "&" in posts[1].value["text"]
    assert "&amp;" not in posts[1].value["text"]
    print("test_tg_profile OK")


def test_tg_user_no_preview():
    """Обычный юзер: /s/ отдаёт 404 — не фейл, только warning."""
    from core.tg_collector import run

    def fake_get(url, **kw):
        m = MagicMock()
        m.raise_for_status = lambda: None
        m.status_code = 404 if "/s/" in url else 200
        m.text = "" if m.status_code == 404 else (
            '<meta property="og:title" content="Telegram: Contact @someuser">'
        )
        return m

    with patch("core.tg_collector.requests.get", side_effect=fake_get):
        res = run(_input("t.me/someuser", input_type="telegram"))

    assert res.ok and any(f.kind == "account.profile" for f in res.facts)
    assert not any(f.kind == "profile.post" for f in res.facts)
    assert any("/s/" in w for w in res.warnings)
    print("test_tg_user_no_preview OK")


# ---------------- dorks: авто-выполнение ----------------

def test_dorks_execute_serper():
    """serper.dev — приоритетный провайдер, POST + X-API-KEY."""
    config.SERPER_API_KEY = "test-serper"
    config.SERPAPI_KEY = ""
    config.DORK_MAX_EXEC = 2
    try:
        from core.dorks_collector import run

        captured = {}

        def fake_post(url, headers=None, json=None, **kw):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            m = MagicMock()
            m.raise_for_status = lambda: None
            m.json = lambda: {"organic": [
                {"position": 1, "title": "S1", "link": "https://ex.com/1", "snippet": "сн1"},
            ]}
            return m

        with patch("core.dorks_collector.requests.post", side_effect=fake_post):
            res = run(_input("Иванов Иван", input_type="name", hints={"city": "Москва"}))

        assert captured["url"] == "https://google.serper.dev/search"
        assert captured["headers"]["X-API-KEY"] == "test-serper"
        assert captured["json"]["q"]
        results = [f for f in res.facts if f.kind == "search.result"]
        assert len(results) == 2, len(results)  # DORK_MAX_EXEC=2
        assert results[0].source_url == "https://ex.com/1"
        assert any("serper" in w for w in res.warnings)
        print("test_dorks_execute_serper OK")
    finally:
        config.SERPER_API_KEY = ""
        config.DORK_MAX_EXEC = 10


def test_dorks_execute_serpapi():
    config.SERPER_API_KEY = ""  # иначе приоритет у serper
    config.SERPAPI_KEY = "test-key"
    config.DORK_MAX_EXEC = 3
    try:
        from core.dorks_collector import run

        def fake_get(url, params=None, **kw):
            m = MagicMock()
            m.raise_for_status = lambda: None
            m.json = lambda: {"organic_results": [
                {"position": 1, "title": "Найдено", "link": "https://example.com/x", "snippet": "сниппет"},
                {"position": 2, "title": "Ещё", "link": "https://example.com/y", "snippet": "сн"},
            ]}
            return m

        with patch("core.dorks_collector.requests.get", side_effect=fake_get):
            res = run(_input("Иванов Иван", input_type="name",
                             hints={"city": "Москва", "university": "МГУ"}))

        queries = [f for f in res.facts if f.kind == "search.query"]
        results = [f for f in res.facts if f.kind == "search.result"]
        assert len(queries) > 10
        assert len(results) == 3 * 2, len(results)  # DORK_MAX_EXEC=3 запроса × 2 результата
        assert results[0].source_url == "https://example.com/x"
        assert any("авто-выполнение" in w for w in res.warnings)
        print("test_dorks_execute_serpapi OK")
    finally:
        config.SERPAPI_KEY = ""
        config.DORK_MAX_EXEC = 10


def test_dorks_no_key_no_exec():
    """Без ключей — только ссылки, ни одного search.result."""
    config.SERPER_API_KEY = ""
    config.SERPAPI_KEY = ""
    from core.dorks_collector import run

    res = run(_input("Иванов Иван", input_type="name", hints={"city": "Москва"}))
    assert any(f.kind == "search.query" for f in res.facts)
    assert not any(f.kind == "search.result" for f in res.facts)
    print("test_dorks_no_key_no_exec OK")


if __name__ == "__main__":
    test_vk_api_path()
    test_vk_api_error_falls_back()
    test_tg_profile()
    test_tg_user_no_preview()
    test_dorks_execute_serper()
    test_dorks_execute_serpapi()
    test_dorks_no_key_no_exec()
    print("ALL COLLECTOR TESTS PASSED")
