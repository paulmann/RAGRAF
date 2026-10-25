"""Проверка реестра источников «Я это ты».

Свойства, которые обязаны держаться:
  1. добавление нормализует путь и не создаёт дубль;
  2. опасные корни (диск, домашний каталог, `.ragraf`, файл) отклоняются
     с причиной, а не молча;
  3. удаление убирает запись, но НЕ трогает файлы на диске;
  4. порядок источников детерминирован: приоритет, затем путь;
  5. реестр переживает повторное чтение (файл, а не память процесса).
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.sources import SourceRegistry, SourcesError  # noqa: E402

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


def expect_error(fn, label: str) -> None:
    try:
        fn()
        check(False, label, "исключения не было")
    except SourcesError as exc:
        check(True, label, f"({str(exc)[:60]})")


print("=" * 78)
print("1. ДОБАВЛЕНИЕ И НОРМАЛИЗАЦИЯ")
print("=" * 78)

with tempfile.TemporaryDirectory(prefix="ragraf-sources-") as tmp:
    base = pathlib.Path(tmp)
    root_a = base / "alpha"
    root_b = base / "beta"
    for path in (root_a, root_b, base / ".ragraf" / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    (base / "file.txt").write_text("x", encoding="utf-8")

    reg = SourceRegistry(base / "sources.json")
    src = reg.add(root_a)
    check(src.id == 1, "первый источник получил id=1", str(src.id))
    check(src.root.endswith("/alpha"), "корень записан в POSIX-форме", src.root)
    check(src.enabled, "новый источник включён")

    # Тот же каталог в другой форме — это дубль, а не второй источник.
    expect_error(lambda: reg.add(str(root_a) + "\\"), "дубль пути отклонён")

    expect_error(lambda: reg.add(root_a / "file.txt"), "файл вместо каталога отклонён")
    expect_error(lambda: reg.add(base / ".ragraf" / "cache"),
                 "каталог .ragraf отклонён (индекс не читает сам себя)")
    expect_error(lambda: reg.add(pathlib.Path.home()), "домашний каталог отклонён")
    drive = pathlib.Path(pathlib.Path.home().drive + "\\")
    expect_error(lambda: reg.add(drive), "корень диска отклонён")
    expect_error(lambda: reg.add(r"\\dead-host\share"),
                 "сетевой путь без --network отклонён (без обращения к сети по возможности)")
    expect_error(lambda: reg.add("s3://bucket/prefix"),
                 "URL-коннектор s3 отклонён с причиной")
    expect_error(lambda: reg.add("ssh://host/var/www"),
                 "URL-коннектор ssh отклонён с причиной")

print()
print("=" * 78)
print("2. ПОРЯДОК, ПРАВА И УДАЛЕНИЕ")
print("=" * 78)

with tempfile.TemporaryDirectory(prefix="ragraf-sources-") as tmp:
    base = pathlib.Path(tmp)
    root_a = base / "alpha"
    root_b = base / "beta"
    keep = base / "keep.txt"
    for path in (root_a, root_b):
        path.mkdir(parents=True, exist_ok=True)
        (path / "marker.txt").write_text("marker", encoding="utf-8")
    keep.write_text("keep", encoding="utf-8")

    path = base / "sources.json"
    reg = SourceRegistry(path)
    a = reg.add(root_a, priority=0)
    b = reg.add(root_b, priority=5)
    order = [s.id for s in reg.sources()]
    check(order == [b.id, a.id], "порядок: приоритет по убыванию", str(order))

    reg.set_enabled(a.id, False)
    check(reg.get(a.id).enabled is False, "источник выключен")
    check([s.id for s in reg.sources(enabled_only=True)] == [b.id],
          "выключенный источник не попадает в enabled_only")

    reg.set_priority(a.id, 9)
    check([s.id for s in reg.sources()] == [a.id, b.id],
          "приоритет меняет порядок детерминированно")

    # Повторное чтение с диска: реестр — файл, а не состояние процесса.
    again = SourceRegistry(path)
    check([s.id for s in again.sources()] == [a.id, b.id],
          "реестр перечитан с диска без потерь")

    removed = reg.remove(b.id)
    check(removed.id == b.id, "удалён именно указанный источник")
    check((root_b / "marker.txt").exists(), "файлы удалённого источника на диске целы")
    check(keep.exists(), "посторонний файл рядом цел")
    expect_error(lambda: reg.remove(b.id), "повторное удаление отклонено")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
