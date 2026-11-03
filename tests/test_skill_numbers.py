# -*- coding: utf-8 -*-
"""Числа навыка сверяются с кодом — и это ловится подсадкой (этап 3.1).

Зачем набор. `docs/skills/ragraf-core/reference/operations.md` заявлял «39 операций /
41 инструмент» при фактических 64 и 71, `reference/repair.md` — «33 проверки» при 45.
Прожило это молча по двум причинам, и обе проверяются здесь:

  1. правило чисел применялось только к КОПИЯМ файлов с режимом `declared`, а
     `operations.md` и `repair.md` объявлены `identical`: сравнивались копии между
     собой, а совпадать они могли и в неверном числе;
  2. шаблон числа не видел формы «проверок» (после 5 и больше по-русски пишут
     именно так): строка «45 проверок» считалась отсутствием заявления.

Проверки идут на ПЕСОЧНИЦЕ (`--root`), потому что подсадка испорченного числа в
живой навык недопустима. Ни один живой файл не меняется.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import re
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
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


def build_sandbox(root: Path) -> Path:
    """Песочница: код для счёта, навык и объявление с копией ВНУТРИ дерева."""
    for relative in ("tools/check_skill_variants.py",
                     "tools/Invoke-ProjectDoctrine.ps1",
                     "src/ragraf/mcp_server.py",
                     "src/ragraf/facade.py"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
    skill = root / "docs" / "skills" / "ragraf-core"
    shutil.copytree(REPO / "docs" / "skills" / "ragraf-core", skill,
                    ignore=shutil.ignore_patterns("*.bak-*"))
    # Объявление своё: копия в дереве, все файлы identical. Так проверяется
    # поведение самого инструмента, а не состояние машины (второй копии у учётной
    # записи в песочнице нет и быть не должно).
    declaration = {
        "schema": 1,
        "why": "песочница набора: копия в дереве, режим identical",
        "source": "docs/skills/ragraf-core",
        "files": ["SKILL.md", "reference/operations.md", "reference/repair.md"],
        "source_rules": {
            "reference/operations.md": [{"kind": "numbers_match_code",
                                         "why": "числа операций и инструментов"}],
            "reference/repair.md": [{"kind": "numbers_match_code",
                                     "why": "baseline чекера"}],
            "SKILL.md": [{"kind": "bold_numbers_match_code",
                          "why": "только выделенные числа — утверждение"}],
        },
        "variants": [{
            "name": "sandbox",
            "runtime": "песочница набора",
            "copies": [{"root": "sandbox-copy/ragraf-core", "in_tree": True,
                        "why": "копия в дереве песочницы"}],
            "files": {
                "SKILL.md": {"mode": "identical"},
                "reference/operations.md": {"mode": "identical"},
                "reference/repair.md": {"mode": "identical"},
            },
        }],
    }
    (skill / "VARIANTS.json").write_text(
        json.dumps(declaration, ensure_ascii=False, indent=2), encoding="utf-8",
        newline="\n")
    copy = root / "sandbox-copy" / "ragraf-core"
    shutil.copytree(skill, copy)
    (copy / "VARIANTS.json").unlink()
    return skill


def run_checker(root: Path) -> tuple[int, str]:
    return run_checker_tool(root, root)


def run_checker_tool(tool_root: Path, check_root: Path) -> tuple[int, str]:
    """Инструмент берётся из одного дерева, проверяется другое (для --root)."""
    result = subprocess.run(
        [sys.executable, str(tool_root / "tools" / "check_skill_variants.py"),
         "--root", str(check_root)],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=600)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"нет якоря {old!r} в {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


NUMBER_PATTERNS = {
    "ops": re.compile(r"\*\*(\d+) операци\w*\*\*"),
    "tools": re.compile(r"\*\*(\d+) инструмент\w*\*\*"),
}


def declared_numbers(path: Path) -> tuple[int, int]:
    """Действующие числа из текста навыка: (операции, инструменты).

    Читаются из файла, а не зашиты литералом: число меняется вместе с кодом, и
    набор, помнящий 64 и 71, падал бы на верной правке — то есть охранял прошлое.
    """
    text = path.read_text(encoding="utf-8")
    found = []
    for kind in ("ops", "tools"):
        match = NUMBER_PATTERNS[kind].search(text)
        if match is None:
            raise SystemExit(f"нет выделенного числа «{kind}» в {path}")
        found.append(int(match.group(1)))
    return found[0], found[1]


def edit_number(path: Path, kind: str, replacement: str) -> None:
    """Подсадить другое значение на место выделенного числа (регуляркой)."""
    text = path.read_text(encoding="utf-8")
    pattern = NUMBER_PATTERNS[kind]
    if pattern.search(text) is None:
        raise SystemExit(f"нет выделенного числа «{kind}» в {path}")
    path.write_text(pattern.sub(replacement, text, count=1), encoding="utf-8",
                    newline="\n")


def mirror(skill: Path, root: Path, name: str) -> None:
    """Правку источника повторить в копии: иначе ловится расхождение, а не число."""
    shutil.copyfile(skill / name, root / "sandbox-copy" / "ragraf-core" / name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_skill_numbers_"))
    os.environ["RAGRAF_CATALOG_DIR"] = str(tmp / "catalog")
    (tmp / "catalog").mkdir()
    print("=" * 78)
    print("0. ПЕСОЧНИЦА: код для счёта, навык и объявление")
    print("=" * 78)
    skill = build_sandbox(tmp)
    ops = skill / "reference" / "operations.md"
    repair = skill / "reference" / "repair.md"
    skill_md = skill / "SKILL.md"
    ops_n, tools_n = declared_numbers(ops)
    # Число проверок чекера тоже читается из текста навыка: оно меняется вместе
    # с чекером, и набор, помнящий 45, падал бы на верной правке.
    checks_n = int(re.search(r"(\d+) проверок",
                             repair.read_text(encoding="utf-8")).group(1))
    pristine_ops = ops.read_bytes()
    pristine_skill = skill_md.read_bytes()
    print(f"  действующие числа навыка: операций {ops_n}, инструментов {tools_n}")

    code, output = run_checker(tmp)
    print(f"  прогон на песочнице: код {code}, осмотрено {output.strip().splitlines()[-1]}")
    check(code == 0, "чистая песочница проходит", output[-400:])
    check(f"число {ops_n} совпадает с кодом" in output,
          "операции сверены с кодом", output[-300:])
    check(f"число {tools_n} совпадает с кодом" in output,
          "инструменты сверены с кодом", output[-300:])
    check(f"число {checks_n} совпадает с кодом" in output,
          "число проверок в форме «проверок» тоже видно", output[-300:])
    check(f"действующее число {tools_n} совпадает" in output,
          "выделенные числа SKILL.md сверены", output[-300:])

    print()
    print("=" * 78)
    print("0.5. ФЛАГ --root ПЕРЕКЛЮЧАЕТ ПРОВЕРКУ НА ДРУГОЕ ДЕРЕВО")
    print("=" * 78)
    # Иначе `--root` мог бы не работать, а набор этого бы не заметил: он запускает
    # КОПИЮ инструмента из песочницы, и по умолчанию та проверяет саму песочницу.
    # Поэтому проверка запускается инструментом из первой песочницы на ВТОРОМ
    # дереве с испорченным числом: находка обязана прийти из второго.
    other = tmp / "other"
    other.mkdir()
    other_skill = build_sandbox(other)
    edit_number(other_skill / "reference" / "operations.md", "ops",
                "**39 операций**")
    mirror(other_skill, other, "reference/operations.md")
    code_other, output_other = run_checker_tool(tmp, other)
    check(code_other == 1, "испорченное число в другом дереве найдено через --root",
          output_other[-300:])
    check("39" in output_other, "находка относится к другому дереву, а не к песочнице",
          output_other[-300:])

    print()
    print("=" * 78)
    print("1. ПОДСАДКА: УСТАРЕВШЕЕ ЧИСЛО ОПЕРАЦИЙ")
    print("=" * 78)
    edit_number(ops, "ops", "**39 операций**")
    mirror(skill, tmp, "reference/operations.md")
    code, output = run_checker(tmp)
    check(code == 1, "устаревшее число ловится", output[-300:])
    check("39" in output and str(ops_n) in output,
          "находка называет заявленное и фактическое", output[-300:])

    print()
    print("=" * 78)
    print("2. ПОДСАДКА: ЧИСЛО ПРОВЕРОК В ФОРМЕ «ПРОВЕРОК»")
    print("=" * 78)
    ops.write_bytes(pristine_ops)
    mirror(skill, tmp, "reference/operations.md")
    edit(repair, f"{checks_n} проверок", "33 проверки")
    mirror(skill, tmp, "reference/repair.md")
    code, output = run_checker(tmp)
    check(code == 1, "устаревший baseline чекера ловится", output[-300:])
    check("33" in output and str(checks_n) in output,
          "находка называет оба числа", output[-300:])

    print()
    print("=" * 78)
    print("3. ПОДСАДКА: РАСХОЖДЕНИЕ КОПИИ С ИСТОЧНИКОМ")
    print("=" * 78)
    edit(repair, "33 проверки", f"{checks_n} проверок")
    mirror(skill, tmp, "reference/repair.md")
    copy_ops = tmp / "sandbox-copy" / "ragraf-core" / "reference" / "operations.md"
    edit_number(copy_ops, "ops", "**62 операции**")
    code, output = run_checker(tmp)
    check(code == 1, "правка в копии ловится", output[-300:])
    check("не совпадает с проектом побайтово" in output,
          "находка называет забытую синхронизацию", output[-300:])
    shutil.copyfile(skill / "reference" / "operations.md", copy_ops)

    print()
    print("=" * 78)
    print("4. УБРАННОЕ ЧИСЛО — ЭТО НАЗВАННОЕ ОТСУТСТВИЕ, А НЕ ТИХИЙ УСПЕХ")
    print("=" * 78)
    edit_number(ops, "ops", "набор операций")
    edit_number(ops, "tools", "набор")
    mirror(skill, tmp, "reference/operations.md")
    code, output = run_checker(tmp)
    check("числовых заявлений нет" in output or "правило проверять нечего" in output,
          "отсутствие числа названо словами, а не выдано за совпадение",
          output[-400:])
    check(code == 0, "при этом это не находка: проверять нечего — не то же, что "
                     "проверено", output[-200:])

    print()
    print("=" * 78)
    print("5. ИСТОРИЧЕСКИЕ ЧИСЛА SKILL.md НЕ СЧИТАЮТСЯ УТВЕРЖДЕНИЕМ")
    print("=" * 78)
    ops.write_bytes(pristine_ops)
    mirror(skill, tmp, "reference/operations.md")
    edit(skill_md, "Действующие числа", "Код объявлял 31 инструмент и 28 операций; "
                                        "документы называли 18, 16 и 21. Действующие числа")
    mirror(skill, tmp, "SKILL.md")
    code, output = run_checker(tmp)
    check(code == 0, "история рядом с действующим числом не роняет проверку",
          output[-400:])
    edit_number(skill_md, "tools", "**41 инструмент**")
    edit_number(skill_md, "ops", "**39 операций**")
    mirror(skill, tmp, "SKILL.md")
    code, output = run_checker(tmp)
    check(code == 1, "а неверное выделенное число — роняет", output[-300:])
    check("41" in output or "39" in output, "находка называет выделенное число",
          output[-300:])

    print()
    print("=" * 78)
    check(skill.exists(), "живое дерево не тронуто: правилась только песочница",
          "песочница удалена, живые файлы навыка не менялись")
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
