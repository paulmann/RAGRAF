"""Приманки правила ремонта адресов: проверяется само правило.

В блоке 25 автоматический пересчёт испортил документы. Правило нового инструмента:
одна пара — одна ссылка; при равенстве расстояний разных адресов — отказ; диапазон
проверяется попаданием, а не подстановкой.

Проверки идут на функциях правила (поведение) и на живом дереве (идемпотентность):
  1. живое дерево: решений 0, отказов 0;
  2. две ссылки на один файл, маркер ближе к одной → выбрана она;
  3. две ссылки равноудалены на РАЗНЫХ адресах → отказ;
  4. две записи на ОДИН адрес → не отказ (правка одна и та же);
  5. диапазон, покрывающий маркер, править не нужно; диапазон вне маркера → отказ;
  6. признак, встречающийся дважды рядом, — отказ.

Запуск: python tools/check_fix_refs_rule.py  (печатает `осмотрено: N`, находки — `FAIL:`)
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS = pathlib.Path(__file__).resolve().parent
REPO = TOOLS.parent
PASS = 0
FAIL = 0


def check(cond: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def load(root: pathlib.Path, name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, root / "tools" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    fix = load(REPO, "fix_line_refs.py", "_fix")
    checker = fix.load_checker(REPO)
    changes, refusals = fix.plan_pairs(checker, REPO)
    check(not changes, "живое дерево: решений нет", f"{len(changes)}")
    check(not refusals, "живое дерево: отказов нет", "; ".join(refusals[:2]))

    path = "src/ragraf/util.py"

    # 2. Две ссылки, маркер ближе к первой (120) — выбирается она.
    refs = [(1, path, 120, None), (2, path, 300, None)]
    picked = fix.pick_target(refs, [125], path)
    check(picked[2] == 120, "выбрана ближайшая к маркеру ссылка", str(picked))

    # 3. Равноудалены на разных адресах — отказ.
    refs = [(1, path, 100, None), (2, path, 200, None)]
    try:
        fix.pick_target(refs, [150], path)
        refused = False
    except fix.Refusal:
        refused = True
    check(refused, "равные расстояния на разных адресах — отказ")

    # 4. Две записи на один адрес — не отказ.
    refs = [(1, path, 199, 206), (2, path, 199, None)]
    picked = fix.pick_target(refs, [199], path)
    check(picked[2] == 199, "две записи на один адрес не считаются неоднозначностью", str(picked))

    # 5. Диапазон: покрывающий маркер править не нужно; вне маркера — отказ.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = pathlib.Path(tmp)
        (root / "docs").mkdir(parents=True)
        (root / "src").mkdir(parents=True)
        (root / "src" / "sample.py").write_text(
            "\n".join(["# 0"] + [f"line {i}" for i in range(1, 40)] + ["def marker(): pass"]) + "\n",
            encoding="utf-8")
        marker_line = 41
        doc = root / "docs" / "doc.md"

        # Диапазон покрывает маркер.
        doc.write_text(f"Утверждение про marker. `src/sample.py:{marker_line - 2}-{marker_line + 2}`\n",
                       encoding="utf-8", newline="\n")
        span = checker.paragraph(checker._read(doc), "Утверждение про marker")
        inside = checker.collect_refs(checker._read(doc)[span[0] - 1:span[1]]) if span else []
        picked_inside = fix.pick_target(inside, [marker_line], "src/sample.py") if inside else None
        check(picked_inside is not None and picked_inside[2] <= marker_line <= (picked_inside[3] or 0),
              "диапазон, покрывающий маркер, выбирается без правки", str(picked_inside))

        # Диапазон мимо маркера: правило обязано отказать при попытке правки.
        doc.write_text(f"Утверждение про marker. `src/sample.py:3-8`\n", encoding="utf-8", newline="\n")
        pair = {"doc": "docs/doc.md", "anchor": "Утверждение про marker",
                "path": "src/sample.py", "marker": "def marker"}
        saved = checker.PAIRS
        checker.PAIRS = (pair,)
        try:
            plan, refusals_span = fix.plan_pairs(checker, root)
        finally:
            checker.PAIRS = saved
        check(not plan and bool(refusals_span),
              "диапазон мимо маркера — отказ, а не подстановка первой границы",
              f"решений {len(plan)}, отказов {refusals_span}")

        # 6. Признак дважды рядом — отказ.
        (root / "src" / "double.py").write_text(
            "\n".join(["# a", "def twice(): pass", "# b", "def twice(): pass", "# c"]) + "\n",
            encoding="utf-8")
        doc.write_text("Адрес `src/double.py:2`.\n", encoding="utf-8", newline="\n")
        report = ("  - docs/doc.md:1  src/double.py  [wrong_object]  утверждение «X» ведёт в "
                  "src/double.py, но признака «def twice» в указанных строках нет")
        plan2, refusals2 = fix.plan_reported(checker, root, report)
        check(bool(refusals2), "признак дважды рядом — отказ", f"решений {len(plan2)}")

    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print(f"осмотрено: {PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
