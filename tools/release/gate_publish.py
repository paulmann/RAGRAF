"""Гейт публикации: пять порч обязаны получить отказ.

Проверка, которая никогда не падала, не стоит ничего. Отпечаток сборки теперь лежит
ВНУТРИ дерева (`BUILD-STAMP.json`) и уходит в архив, поэтому подменять есть что:

  1. файл дерева изменён, отпечаток остался прежним — отказ по отпечатку;
  2. файл дерева изменён И отпечаток пересчитан — отказ сверки с источником
     (дерево разошлось с живым деревом, из которого собрано);
  3. подменён только отпечаток (его хэш не описывает дерево) — отказ по отпечатку;
  4. в отпечаток вписана ЧУЖАЯ голова таблицы — отказ по голове;
  5. файл удалён из архива — отказ сверки архива.

Запуск:
    python tools/release/gate_publish.py --tree <свежесобранное дерево> --clone <клон>
Без `--tree` дерево собирается заново (медленно, зато гейт самодостаточен).

ВАЖНО: гейт запускать на СВЕЖЕСОБРАННОМ дереве и не править дерево между сборкой и
гейтом — любой edit делает контрольный шаг красным (дерево разойдётся с источником).

Коды: 0 — все порчи пойманы, 1 — какая-то прошла (находка), 2 — окружение.
"""
from __future__ import annotations

import argparse
import json
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


def run_check(tree: pathlib.Path, clone: pathlib.Path, archive: pathlib.Path | None = None,
              version: str = "") -> tuple[int, str]:
    cmd = [sys.executable, str(ROOT / "tools/release/publish_release.py"), "--check",
           "--tree", str(tree), "--clone", str(clone)]
    if version:
        cmd += ["--version", version]
    if archive is not None:
        cmd += ["--archive", str(archive)]
    return pub.run(cmd, ROOT)


def copy_tree(source: pathlib.Path, target: pathlib.Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def victim_file(tree: pathlib.Path) -> pathlib.Path | None:
    for rel in pub.tree_files(tree):
        if rel.endswith(".py") and "tests/" not in rel and not rel.endswith(pub.STAMP_NAME):
            return tree / rel
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="гейт публикации: пять порч")
    parser.add_argument("--tree", default="", help="свежесобранное дерево")
    parser.add_argument("--clone", default=os.environ.get("RAGRAF_PUBLICATION_CLONE", ""))
    parser.add_argument("--version", default="", help="версия сборки (для сверки отпечатка)")
    args = parser.parse_args(argv)
    if not args.clone:
        print("ОКРУЖЕНИЕ: клон публикации не задан (--clone или RAGRAF_PUBLICATION_CLONE)")
        return 2
    clone = pathlib.Path(args.clone).resolve()

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        if args.tree:
            source = pathlib.Path(args.tree).resolve()
            if not pub.stamp_path(source).is_file():
                print(f"ОКРУЖЕНИЕ: в дереве нет {pub.STAMP_NAME}: {source}")
                return 2
        else:
            source = work / "built"
            code, text = pub.run([sys.executable, str(ROOT / "tools/release/build_release.py"),
                                  "--out", str(source)], ROOT)
            if code != 0:
                print(f"ОКРУЖЕНИЕ: сборка не удалась: {text.strip()[-200:]}")
                return 2
            pub.write_stamp(source, args.version)

        def control_ok(tree: pathlib.Path, label: str) -> bool:
            code, text = run_check(tree, clone, version=args.version)
            if code != 0:
                print(f"ОКРУЖЕНИЕ: контроль ({label}) не проходит — мерить нечего")
                print("         " + "\n         ".join(text.strip().splitlines()[-4:]))
                return False
            return True

        base = work / "control"
        copy_tree(source, base)
        if not control_ok(base, "чистая копия"):
            return 2
        print("  [ ok ] 0. контроль: чистая копия проходит все проверки")

        def report(number: int, label: str, ok: bool, code: int, text: str) -> None:
            nonlocal failures
            if ok:
                print(f"  [ ok ] {number}. {label}")
            else:
                print(f"  [FAIL] {number}. {label} (код {code})")
                tail = [line for line in text.strip().splitlines() if line.strip()][-1:]
                print("         " + (tail[0][:150] if tail else "нет вывода"))
                failures += 1

        # 1. Файл изменён, отпечаток прежний.
        tree1 = work / "trap1"
        copy_tree(base, tree1)
        victim = victim_file(tree1)
        if victim is None:
            print("ОКРУЖЕНИЕ: в сборке нет .py вне tests — подменять нечего")
            return 2
        victim.write_text(victim.read_text(encoding="utf-8") + "\n# подмена\n", encoding="utf-8")
        code, text = run_check(tree1, clone, version=args.version)
        report(1, "файл изменён, отпечаток прежний — отказ",
               code != 0 and ("дерево изменилось" in text or "отпечаток" in text), code, text)

        # 2. Файл изменён И отпечаток пересчитан.
        tree2 = work / "trap2"
        copy_tree(base, tree2)
        victim2 = victim_file(tree2)
        victim2.write_text(victim2.read_text(encoding="utf-8") + "\n# подмена-2\n", encoding="utf-8")
        pub.write_stamp(tree2, args.version)
        code, text = run_check(tree2, clone, version=args.version)
        report(2, "файл изменён и отпечаток пересчитан — отказ сверки с источником",
               code != 0 and ("не прошло сверку с источником" in text
                              or "не совпадает с источником" in text
                              or "нарушени" in text), code, text)

        # 3. Подменён только отпечаток.
        tree3 = work / "trap3"
        copy_tree(base, tree3)
        stamp = pub.stamp_path(tree3)
        document = json.loads(stamp.read_text(encoding="utf-8"))
        document["digest"] = "0" * 64
        stamp.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
        code, text = run_check(tree3, clone, version=args.version)
        report(3, "подменён только отпечаток — отказ по отпечатку",
               code != 0 and ("дерево изменилось" in text or "отпечаток" in text), code, text)

        # 4. В отпечаток вписана чужая голова таблицы.
        tree4 = work / "trap4"
        copy_tree(base, tree4)
        stamp4 = pub.stamp_path(tree4)
        document4 = json.loads(stamp4.read_text(encoding="utf-8"))
        document4["table_head"] = "1234567890abcdef1234567890abcdef12345678"
        stamp4.write_text(json.dumps(document4, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8", newline="\n")
        code, text = run_check(tree4, clone, version=args.version)
        report(4, "чужая голова в отпечатке — отказ по голове",
               code != 0 and ("голов" in text or "отпечаток" in text), code, text)

        # 5. Файл удалён из архива.
        tree5 = work / "trap5"
        copy_tree(base, tree5)
        archive = work / "trap5.zip"
        pub.build_archive(tree5, archive)
        with zipfile.ZipFile(archive) as bundle:
            keep = {name: bundle.read(name) for name in bundle.namelist()}
        drop = sorted(name for name in keep if name != pub.STAMP_NAME)[0]
        keep.pop(drop)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, data in keep.items():
                bundle.writestr(name, data)
        pathlib.Path(str(archive) + ".sha256").write_text(
            f"{pub.sha256(archive)}  {archive.name}\n", encoding="utf-8", newline="\n")
        code, text = run_check(tree5, clone, archive, version=args.version)
        report(5, f"файл удалён из архива ({drop}) — отказ сверки архива",
               code != 0 and ("в архиве нет файлов" in text or "не совпадает" in text), code, text)

    if failures:
        print(f"\nИТОГ: порч не поймано {failures} из 5 — публикация не защищена")
        return 1
    print("\nИТОГ: пять порч из пяти пойманы")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
