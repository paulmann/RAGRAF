"""Проверка P0-2: бюджет времени останавливает прогон ВНУТРИ стадии и называет место.

Требование ТЗ: «Никогда не отвечать таймаутом без деталей: при превышении
бюджета — ответ status=timeout с этапом, числом обработанных файлов и
подсказкой». До этой правки бюджет проверялся только МЕЖДУ стадиями, поэтому
самая долгая стадия (`ast` на корпусе BWO — 370–410 с при бюджете 120 с)
завершалась целиком и только потом выяснялось, что времени не было.

Проверяется три свойства:
  1. прогон с исчерпанным бюджетом ОСТАНАВЛИВАЕТСЯ и не закрепляет ревизию;
  2. в отчёте есть стадия и число обработанных файлов (а не просто «таймаут»);
  3. прогон с достаточным бюджетом по-прежнему проходит и не помечается.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.init_corpus import create_config  # noqa: E402

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


PHP = """<?php
function probe_%(i)d($x) {
    $y = $x + %(i)d;
    return $y * 2;
}
"""


def build_corpus(root: Path, count: int = 200) -> None:
    (root / ".gitignore").write_text("probe\n", encoding="utf-8")
    src = root / "src"
    src.mkdir()
    for i in range(count):
        (src / f"mod{i:03d}.php").write_text(PHP % {"i": i}, encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_budget_"))
    try:
        build_corpus(tmp)
        cfg_path, _scan = create_config(tmp, project="budgetprobe",
                                         config_path=tmp / "budget.yaml")
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)

        print("=== 1. Бюджет 1 мс: прогон обязан остановиться внутри стадии ===")
        before = None
        idx = Indexer(cfg)
        try:
            before = idx.control.current_rev()
            report = idx.run(with_vectors=False, budget_seconds=0.001)
        finally:
            idx.close()
        print(f"  отчёт: budget_exceeded={report.budget_exceeded} "
              f"stage={report.budget_stage!r} files_done={report.budget_files_done} "
              f"ok={report.ok}")
        check(report.budget_exceeded, "прогон помечен как превысивший бюджет")
        check(bool(report.budget_stage), "стадия названа", report.budget_stage)
        check(int(report.budget_files_done) > 0,
              "число обработанных файлов названо", str(report.budget_files_done))
        check(report.budget_files_done < 200,
              "прогон остановился, не дойдя до конца корпуса",
              str(report.budget_files_done))
        check(not report.ok, "отчёт не объявляет прогон успешным")
        note = " ".join(report.notes)
        check("бюджет" in note and "прерван" in note,
              "в примечаниях есть причина и подсказка", note[:200])
        check("index.budget_seconds" in note or "exclude" in note,
              "подсказка называет, что делать", note[:200])
        after = None
        idx2 = Indexer(cfg)
        try:
            after = idx2.control.current_rev()
        finally:
            idx2.close()
        check(after == before, "ревизия не закреплена: прежний индекс остался рабочим",
              f"было {before}, стало {after}")

        print("\n=== 2. Достаточный бюджет: прогон проходит и не помечается ===")
        idx3 = Indexer(cfg)
        try:
            ok_report = idx3.run(with_vectors=False, budget_seconds=300)
        finally:
            idx3.close()
        check(not ok_report.budget_exceeded, "прогон не помечен как превысивший бюджет",
              str(ok_report.budget_stage))
        check(ok_report.ok and bool(ok_report.rev_id), "прогон завершён",
              f"ok={ok_report.ok} rev={ok_report.rev_id}")
        check(ok_report.files >= 200, "все файлы корпуса обработаны",
              str(ok_report.files))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
