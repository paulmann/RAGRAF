"""Ремонт адресов `файл:строка` по правилу, а не по догадке.

Зачем отдельный инструмент. В блоке 25 правки ядра сдвинули 28 адресов, и автоматический
пересчёт «по похожему абзацу» ИСПОРТИЛ документы: в абзаце несколько ссылок на один файл, и
маркер одного утверждения применялся к соседям (`.work/repair_refs25e.py`, 104 «исправления»).
Восстанавливать пришлось вручную, с двумя ошибками на 28 ссылок.

Правило здесь одно и проверяемое:

  1. **Одна пара — одна ссылка.** Пара из `PAIRS` правит РОВНО одну ссылку: ту, что в её
     абзаце относится к её файлу и стоит ближе всех к её маркеру. Если кандидатов два и они
     равны по расстоянию — ОТКАЗ с названием пары, а не выбор наугад.
  2. **Признак из отчёта — для ссылок без пары.** У `wrong_object` отчёт называет признак
     («признака «X» нет»); он обязан встречаться в файле ровно один раз в пределах ±60 строк
     от текущего адреса, иначе — ОТКАЗ с причиной.
  3. **Диапазон правится целиком.** `file.py:1176-1179` получает `маркер-маркер+3`, а не
     только первую половину.
  4. **Идемпотентность.** Второй запуск на неизменённом дереве не меняет ни байта.

Запуск:
    python tools/fix_line_refs.py                 # сухой прогон, таблица решений
    python tools/fix_line_refs.py --apply         # записать
    python tools/fix_line_refs.py --root <каталог> --apply
Коды: 0 — расхождений нет или всё исправлено; 1 — есть отказы (ничего не записано);
2 — ошибка аргументов.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS = pathlib.Path(__file__).resolve().parent
REPO = TOOLS.parent
WINDOW = 60


def load_checker(root: pathlib.Path):
    """Загрузить модуль проверки адресов из того же дерева."""
    path = root / "tools" / "check_line_refs.py"
    if not path.is_file():
        raise SystemExit(f"нет {path}: сверять не с чем")
    spec = importlib.util.spec_from_file_location("_clr_fix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Refusal(Exception):
    """Отказ: правило не даёт однозначного решения. Ничего не записывается."""


def pick_target(refs: list[tuple[int, str, int, int | None]], hits: list[int],
                path: str) -> tuple[int, str, int, int | None]:
    """Выбрать ОДНУ ссылку пары: ближайшую к маркеру, при равенстве — отказ."""
    # Одна ссылка может попасть в список дважды (`REF` и продолжение строки): одинаковые
    # записи схлопываются, иначе «равноудалённые кандидаты» — это она же сама.
    seen: set[tuple[int, str, int, int | None]] = set()
    unique: list[tuple[int, str, int, int | None]] = []
    for item in refs:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    refs = unique
    own = [item for item in refs if item[1].replace("\\", "/") == path]
    if not own:
        raise Refusal(f"в абзаце нет ссылки на {path}")

    def distance(item: tuple[int, str, int, int | None]) -> int:
        return min(abs(item[2] - hit) for hit in hits)

    ranked = sorted(own, key=distance)
    # Ближайшие адреса: сравниваются РАЗНЫЕ номера, а не отдельные записи. Две записи,
    # ведущие на один адрес, — не неоднозначность: правка у них одна и та же.
    best_distance = distance(ranked[0])
    nearest = [item for item in ranked if distance(item) == best_distance]
    distinct = sorted({item[2] for item in nearest})
    if len(distinct) > 1:
        raise Refusal(
            f"два разных адреса {path} равноудалены от маркера ({best_distance} строк): "
            f"{distinct} — выбор был бы догадкой"
        )
    return nearest[0]


def plan_pairs(checker, root: pathlib.Path) -> tuple[list[dict], list[str]]:
    """Решения по парам: что менять и что невозможно."""
    changes: list[dict] = []
    refusals: list[str] = []
    for pair in checker.PAIRS:
        doc = root / pair["doc"]
        source = root / pair["path"]
        if not doc.is_file() or not source.is_file():
            continue
        lines = checker._read(doc)
        span = checker.paragraph(lines, pair["anchor"])
        if span is None:
            continue
        lo, hi = span
        refs = checker.collect_refs(lines[lo - 1:hi])
        src_lines = checker._read(source)
        hits = [i + 1 for i, row in enumerate(src_lines) if pair["marker"] in row]
        if not hits:
            refusals.append(f"{pair['doc']}: маркер «{pair['marker'][:40]}» не найден в {pair['path']}")
            continue
        if hits[0] <= 1:
            refusals.append(f"{pair['doc']}: маркер «{pair['marker'][:40]}» стоит на первой строке файла")
            continue
        try:
            lineno, rel, first, last = pick_target(refs, hits, pair["path"])
        except Refusal as exc:
            refusals.append(f"{pair['doc']}:{lo} — {exc}")
            continue
        want = hits[0]
        if first in hits:
            continue
        if last is not None:
            # Диапазон проверяется попаданием: если маркер внутри — адрес верен и трогать
            # его нельзя (смысл утверждения в диапазоне, а не в одной строке).
            if first <= want <= last:
                continue
            refusals.append(
                f"{pair['doc']}:{lo + lineno - 1} — ссылка диапазоном {first}-{last}, "
                f"а маркер на {want}: вне диапазона, замена была бы другим объектом"
            )
            continue
        changes.append({
            "doc": pair["doc"], "line": lo + lineno - 1, "path": pair["path"],
            "old": first, "old_last": last, "new": want,
            "why": f"маркер «{pair['marker'][:44]}» стоит на строке {want}",
        })
    return changes, refusals


def plan_reported(checker, root: pathlib.Path, text: str) -> tuple[list[dict], list[str]]:
    """Решения по ссылкам без пары: признак из отчёта должен быть однозначен."""
    changes: list[dict] = []
    refusals: list[str] = []
    for line in text.splitlines():
        match = re.match(r"\s*- ([\w./\\-]+):(\d+)\s+(\S+)\s+\[wrong_object\]", line)
        if not match:
            continue
        doc_rel, doc_line, file_rel = match.group(1), int(match.group(2)), match.group(3)
        marker_match = re.search(r"признака «(.+?)»", line)
        if not marker_match:
            refusals.append(f"{doc_rel}:{doc_line} — отчёт не назвал признак")
            continue
        marker = marker_match.group(1)
        doc = root / doc_rel
        source = root / file_rel.replace("\\", "/")
        if not doc.is_file() or not source.is_file():
            continue
        rows = checker._read(doc)
        ref = None
        for shift in range(0, 4):
            for index in (doc_line - 1 + shift, doc_line - 1 - shift):
                if not 0 <= index < len(rows):
                    continue
                found = re.search(re.escape(file_rel.split("/")[-1]) + r":(\d+)", rows[index])
                if found:
                    ref = (index, found)
                    break
            if ref:
                break
        if ref is None:
            refusals.append(f"{doc_rel}:{doc_line} — адрес не найден рядом")
            continue
        index, found = ref
        old = int(found.group(1))
        src = checker._read(source)
        near = [i + 1 for i, row in enumerate(src)
                if marker in row and abs(i + 1 - old) <= WINDOW]
        if len(near) != 1:
            refusals.append(
                f"{doc_rel}:{index + 1} — признак «{marker[:40]}» встречается {len(near)} раз "
                f"в пределах ±{WINDOW} строк от {old}: нужно ровно одно вхождение"
            )
            continue
        if near[0] == old:
            continue
        changes.append({"doc": doc_rel, "line": index + 1, "path": file_rel,
                        "old": old, "old_last": None, "new": near[0],
                        "why": f"признак «{marker[:44]}» стоит на строке {near[0]}"})
    return changes, refusals


def apply_changes(root: pathlib.Path, changes: list[dict]) -> int:
    """Записать решения. Диапазон правится целиком: длина сохраняется."""
    by_doc: dict[str, list[dict]] = {}
    for change in changes:
        by_doc.setdefault(change["doc"], []).append(change)
    written = 0
    for doc_rel, items in by_doc.items():
        doc = root / doc_rel
        rows = doc.read_text(encoding="utf-8").splitlines(keepends=True)
        for item in items:
            index = item["line"] - 1
            if not 0 <= index < len(rows):
                continue
            row = rows[index]
            name = item["path"].replace("\\", "/").split("/")[-1]
            pattern = re.escape(name) + r":(\d+)(?:-(\d+))?"
            replaced = False

            def repl(match: re.Match) -> str:
                nonlocal replaced
                if replaced or int(match.group(1)) != item["old"]:
                    return match.group(0)
                replaced = True
                if match.group(2):
                    span = int(match.group(2)) - int(match.group(1))
                    return f"{name}:{item['new']}-{item['new'] + span}"
                return f"{name}:{item['new']}"

            rows[index] = re.sub(pattern, repl, row)
            if replaced:
                written += 1
        doc.write_text("".join(rows), encoding="utf-8", newline="\n")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ремонт адресов по правилу")
    parser.add_argument("--root", default=str(REPO))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root)
    if not root.is_dir():
        print(f"FAIL: каталог не найден: {root}")
        return 2

    checker = load_checker(root)
    report = checker.report_text() if hasattr(checker, "report_text") else ""
    if not report:
        import subprocess
        out = subprocess.run([sys.executable, str(root / "tools" / "check_line_refs.py")],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        report = (out.stdout or "") + (out.stderr or "")

    pair_changes, pair_refusals = plan_pairs(checker, root)
    rep_changes, rep_refusals = plan_reported(checker, root, report)
    changes = pair_changes + rep_changes
    refusals = pair_refusals + rep_refusals

    if not args.quiet:
        print(f"решений: {len(changes)}, отказов: {len(refusals)}")
        for item in changes:
            span = f"-{item['old_last']}" if item.get("old_last") else ""
            print(f"  {item['doc']}:{item['line']}  {item['path']}: "
                  f"{item['old']}{span} → {item['new']}   ({item['why']})")
        for line in refusals:
            print(f"  ОТКАЗ: {line}")

    if refusals:
        if args.apply:
            print("запись не выполнена: есть отказы, и догадка запрещена")
        return 1

    if not args.apply:
        print("сухой прогон: ничего не записано (--apply чтобы записать)")
        return 0

    written = apply_changes(root, changes)
    print(f"записано правок: {written}")
    second, second_refusals = plan_pairs(checker, root)
    print(f"повторная проверка: решений {len(second)}, отказов {len(second_refusals)}")
    return 0 if not second else 1


if __name__ == "__main__":
    raise SystemExit(main())
