"""Гейт публикации: три порчи обязаны получить отказ.

Проверка, которая никогда не падала, не стоит ничего. Здесь три подмены, каждая из
которых раньше прошла бы молча:

  1. ДЕРЕВО ПОДМЕНЕНО после того, как его проверили (между `--stage` и публикацией).
     Отказ по отпечатку сборки (`<дерево>.stamp.json`).
  2. ФАЙЛ УДАЛЁН ИЗ АРХИВА при том, что дерево цело. Отказ по сверке архива с деревом.
  3. ГОЛОВА ТАБЛИЦЫ ПОДМЕНЕНА после сборки. Отказ `check_sources_lag --mode build`.

Запуск:
    python tools/release/gate_publish.py --tree <проверенное дерево> --clone <клон>
Без `--tree` дерево собирается заново (медленно, зато гейт самодостаточен).

Коды: 0 — все три порчи пойманы, 1 — какая-то прошла (находка), 2 — окружение.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "release"))
import publish_release as pub  # noqa: E402


def run_check(tree: pathlib.Path, clone: pathlib.Path, archive: pathlib.Path | None = None) -> tuple[int, str]:
    cmd = [sys.executable, str(ROOT / "tools/release/publish_release.py"), "--check",
           "--tree", str(tree), "--clone", str(clone)]
    if archive is not None:
        cmd += ["--archive", str(archive)]
    return pub.run(cmd, ROOT)


def copy_tree(source: pathlib.Path, target: pathlib.Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    stamp = pub.stamp_path(source)
    if stamp.is_file():
        pub.stamp_path(target).write_text(stamp.read_text(encoding="utf-8"), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="гейт публикации: три порчи")
    parser.add_argument("--tree", default="", help="проверенное дерево сборки")
    parser.add_argument("--clone", default=os.environ.get("RAGRAF_PUBLICATION_CLONE", ""))
    args = parser.parse_args(argv)
    if not args.clone:
        print("ОКРУЖЕНИЕ: клон публикации не задан (--clone или RAGRAF_PUBLICATION_CLONE)")
        return 2
    clone = pathlib.Path(args.clone).resolve()

    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        if args.tree:
            source = pathlib.Path(args.tree).resolve()
            if not pub.stamp_path(source).is_file():
                print(f"ОКРУЖЕНИЕ: у дерева нет отпечатка сборки: {pub.stamp_path(source)}")
                return 2
        else:
            source = work / "built"
            code, text = pub.run([sys.executable, str(ROOT / "tools/release/build_release.py"),
                                  "--out", str(source)], ROOT)
            if code != 0:
                print(f"ОКРУЖЕНИЕ: сборка не удалась: {text.strip()[-200:]}")
                return 2
            pub.write_stamp(source)

        failures = 0

        # --- контроль: чистая копия обязана проходить ----------------------
        control = work / "control"
        copy_tree(source, control)
        pub.write_stamp(control)
        code, text = run_check(control, clone)
        if code != 0:
            print("ОКРУЖЕНИЕ: чистая копия сборки не проходит проверку — мерить нечего")
            print("         " + "\n         ".join(text.strip().splitlines()[-4:]))
            return 2
        print("  [ ok ] 0. контроль: чистая копия проходит все проверки")

        # --- порча 1: дерево подменено после проверки ----------------------
        first = work / "trap1"
        copy_tree(source, first)
        victim = next((first / rel for rel in pub.tree_files(first)
                       if rel.endswith(".py") and "tests/" not in rel), None)
        if victim is None:
            print("ОКРУЖЕНИЕ: в сборке нет .py вне tests — подменять нечего")
            return 2
        victim.write_text(victim.read_text(encoding="utf-8") + "\n# подмена\n", encoding="utf-8")
        code, text = run_check(first, clone)
        if code != 0 and "дерево изменилось после проверки" in text:
            print("  [ ok ] 1. дерево подменено после проверки — отказ по отпечатку")
        else:
            print(f"  [FAIL] 1. подмена дерева не поймана (код {code})")
            print("         " + text.strip().splitlines()[-1][:150])
            failures += 1

        # --- порча 2: файл удалён из архива --------------------------------
        second = work / "trap2"
        copy_tree(source, second)
        pub.write_stamp(second)
        archive = work / "trap2.zip"
        pub.build_archive(second, archive)
        with zipfile.ZipFile(archive) as bundle:
            keep = {name: bundle.read(name) for name in bundle.namelist()}
        drop = sorted(keep)[0]
        keep.pop(drop)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, data in keep.items():
                bundle.writestr(name, data)
        pathlib.Path(str(archive) + ".sha256").write_text(
            f"{pub.sha256(archive)}  {archive.name}\n", encoding="utf-8", newline="\n")
        code, text = run_check(second, clone, archive)
        if code != 0 and ("в архиве нет файлов" in text or "не совпадает" in text):
            print(f"  [ ok ] 2. файл удалён из архива ({drop}) — отказ по сверке архива")
        else:
            print(f"  [FAIL] 2. усечённый архив принят (код {code})")
            print("         " + text.strip().splitlines()[-1][:150])
            failures += 1

        # --- порча 3: голова таблицы подменена после сборки ----------------
        third = work / "trap3"
        copy_tree(source, third)
        doc = third / pub.SOURCES_DOC
        text = doc.read_text(encoding="utf-8")
        fake = "1234567890abcdef1234567890abcdef12345678"
        patched = re.sub(r"(Публичный `main` на момент сверки:\*\*\s*`)[0-9a-f]{40}(`)",
                         lambda m: f"{m.group(1)}{fake}{m.group(2)}", text, count=1)
        if patched == text:
            print("ОКРУЖЕНИЕ: строка головы в таблице не найдена")
            return 2
        doc.write_text(patched, encoding="utf-8", newline="\n")
        # Отпечаток пересчитан намеренно: порча проверяет СВЕРКУ содержимого и голову,
        # а не защиту от подмены дерева (её ловит порча 1). Первым срабатывает правило
        # защиту от подмены дерева (её ловит порча 1).
        pub.write_stamp(third)
        code, text = run_check(third, clone)
        if code != 0 and ("голов" in text or "таблиц" in text or "[sources]" in text
                       or "отстава" in text or "[hash]" in text
                       or "не прошло сверку с источником" in text
                       or "не совпадает с источником" in text):
            print("  [ ok ] 3. голова таблицы подменена — отказ сверки (правило sources или отставание)")
        else:
            print(f"  [FAIL] 3. подмена головы не поймана (код {code})")
            print("         " + text.strip().splitlines()[-1][:150])
            failures += 1

    if failures:
        print(f"\nИТОГ: порч не поймано {failures} из 3 — публикация не защищена")
        return 1
    print("\nИТОГ: три порчи из трёх пойманы")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
