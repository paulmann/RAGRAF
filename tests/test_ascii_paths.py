"""Пути, которые JVM читает без искажения (ASCII + выбор временного каталога).

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_ascii_paths.py`).

Какой дефект закрывается. Замер 06.11.2026 на корпусе HARNESS
(`…/Документы/deepseek-harness/default-workspace`): Joern отказывал двумя
сообщениями сразу —

  * `Error: Argument input-dir failed when given '…\\??????'.
     Illegal char <?> at index 35` — корень корпуса;
  * `WARNING: java.io.tmpdir directory does not exist` — временный каталог JVM.

Причина одна: командная строка и `-D`-флаги доходят до JVM через ANSI-кодировку,
а не-ASCII символы в ней превращаются в `?`. Следствие было не «нет CPG», а
`capability=degraded` — то есть выглядело отказом инструмента, хотя инструмент
исправен.

Проверки:
  1. ASCII-путь не меняется вовсе (никаких преобразований «на всякий случай»);
  2. путь с кириллицей превращается в существующее короткое имя 8.3 и оно ASCII;
  3. путь к ещё НЕ созданному файлу тоже преобразуется (предок + остаток) —
     именно на этом ломался `-o <путь>.cpg.bin`: `GetShortPathNameW` для
     несуществующего листа молча возвращает длинное имя;
  4. временный каталог JVM выбирается так, чтобы ASCII была ДЛИННАЯ форма:
     `zstd-jni` канонизирует путь и передаёт его в `System.load`;
  5. отказ `build()` называет причину, а не падает трейсбеком Joern.

Ничего не меняет: работает в памяти и на одноразовых каталогах.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import cpg as cpg_mod  # noqa: E402
from ragraf import util  # noqa: E402

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


class _Cfg:
    """Минимальный стенд: joern_tmp читает только root и ragraf_dir."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.ragraf_dir = root / ".ragraf"
        self.project = root.name

        class _Index:
            joern_enabled = True
            joern_heap_gb = 2

        self.index = _Index()


def main() -> int:
    print("=== 1. ASCII-путь не трогается ===")
    plain = REPO
    check(util.ascii_path(plain) == str(plain),
          "ASCII-путь возвращается как есть", util.ascii_path(plain))
    check(util.is_ascii_path(plain), "ASCII-путь признан годным")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # Каталог с кириллицей в имени — как у корпуса HARNESS.
        cyr = base / "Документы" / "проект"
        cyr.mkdir(parents=True)
        (cyr / "main.py").write_text("x = 1\n", encoding="utf-8")

        print("\n=== 2. Кириллица → короткое имя 8.3 ===")
        converted = util.ascii_path(cyr)
        check(converted.isascii(), "преобразованный путь чистый ASCII", converted)
        check(Path(converted).is_dir(), "короткое имя указывает на тот же каталог",
              converted)
        check(Path(util.ascii_path(cyr / "main.py")).is_file(),
              "файл внутри каталога тоже доступен по короткому пути")
        check(util.ascii_path(cyr) == util.ascii_path(cyr),
              "повторный вызов даёт тот же результат (кэш)")

        print("\n=== 3. Путь к ещё не созданному файлу ===")
        future = cyr / "rev-0001.cpg.bin"
        got = util.ascii_path(future)
        check(got.isascii() and got.endswith("rev-0001.cpg.bin"),
              "несуществующий лист преобразован вместе с предком", got)
        check(Path(got).parent.is_dir(),
              "каталог из преобразованного пути существует", str(Path(got).parent))
        # Файл действительно создаётся по этому пути и виден по длинному.
        Path(got).write_text("x", encoding="utf-8")
        check(future.exists(), "файл, записанный по короткому пути, виден по длинному")

        print("\n=== 4. Временный каталог JVM ===")
        cfg = _Cfg(cyr)
        tmpdir = cpg_mod.joern_tmp(cfg)
        check(str(tmpdir.resolve()).isascii(),
              "длинная форма временного каталога ASCII", str(tmpdir.resolve()))
        check(tmpdir.is_dir(), "временный каталог создан", str(tmpdir))
        check(cpg_mod.joern_tmp_ascii(cfg), "проверка годности отвечает «годен»")
        # Обычный ASCII-корпус: временный каталог остаётся внутри .ragraf,
        # как и было — выбор не должен менять поведение без нужды.
        cfg_plain = _Cfg(base / "ascii-corpus")
        cfg_plain.root.mkdir(parents=True, exist_ok=True)
        check(cpg_mod.joern_tmp(cfg_plain) == cfg_plain.ragraf_dir / "tmp" / "joern",
              "для ASCII-корпуса каталог остался прежним",
              str(cpg_mod.joern_tmp(cfg_plain)))

        print("\n=== 5. Отказ называется причиной ===")
        # Корень с кириллицей и несуществующим драйвером: без CPG, но с текстом.
        runner = cpg_mod.JoernRunner.__new__(cpg_mod.JoernRunner)
        runner.cfg = cfg
        note = cpg_mod._console_failure(
            "io.joern.console.ConsoleException: Error creating project for input path: `/x`\n"
            "  at scala.runtime.Scala3RunTime$.assertFailed")
        check(bool(note) and "ConsoleException" in note,
              "отказ консоли Joern распознан как отказ", str(note))
        check(cpg_mod._console_failure("List(\"main\", \"helper\")") is None,
              "нормальный ответ отказом не считается")
        check(cpg_mod._console_failure(None) is None, "пустой ответ — не отказ")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
