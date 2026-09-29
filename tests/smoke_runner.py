"""Локальная проверка раннера без Docker/Redis (sqlite + прямой вызов)."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "worker"))

from core.db import init_db, session  # noqa: E402
from core.models import AuditLog, Case, Fact, Person, SearchJob  # noqa: E402
from worker.runner import run_job  # noqa: E402


def make_job(input_type: str, value: str, hints: dict | None = None) -> int:
    with session() as s:
        case = s.execute(
            __import__("sqlalchemy").select(Case)
        ).scalars().first()
        if case is None:
            case = Case(name="тест", legal_basis="тест")
            s.add(case)
            s.flush()
        job = SearchJob(
            case_id=case.id,
            input_type=input_type,
            input_value=value,
            hints=hints or {},
            status="pending",
        )
        s.add(job)
        s.flush()
        return job.id


def main() -> int:
    init_db()
    failures = []

    # S2: VK
    jid = make_job("vk", "https://vk.com/durov")
    out = run_job(jid)
    print(f"[vk]        {out}")
    if out.get("status") != "done":
        failures.append("vk job not done")

    # S4: ФИО + подсказки
    jid = make_job(
        "name",
        "Иванов Иван Иванович",
        {"city": "Москва", "university": "МГУ", "age": "22"},
    )
    out = run_job(jid)
    print(f"[name]      {out}")
    if out.get("status") != "done" or out["summary"].get("new_facts", 0) < 5:
        failures.append("name dorks: expected >=5 facts")

    # username: maigret отсутствует → fallback dorks, но статус done
    jid = make_job("username", "john_doe_42")
    out = run_job(jid)
    print(f"[username]  {out}")
    if out.get("status") != "done":
        failures.append("username fallback failed")
    if not any("maigret" in w for w in out.get("warnings", [])):
        failures.append("expected maigret-missing warning")

    # telegram: пассивный маршрут (tg_profile сеть упадёт в песочнице → warning, но done)
    jid = make_job("telegram", "some_channel")
    out = run_job(jid)
    print(f"[telegram]  {out}")
    if out.get("status") != "done":
        failures.append("telegram job not done")

    # идемпотентность: повторный запуск не плодит факты
    with session() as s:
        n_before = s.query(Fact).count()
        p_before = s.query(Person).count()
    out2 = run_job(jid)
    with session() as s:
        n_after = s.query(Fact).count()
        p_after = s.query(Person).count()
    print(f"[rerun]     {out2}")
    if (n_after, p_after) != (n_before, p_before):
        failures.append(f"not idempotent: facts {n_before}->{n_after}, persons {p_before}->{p_after}")

    with session() as s:
        print(f"[totals]    persons={s.query(Person).count()} facts={s.query(Fact).count()} "
              f"audit={s.query(AuditLog).count()}")
        if s.query(AuditLog).count() < 6:
            failures.append("audit log missing entries")

    if failures:
        print("FAILURES:", *failures, sep="\n  - ")
        return 1
    print("ALL RUNNER TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
