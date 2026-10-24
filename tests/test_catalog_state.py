"""Проверка состояния реестра: «заведена» и «проиндексирована» — разные вещи.

Зачем проверка. Регистрация разработки фиксирует суммы корпуса ДО индексации и
зовёт ту же точку записи, что и прогон, — с пустым `rev_id`. Первая редакция
писала такую запись в историю прогонов (`runs`), и в реестре оказывалась
разработка со «файлов 54 235, узлов 0» (измерено 14.10.2026 на AutoClaw:
`C:\\AutoClaw\\.ragraf` не существует вовсе, то есть индекса там нет, а запись
читается как сломанный индекс).

Правило: пустая ревизия НЕ попадает в историю прогонов; состояние записи
называется прямо (`registered` / `indexed`).

Работа идёт на ВРЕМЕННОМ файле реестра: рабочий `catalog/catalog.json` не
изменяется.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.catalog import Catalog  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_catalog_"))
try:
    path = tmp / "catalog.json"
    cat = Catalog(path)

    print("=== 1. Регистрация без индексации ===")
    entry = cat.record_run("ProbeCorpus", rev_id="", corpus_hash="h1",
                           config_hash="c1", file_count=54235,
                           trigger="register")
    check(entry.state == "registered", "состояние — «заведена»", entry.state)
    check(len(entry.runs) == 0, "история прогонов НЕ пополнена пустой ревизией",
          f"runs={len(entry.runs)}")
    check(entry.file_count == 54235, "число файлов зафиксировано (сумма корпуса)",
          str(entry.file_count))
    check(entry.rev_id == "", "ревизии нет и она не выдумана", repr(entry.rev_id))

    print("\n=== 2. Сводка называет состояние ===")
    row = [r for r in cat.summary()["entries"] if r["category"] == "ProbeCorpus"][0]
    check(row["state"] == "registered", "в сводке state=registered", str(row["state"]))
    check(row["runs"] == 0, "в сводке ноль прогонов", str(row["runs"]))

    print("\n=== 3. Прогон с ревизией попадает в историю ===")
    entry = cat.record_run("ProbeCorpus", rev_id="rev-0001", corpus_hash="h1",
                           config_hash="c1", file_count=10, node_count=99,
                           edge_count=7, seconds=1.5, capability="full",
                           trigger="cli:index")
    check(entry.state == "indexed", "состояние — «проиндексирована»", entry.state)
    check(len(entry.runs) == 1, "прогон записан", f"runs={len(entry.runs)}")
    check(entry.runs[0]["rev_id"] == "rev-0001", "ревизия прогона записана")
    check(entry.runs[0]["trigger"] == "cli:index", "триггер прогона записан")
    row = [r for r in cat.summary()["entries"] if r["category"] == "ProbeCorpus"][0]
    check(row["state"] == "indexed" and row["runs"] == 1,
          "сводка показывает проиндексированное состояние",
          f"{row['state']}/{row['runs']}")

    print("\n=== 4. Повторная регистрация не понижает состояние и не врёт ===")
    entry = cat.record_run("ProbeCorpus", rev_id="", corpus_hash="h2",
                           config_hash="c1", file_count=11, trigger="register")
    check(entry.state == "indexed",
          "регистрация не отменяет построенный индекс", entry.state)
    check(len(entry.runs) == 1, "история прогонов не тронута", str(len(entry.runs)))
    check(entry.rev_id == "rev-0001",
          "ревизия построенного индекса не затёрта пустой", repr(entry.rev_id))

    print("\n=== 5. Запись переживает чтение из файла (совместимость формата) ===")
    again = Catalog(path)
    row = [r for r in again.summary()["entries"] if r["category"] == "ProbeCorpus"][0]
    check(row["state"] == "indexed", "состояние прочитано из файла", str(row["state"]))
    check(row["rev_id"] == "rev-0001", "ревизия прочитана из файла")

    print("\n=== 6. Старый файл без поля state не ломает чтение ===")
    legacy = tmp / "legacy.json"
    legacy.write_text(
        '{"version": 1, "entries": {"Old": {"category": "Old", "root": "r",'
        ' "config": "c", "rev_id": "rev-0007", "node_count": 5}}}',
        encoding="utf-8", newline="\n")
    old = Catalog(legacy)
    row = old.summary()["entries"][0]
    check(row["state"] == "indexed",
          "состояние выводится из наличия ревизии у старой записи", str(row["state"]))
    legacy_empty = tmp / "legacy_empty.json"
    legacy_empty.write_text(
        '{"version": 1, "entries": {"Old2": {"category": "Old2", "root": "r",'
        ' "config": "c"}}}', encoding="utf-8", newline="\n")
    row2 = Catalog(legacy_empty).summary()["entries"][0]
    check(row2["state"] == "registered",
          "запись без ревизии читается как «заведена»", str(row2["state"]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
