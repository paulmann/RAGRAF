"""ПРОМТ-И14: мусор команды оболочки отличается от «файла без места».

Измеренный дефект: в корне проекта оказался файл с именем `=` нулевой длины, и
проверка поставки назвала его «файлом без места» — то есть причиной, по которой
непонятно, что делать. У мусора оболочки своя причина и своя правка: его удаляют,
а не объявляют удержанным.

Проверяется СВОЙСТВО, а не написание:
  * ловится мусор любой формы (`=`, `>`, `2>&1`, `>>`, `1>&2`), а не только `=`;
  * приманка `=real.txt` (в имени есть буквы) остаётся рабочим файлом;
  * файл нулевой длины с обычным именем мусором НЕ считается;
  * имя из одних цифр (`12345`, `2026-10-25`) мусором не считается;
  * ненулевой файл с именем `=` тоже мусором не считается: правило говорит
    «нулевая длина».

Ограничение платформы названо измерением: Windows не даёт создать файл с именем
`>` или `2>&1` (OSError 22, Invalid argument), поэтому дисковая песочница
содержит только форму `=`; остальные имена проверяются строкой. Это не «не
проверили», а «на этой платформе такая запись невозможна».
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "release"))

import editions_report  # noqa: E402

PASS = 0
FAIL = 0


def check(ok: bool, message: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {message}")
    else:
        FAIL += 1
        print(f"  FAIL {message}")


def main() -> int:
    print("=" * 78)
    print("1. СВОЙСТВО: нулевая длина + нет букв + есть знак оболочки")
    print("=" * 78)
    cases = [
        ("=", 0, True),
        (">", 0, True),
        ("2>&1", 0, True),
        (">>", 0, True),
        ("1>&2", 0, True),
        ("=real.txt", 0, False),      # приманка: буквы есть
        ("notes.txt", 0, False),      # пустой, но обычный файл
        ("12345", 0, False),          # одни цифры — не мусор
        ("2026-10-25", 0, False),     # цифры и дефисы — не мусор
        ("=", 12, False),             # не нулевой длины
        ("README.md", 37025, False),
    ]
    for name, size, expected in cases:
        got = editions_report.shell_artifact_of(name, size) is not None
        check(got == expected, f"{name!r} длиной {size} -> мусор={got}, ожидалось {expected}")

    print()
    print("=" * 78)
    print("2. ПЕСОЧНИЦА: что платформа позволяет создать на диске")
    print("=" * 78)
    with tempfile.TemporaryDirectory(prefix="ragraf-shell-artifact-") as tmp:
        sandbox = pathlib.Path(tmp)
        (sandbox / "=").write_bytes(b"")
        (sandbox / "=real.txt").write_bytes(b"real content\n")
        (sandbox / "work.py").write_bytes(b"print('ok')\n")

        found = sorted(
            p.name for p in sandbox.iterdir()
            if p.is_file() and editions_report.shell_artifact_of(p.name, p.stat().st_size)
        )
        check(found == ["="], f"на диске мусор найден по имени «=»: {found}")

        # Замер ограничения платформы: файл с именем «>» Windows создать не даёт.
        blocked = False
        try:
            (sandbox / ">").write_bytes(b"")
        except OSError as exc:
            blocked = True
            print(f"  инфо  файл «>» на этой платформе не создаётся: {exc.strerror}")
        check(blocked, "Windows не разрешает имя «>» — проверка формы идёт строкой")
        check(not (sandbox / ">").exists(), "файла «>» в песочнице нет")

        trap = sandbox / "=real.txt"
        check(trap.exists() and trap.stat().st_size > 0,
              "приманка =real.txt цела и непуста")
        check(not editions_report.shell_artifact_of("=real.txt", trap.stat().st_size),
              "приманка =real.txt не объявлена мусором")
        work = sandbox / "work.py"
        check(not editions_report.shell_artifact_of("work.py", work.stat().st_size),
              "рабочий файл work.py не объявлен мусором")

        reason = editions_report.shell_artifact_of("=", 0) or ""
        check("artifact of a shell redirect" in reason,
              "причина названа словами «artifact of a shell redirect»")
        check("без места" not in reason,
              "в причине нет формулировки «без места» — это другой класс")

    print()
    print("=" * 78)
    print("3. ЖИВОЕ ДЕРЕВО: мусора оболочки нет, другие классы не задеты")
    print("=" * 78)
    rows, counts, problems = editions_report.collect()
    artifacts = [p for p in problems if "shell redirect" in p]
    check(not artifacts, f"в живом дереве нет мусора оболочки (найдено {len(artifacts)})")
    homeless = [p for p in problems if "shell redirect" not in p]
    check(not homeless, f"файлов без места нет (найдено {len(homeless)})")
    print(f"  осмотрено файлов: {sum(counts.values())}, проблем всего: {len(problems)}")
    print(f"  классы: {dict(sorted(counts.items()))}")

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
