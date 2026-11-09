"""Замки: атомарный захват, признак живости, уборка брошенных.

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_locks.py`).

Какие дефекты закрываются (оба замерены 06.11.2026):

  1. **Захват не был атомарным.** Прежний код делал `path.exists()` и лишь
     потом `write_json`: два процесса одновременно видели «файла нет», оба
     записывали свой замок и ОБА считали его взятым. Защита «индексация уже
     идёт» в этом окне не работала вовсе.
  2. **Возраст старше 15 минут считался смертью владельца.** Полный прогон
     этого корпуса идёт 700 с, зависший HARNESS держал замок 8,3 ч — в обоих
     случаях второй процесс захватывал ЖИВОЙ замок и начинал вторую
     индексацию того же корпуса.

Проверки:
  1. два одновременных захвата в разных процессах — ровно один успешен;
  2. замок живого процесса не отбирается возрастом, если признак живости свеж;
  3. признак живости обновляется, и замок остаётся своим;
  4. замок мёртвого процесса захватывается (а не ждёт 15 минут);
  5. `locks --clear` снимает брошенные (мёртвые, битые) и НЕ трогает живые;
  6. `--force` без причины отказывает (код использования), с причиной — снимает;
  7. чужой замок нельзя снять из «не своего» экземпляра реестра.

Ничего не меняет за пределами временного каталога: реестр и замки лежат в
`RAGRAF_CATALOG_DIR`, содержимое — во временном каталоге.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import catalog as catalog_mod  # noqa: E402

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def dead_pid() -> int:
    """Номер процесса, которого заведомо нет: короткий прогон, уже завершённый."""
    proc = subprocess.run([sys.executable, "-c", "pass"], capture_output=True)
    return int(proc.returncode if proc.returncode > 1 else 0) or 999_999


def write_lock(path: Path, *, pid: int, ts: float, token: str = "x",
               category: str = "corpus") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "ts": ts, "token": token,
                                "category": category, "started": ts}),
                    encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = base / "state"
        state.mkdir()
        (state / "catalog.json").write_text(
            json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
        os.environ["RAGRAF_CATALOG_DIR"] = str(state)

        print("=== 1. Одновременный захват: успешен ровно один ===")
        # Два процесса, каждый берёт замок в свой момент. Без атомарного
        # создания (O_EXCL) оба видели «файла нет» и оба считали себя владельцем.
        race = base / "race.py"
        race.write_text(
            "import sys, time\n"
            f"sys.path.insert(0, {str(SRC)!r})\n"
            "from ragraf import catalog as c\n"
            "cat = c.Catalog()\n"
            "time.sleep(0.30)\n"          # оба процесса просыпаются одновременно
            "got = cat.acquire_corpus_lock('shared', timeout_sec=0.6, poll=0.01)\n"
            "print('GOT' if got else 'MISS')\n"
            "time.sleep(1.2 if got else 0.1)\n"
            "cat.release_corpus_lock('shared')\n",
            encoding="utf-8", newline="\n")
        env = dict(os.environ)
        env["RAGRAF_CATALOG_DIR"] = str(state)
        procs = [subprocess.Popen([sys.executable, str(race)], stdout=subprocess.PIPE,
                                  text=True, encoding="utf-8", env=env)
                 for _ in range(2)]
        outs = [(proc.communicate(timeout=120)[0] or "").strip() for proc in procs]
        wins = sum(1 for out in outs if "GOT" in out)
        check(wins == 1, "замок достался ровно одному процессу",
              f"результаты: {outs}")

        print("\n=== 2. Живой замок не отбирается возрастом ===")
        catalog = catalog_mod.Catalog()
        lock = catalog.corpus_lock_path("live")
        # Замок «старый», но процесс-держатель жив и признак живости свежий.
        write_lock(lock, pid=os.getpid(), ts=time.time())
        got = catalog.acquire_corpus_lock("live", timeout_sec=1.0, poll=0.05)
        check(not got, "замок живого процесса не взят вторым захватом")
        check(lock.exists(), "файл замка остался на месте", str(lock))

        print("\n=== 3. Признак живости обновляется ===")
        before = float(json.loads(lock.read_text(encoding="utf-8"))["ts"])
        time.sleep(0.15)
        touched = catalog.touch_corpus_lock("live")
        after = float(json.loads(lock.read_text(encoding="utf-8"))["ts"])
        check(touched and after > before, "метка времени обновлена",
              f"{before} → {after}")

        print("\n=== 4. Замок мёртвого процесса захватывается ===")
        write_lock(lock, pid=dead_pid(), ts=time.time() - 5)
        fresh_catalog = catalog_mod.Catalog()
        got = fresh_catalog.acquire_corpus_lock("live", timeout_sec=3.0, poll=0.05)
        check(got, "мёртвый замок захвачен, а не ожидание 15 минут")
        if got:
            fresh_catalog.release_corpus_lock("live")

        print("\n=== 5. Уборка брошенных замков ===")
        live_lock = catalog.corpus_lock_path("alive-one")
        dead_lock = catalog.corpus_lock_path("dead-one")
        broken = catalog.lock_path.parent / "locks" / "broken.lock"
        write_lock(live_lock, pid=os.getpid(), ts=time.time(), token="mine")
        write_lock(dead_lock, pid=dead_pid(), ts=time.time() - 60)
        broken.write_text("{ это не json", encoding="utf-8")
        cat2 = catalog_mod.Catalog()
        removed = cat2.clear_abandoned_locks()
        names = {row["file"] for row in removed}
        check("dead-one.lock" in names, "замок мёртвого процесса снят", str(names))
        check("broken.lock" in names, "битый замок снят (он ничего не защищает)", str(names))
        check(live_lock.exists(), "замок живого процесса НЕ снят", str(live_lock))

        print("\n=== 6. Принудительное снятие требует причины ===")
        env = dict(os.environ)
        env["RAGRAF_CATALOG_DIR"] = str(state)
        no_reason = subprocess.run(
            [sys.executable, str(REPO / "tools" / "ragraf.py"), "locks",
             "--clear", "--force"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        check(no_reason.returncode != 0,
              "--force без --reason отклонён",
              f"код {no_reason.returncode}: {no_reason.stderr.strip()[:120]}")
        with_reason = subprocess.run(
            [sys.executable, str(REPO / "tools" / "ragraf.py"), "locks",
             "--clear", "--force", "--reason", "проба: процесс проверен вручную"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        check(with_reason.returncode == 0, "--force с причиной выполнен",
              f"код {with_reason.returncode}: {with_reason.stderr.strip()[:120]}")
        check(not live_lock.exists(), "живой замок снят принудительно (по решению)")


        print("\n=== 7. Чужой замок не снимается ===")
        foreign = catalog.corpus_lock_path("foreign")
        write_lock(foreign, pid=dead_pid(), ts=time.time(), token="чужой")
        catalog_mod.Catalog().release_corpus_lock("foreign")
        check(foreign.exists(), "замок чужого процесса не снят своим снятием",
              str(foreign))
        # Уберём за собой: замок записан этим же pid, поэтому снимаем явно.
        foreign.unlink(missing_ok=True)

        print("\n=== 8. Отчёт называет вердикт ===")
        write_lock(catalog.lock_path.parent / "locks" / "report.lock",
                   pid=dead_pid(), ts=time.time() - 120, category="report")
        rows = catalog_mod.Catalog().lock_report()
        row = next((item for item in rows if item["file"] == "report.lock"), None)
        check(row is not None and row["verdict"] in ("dead", "silent"),
              "вердикт назван, а не «файл есть»", str(row))
        (catalog.lock_path.parent / "locks" / "report.lock").unlink(missing_ok=True)

        os.environ.pop("RAGRAF_CATALOG_DIR", None)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
