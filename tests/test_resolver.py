"""Тесты Этапа 3: entity resolution, граф, очередь проверки.

Запуск: .venv/bin/python tests/test_resolver.py
Проверяем: мердж с переносом, автомердж по нормализованному имени,
скоринг с прозрачными сигналами, связи графа, очередь face/pHash-проверок
и approve→merge (human-in-the-loop), идемпотентность.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os

os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite+pysqlite:///./test_resolver.db")

from core.db import init_db, session
from core.models import (
    Account,
    AuditLog,
    Case,
    Fact,
    Link,
    Person,
    ReviewItem,
    SearchJob,
)
from core.normalize import payload_hash
from core.resolver import (
    collect_case_hints,
    decide_review,
    graph_payload,
    merge_persons,
    resolve_case,
    score_person,
)

DB = ROOT / "test_resolver.db"
for p in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
    if p.exists():
        p.unlink()


def setup():
    from core import db

    # закрываем пул соединений: иначе unlink файла оставляет «протухшие» хендлы
    if db._engine is not None:
        db._engine.dispose()
        db._engine = None
        db._SessionFactory = None
    if DB.exists():
        DB.unlink()
    init_db()


def make_case(s, name="Иванов Иван"):
    case = Case(name=name, legal_basis="тест")
    s.add(case)
    s.flush()
    return case


def fact(case_id, kind, value, person_id=None, source_url="x", confidence=0.8):
    """Факт с корректным payload_hash (uq case+kind+hash)."""
    return Fact(
        job_id=0,
        case_id=case_id,
        person_id=person_id,
        kind=kind,
        value=value,
        source_url=source_url,
        confidence=confidence,
        payload_hash=payload_hash(kind, value),
    )


def test_merge_repoints_everything():
    setup()
    with session() as s:
        case = make_case(s)
        keep = Person(case_id=case.id, display_name="Иванов Иван", meta={})
        dup = Person(case_id=case.id, display_name="иванов  иван ", meta={"x": 1})
        acc = Account(
            case_id=case.id, person_id=None, platform="vk",
            handle="id1", url="https://vk.com/id1", confidence=0.9,
        )
        f = fact(case.id, "account.profile", {"name": "Иванов Иван"},
                 source_url="https://vk.com/id1")
        s.add_all([keep, dup, acc, f])
        s.flush()
        acc.person_id = dup.id
        f.person_id = dup.id
        link = Link(
            case_id=case.id, src_person_id=dup.id, dst_person_id=keep.id,
            kind="friend", dst_platform="vk", dst_handle="id2",
        )
        s.add(link)
        s.flush()

        merge_persons(s, keep, dup)
        assert s.get(Person, dup.id) is None
        assert s.query(Account).filter_by(person_id=keep.id).count() == 1
        assert s.query(Fact).filter_by(person_id=keep.id).count() == 1
        assert s.query(Link).filter_by(src_person_id=keep.id).count() == 1
        assert s.query(Link).filter_by(dst_person_id=keep.id).count() == 1
        # конфликт уникальности аккаунта не роняет мердж
        assert s.query(Account).count() == 1

        # у dst пустое имя-заглушка → принимает настоящее имя src
        keep.display_name = ""
        holder = Person(case_id=case.id, display_name="Петров Пётр")
        s.add(holder)
        s.flush()
        merge_persons(s, keep, holder)
        assert keep.display_name == "Петров Пётр"
        assert s.query(Person).count() == 1
    print("ok: merge_repoints_everything")


def test_auto_merge_and_scored():
    setup()
    with session() as s:
        case = make_case(s, "Пётр Петров")
        a = Person(case_id=case.id, display_name="Пётр Петров")
        b = Person(case_id=case.id, display_name="петр  петров")
        acc = Account(
            case_id=case.id, person_id=None, platform="vk",
            handle="id7", url="https://vk.com/id7", confidence=0.9,
        )
        job = SearchJob(
            case_id=case.id, input_type="name", input_value="Пётр Петров",
            hints={"full_name": "Пётр Петров", "city": "Тула"}, status="done",
        )
        f_name = fact(case.id, "account.profile", {"name": "Петров Петр"})
        s.add_all([a, b, acc, job, f_name])
        s.flush()
        acc.person_id = b.id
        f_name.person_id = b.id
        s.flush()
        s.commit()  # внутренние сессии резолвера должны видеть данные

        res = resolve_case(case.id)
        s.expire_all()  # внешняя сессия: сбросить identity map
        assert res["merged"] >= 1, res
        people = s.query(Person).filter_by(case_id=case.id).all()
        assert len(people) == 1, [p.display_name for p in people]
        assert res["scored"] >= 1, res
        sig = people[0].meta.get("signals") or {}
        assert "name_ratio" in sig and sig["name_ratio"] >= 0.9, sig
        assert sig.get("has_account") is True, sig
        assert 0.3 <= people[0].confidence <= 0.95, people[0].confidence
    print("ok: auto_merge_and_scored")


def test_collect_hints_and_score_person():
    setup()
    with session() as s:
        case = make_case(s, "Сергей Сидоров")
        job = SearchJob(
            case_id=case.id, input_type="name", input_value="С.С.",
            hints={"full_name": "Сергей Сидоров", "university": "НГУ",
                   "city": "Новосибирск", "age": 29},
            status="done",
        )
        job2 = SearchJob(
            case_id=case.id, input_type="username", input_value="sidorov",
            hints={"city": "Новосибирск"}, status="done",
        )
        s.add_all([job, job2])
        s.flush()
        hints = collect_case_hints(s, case.id)
        assert hints["university"] == "НГУ"
        assert hints["city"] == "Новосибирск"
        assert hints["age"] == 29

        p = Person(case_id=case.id, display_name="Сергей Сидоров", confidence=0.5)
        acc = Account(
            case_id=case.id, person_id=None, platform="vk",
            handle="id5", url="https://vk.com/id5", confidence=0.9,
        )
        f_prof = fact(case.id, "account.profile", {"name": "Сергей Сидоров"})
        f_demo = fact(
            case.id, "profile.demo",
            {"age": 30, "city": "Новосибирск",
             "universities": [{"name": "НГУ"}]},
            confidence=0.9,
        )
        s.add_all([p, acc, f_prof, f_demo])
        s.flush()
        acc.person_id = p.id
        f_prof.person_id = p.id
        f_demo.person_id = p.id
        s.flush()

        conf, signals = score_person(p, hints, [f_prof, f_demo])
        assert signals["has_account"] is True, signals
        assert signals["name_ratio"] >= 0.9, signals
        assert signals["city"] == "Новосибирск", signals
        assert "university" in signals, signals
        assert signals["age"] == 30, signals
        assert 0.9 <= conf <= 0.95, conf  # сумма перевышает cap → 0.95
        # без хинтов — ничего не скорим
        conf2, signals2 = score_person(p, {}, [f_prof])
        assert signals2 == {} and conf2 == p.confidence
    print("ok: collect_hints_and_score_person")


def test_links_and_graph():
    setup()
    with session() as s:
        case = make_case(s, "Вася Пупкин")
        p1 = Person(case_id=case.id, display_name="Вася Пупкин")
        p2 = Person(case_id=case.id, display_name="Сосед Петров")
        a2 = Account(
            case_id=case.id, person_id=None, platform="vk",
            handle="id2", url="https://vk.com/id2", confidence=0.9,
        )
        f = fact(
            case.id, "graph.friend",
            {"platform": "vk", "handle": "id2", "friend_handle": "id2",
             "name": "Сосед Петров", "url": "https://vk.com/id2"},
            confidence=0.7,
        )
        s.add_all([p1, p2, a2, f])
        s.flush()
        f.person_id = p1.id
        a2.person_id = p2.id
        s.flush()
        s.commit()

        res = resolve_case(case.id)
        s.expire_all()  # внешняя сессия: сбросить identity map
        assert res["links"] >= 1, res
        link = s.query(Link).one()
        assert link.src_person_id == p1.id
        assert link.dst_person_id == p2.id, "друг разрешён через Account"

        g = graph_payload(case.id)
        assert len(g["nodes"]) == 2  # оба — персоны, без «висячих» handle-узлов
        assert len(g["edges"]) == 1
        assert {n["type"] for n in g["nodes"]} == {"person"}
        assert g["edges"][0]["src"] == f"p{p1.id}"
        assert g["edges"][0]["dst"] == f"p{p2.id}"
        s.commit()

        # повторный проход не дублирует
        res2 = resolve_case(case.id)
        s.expire_all()  # внешняя сессия: сбросить identity map
        assert res2["links"] == 0, res2
        assert s.query(Link).count() == 1
    print("ok: links_and_graph")


def test_reviews_approve_merges():
    setup()
    with session() as s:
        case = make_case(s, "Два Лица")
        a = Person(case_id=case.id, display_name="Лицо А")
        b = Person(case_id=case.id, display_name="Лицо Б")
        # аватарки обоих (нужны для map url→person)
        av_a = fact(case.id, "photo.avatar", {"url": "https://img/a.jpg"},
                    source_url="https://vk.com/a", confidence=0.9)
        av_b = fact(case.id, "photo.avatar", {"url": "https://img/b.jpg"},
                    source_url="https://vk.com/b", confidence=0.9)
        # сильный face-матч A↔B и точный pHash
        face = fact(case.id, "photo.face_match",
                    {"target": "https://img/b.jpg", "verdict": "strong",
                     "cosine": 0.93, "method": "sface"},
                    source_url="https://vk.com/a", confidence=0.93)
        phash = fact(case.id, "photo.match",
                     {"target": "https://img/b.jpg", "distance": 0, "method": "phash"},
                     source_url="https://vk.com/a", confidence=0.9)
        # слабый матч — НЕ должен попадать в очередь
        weak = fact(case.id, "photo.face_match",
                    {"target": "https://img/b.jpg", "verdict": "possible",
                     "cosine": 0.71, "method": "sface"},
                    source_url="https://vk.com/a", confidence=0.6)
        s.add_all([a, b, av_a, av_b, face, phash, weak])
        s.flush()
        av_a.person_id = a.id
        av_b.person_id = b.id
        face.person_id = a.id
        phash.person_id = a.id
        weak.person_id = a.id
        s.flush()
        s.commit()

        res = resolve_case(case.id)
        s.expire_all()  # внешняя сессия: сбросить identity map
        assert res["reviews"] == 2, res
        kinds = {r.kind for r in s.query(ReviewItem).all()}
        assert kinds == {"face_link", "photo_link"}, kinds
        assert s.query(ReviewItem).filter_by(status="pending").count() == 2
        s.commit()

        # идемпотентность: второй проход не плодит очередь
        res2 = resolve_case(case.id)
        s.expire_all()  # внешняя сессия: сбросить identity map
        assert res2["reviews"] == 0, res2
        assert s.query(ReviewItem).count() == 2

        face_item = s.query(ReviewItem).filter_by(kind="face_link").one()
        assert face_item.payload["src_person_id"] == a.id
        assert face_item.payload["dst_person_id"] == b.id
        s.commit()

        out = decide_review(face_item.id, "approve")
        assert out["status"] == "approved"
        assert s.query(Person).count() == 1, "approve сшил два лица"
        left = s.query(ReviewItem).filter_by(kind="photo_link").one()
        assert left.status == "pending"
        s.commit()

        # повторное решение запрещено
        try:
            decide_review(face_item.id, "approve")
            raise AssertionError("двойное решение должно падать")
        except ValueError:
            pass
        s.commit()

        decide_review(left.id, "reject")
        assert s.query(ReviewItem).filter_by(status="rejected").count() == 1
        assert s.query(Person).count() == 1, "reject ничего не трогает"
        s.commit()

        # несуществующий id
        try:
            decide_review(999999, "approve")
            raise AssertionError("404-кейс")
        except LookupError:
            pass
    print("ok: reviews_approve_merges")


def test_resolve_idempotent():
    setup()
    with session() as s:
        case = make_case(s, "Идиом")
        s.add(Person(case_id=case.id, display_name="Иван Иванов"))
        s.flush()
        s.commit()
        r1 = resolve_case(case.id)
        s.expire_all()  # внешняя сессия: сбросить identity map
        s.commit()
        r2 = resolve_case(case.id)
        s.expire_all()  # внешняя сессия: сбросить identity map
        assert r1["merged"] == r2["merged"] == 0
        assert s.query(Link).count() == 0
        assert s.query(ReviewItem).count() == 0
        # аудит фиксирует прогоны
        assert s.query(AuditLog).filter_by(action="resolver.run").count() == 2
    print("ok: resolve_idempotent")


if __name__ == "__main__":
    test_merge_repoints_everything()
    test_auto_merge_and_scored()
    test_collect_hints_and_score_person()
    test_links_and_graph()
    test_reviews_approve_merges()
    test_resolve_idempotent()
    print("ALL OK (6): Этап 3 — resolver/граф/очередь проверки")
