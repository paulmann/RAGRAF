"""Свойства закрытия брошенных ревизий (этап 12, находка 2).

Что закрепляется — замер 31.10.2026: в журнале корпуса ragraf **9** записей в
состоянии `building` (rev-0519 от 28.10 … rev-1112), у всех `files`/`nodes`/`edges`
пусты, и ни у одной нет события в `event_log`. Оборванный прогон оставлял запись,
которую никто не закрывал и не называл: политика хранения говорит про ДАННЫЕ
ревизии, а не про запись.

Закрывать «все `building` старше суток» нельзя: рядом может идти ЖИВОЙ прогон
другого процесса, и второй закрыл бы чужую работу. Поэтому запись несёт признак
живости (`heartbeat_at`, обновляется на каждой стадии), а закрытие требует, чтобы
он устарел. Проверки ниже — про это, а не про факт закрытия.
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import util  # noqa: E402
from ragraf.revisions import ControlStore  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"\n      {detail}" if detail else ""))


def scratch_dir() -> pathlib.Path:
    try:
        d = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-stale-"))
        probe = d / "probe.txt"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return d
    except OSError:
        d = REPO / ".staging" / "stale-revision-test"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        return d


def make_store(tmp: pathlib.Path) -> ControlStore:
    cfg = SimpleNamespace(
        ragraf_dir=tmp / ".ragraf",
        control_db=tmp / ".ragraf" / "control.sqlite",
        rev_file=tmp / ".ragraf" / "index" / "rev.json",
        project="staleprobe",
    )
    (tmp / ".ragraf" / "index").mkdir(parents=True, exist_ok=True)
    cfg.ragraf_dir.mkdir(parents=True, exist_ok=True)
    return ControlStore(cfg)


def age(store: ControlStore, rev_id: str, seconds: int) -> None:
    """Сдвинуть признак живости в прошлое — так выглядит оборванный прогон."""
    from datetime import datetime, timedelta, timezone

    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    store.conn.execute(
        "UPDATE rev_history SET heartbeat_at = ? WHERE rev_id = ?", (stamp, rev_id)
    )
    store.conn.commit()


def main() -> int:
    tmp = scratch_dir()
    store = make_store(tmp)
    try:
        # 0. Колонка признака живости есть в базе (догоняющая миграция).
        cols = {str(r[1]) for r in store.conn.execute("PRAGMA table_info(rev_history)")}
        check("heartbeat_at" in cols, "колонка heartbeat_at появилась в журнале", str(sorted(cols)))

        for rev in ("rev-0001", "rev-0002", "rev-0003", "rev-0004"):
            store.register_rev(rev, None, "hash-" + rev, "cfg", "full", False, "staleprobe")

        # 1. Признак живости пишется при регистрации.
        row = store.conn.execute(
            "SELECT heartbeat_at FROM rev_history WHERE rev_id = 'rev-0001'").fetchone()
        check(bool(row and row["heartbeat_at"]), "признак живости записан при регистрации")

        # 2. Живой прогон не закрывается: свежий признак живости — это и есть
        #    защита от гонки с параллельной индексацией.
        closed = store.close_abandoned(900)
        check(closed == [], "свежие записи не закрыты", f"закрыто {len(closed)}")

        # 3. `touch_revision` продлевает жизнь записи.
        age(store, "rev-0002", 5000)
        closed = store.close_abandoned(900)
        check([c["rev_id"] for c in closed] == ["rev-0002"],
              "устаревшая запись закрыта", str([c["rev_id"] for c in closed]))
        state = store.conn.execute(
            "SELECT state FROM rev_history WHERE rev_id = 'rev-0002'").fetchone()["state"]
        check(state == "aborted", "состояние стало aborted (данных нет — отдавать нечего)", state)

        # 4. Событие названо и содержит числа: без них «закрыто» — не факт.
        events = store.conn.execute(
            "SELECT payload FROM event_log WHERE kind = 'rev_closed_stale'").fetchall()
        check(len(events) == 1, "событие rev_closed_stale записано одно", str(len(events)))
        payload = util.loads(events[0]["payload"]) if events else {}
        check(payload.get("idle_seconds", 0) >= 900, "в событии назван простой в секундах",
              str(payload.get("idle_seconds")))
        check(payload.get("threshold_seconds") == 900, "в событии назван порог",
              str(payload.get("threshold_seconds")))

        # 5. Повторный вызов ничего не закрывает: закрытие однократно.
        again = store.close_abandoned(900)
        check(again == [], "повторное закрытие ничего не делает", str(len(again)))

        # 6. Запечатанная ревизия не трогается, даже если признак живости старый.
        store.seal_rev("rev-0003", 10, 20, 30, "full", False)
        age(store, "rev-0003", 5000)
        store.close_abandoned(900)
        state = store.conn.execute(
            "SELECT state FROM rev_history WHERE rev_id = 'rev-0003'").fetchone()["state"]
        check(state == "ready", "запечатанная ревизия осталась ready", state)

        # 7. Порог соблюдается: запись с простоем меньше порога живёт.
        age(store, "rev-0004", 100)
        alive = store.close_abandoned(900)
        check(alive == [], "запись с простоем меньше порога не закрыта", str(len(alive)))

        # 8. В `building` остаются РОВНО живые записи: устаревших не осталось.
        #    Это и есть свойство находки 2 — «оборванная запись не живёт вечно»,
        #    а не «building исчез совсем»: свежая запись обязана ждать своего срока.
        building = sorted(
            str(r["rev_id"]) for r in store.conn.execute(
                "SELECT rev_id FROM rev_history WHERE state = 'building'")
        )
        check(building == ["rev-0001", "rev-0004"],
              "в building остались только свежие записи (rev-0001, rev-0004)", str(building))
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"итог: пройдено {PASS}, провалено {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())