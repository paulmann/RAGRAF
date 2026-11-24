"""Отставание таблицы источников: проверка, а не правило в документе.

Что проверяется. `docs/03_implementation/RELEASE-SOURCES.md` называет голову
публикации, на момент которой сверялись хэши. Эта голова всегда — ПРЕДЫДУЩИЙ
выпуск: релизный коммит не может назвать сам себя. Поэтому:

  * режим `build` (то, что делает гейт) — таблица в собранном дереве обязана
    называть ТЕКУЩУЮ голову клона публикации: релизный коммит станет следующим,
    и после него разница будет ровно один;
  * режим `published` (проверка после публикации) — разница между головой в
    таблице и тегом обязана быть ровно один коммит.

Измеренный дефект, из которого это выросло (22.11.2026): пересчёт таблицы был
закоммичен отдельным коммитом `6102b4f`, и в архиве 0.13.5 голова `7feec91`
оказалась от тега `b599602` на ДВА коммита. Правило «так делать нельзя» лежало в
документе и не сработало; проверка считает коммиты и отказывает.

Запуск:
    python tools/release/check_sources_lag.py --mode build --tree <собранное дерево>
    python tools/release/check_sources_lag.py --mode published --tree <дерево> --tag v0.13.6

Коды: 0 — как надо, 1 — расхождение (названо числом и коммитами), 2 — окружение
(нет клона, нет тега, нет таблицы) — это тоже отказ, но с другой причиной.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

SOURCES_DOC = "docs/03_implementation/RELEASE-SOURCES.md"
HEAD_RE = re.compile(r"Публичный `main` на момент сверки:\*\*\s*`([0-9a-f]{40})`")
VERSION_RE = re.compile(r'__version__\s*=\s*"([0-9][^"]+)"')


def git(clone: pathlib.Path, *args: str) -> tuple[int, str]:
    completed = subprocess.run(["git", "-C", str(clone), *args],
                               capture_output=True, text=True, check=False)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def tree_equals_head(clone: pathlib.Path, tree: pathlib.Path) -> tuple[bool, str]:
    """Собранное дерево побайтово равно рабочему дереву головы клона.

    Нужно ровно для одного случая: релизный коммит уже сделан из этой самой сборки, и
    повтор публикации после сбоя на загрузке не должен отказывать. Сравниваются только
    файлы, которые клон отслеживает: посторонние файлы в сборке проверяет verify_release,
    а здесь важно, что коммитить нечего.
    """
    import hashlib

    listed = subprocess.run(["git", "-C", str(clone), "ls-tree", "-r", "--name-only", "-z", "HEAD"],
                            capture_output=True, text=True, check=False)
    if listed.returncode != 0:
        return False, "git ls-tree не отработал"
    tracked = [item for item in listed.stdout.split("\0") if item]
    if not tracked:
        return False, "в голове клона нет файлов"
    built = {item.relative_to(tree).as_posix() for item in tree.rglob("*") if item.is_file()}
    missing = [item for item in tracked if item not in built]
    if missing:
        return False, f"в сборке нет {len(missing)} файлов головы, например {missing[0]}"
    for relative in tracked:
        try:
            left = hashlib.sha256((clone / relative).read_bytes()).hexdigest()
            right = hashlib.sha256((tree / relative).read_bytes()).hexdigest()
        except OSError as error:
            return False, f"файл не прочитан: {relative} ({error.__class__.__name__})"
        if left != right:
            return False, f"содержимое расходится: {relative}"
    return True, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="отставание таблицы источников от головы публикации")
    parser.add_argument("--mode", choices=["build", "published"], default="build")
    parser.add_argument("--tree", required=True, help="собранное дерево (или корень живого дерева)")
    parser.add_argument("--clone", default="",
                        help="клон публикации; по умолчанию берётся из RAGRAF_PUBLICATION_CLONE")
    parser.add_argument("--tag", default="", help="тег выпуска (для mode=published); по умолчанию — из версии")
    parser.add_argument("--source", default="", help="корень живого дерева (для версии в build-режиме)")
    args = parser.parse_args(argv)

    tree = pathlib.Path(args.tree).resolve()
    # Путь к клону НЕ вписан в код: в поставке не должно быть путей машины автора
    # (инвариант 24), а проверка без клона бессмысленна — поэтому имя задаётся
    # окружением, и его отсутствие — отдельный отказ с причиной.
    clone_arg = args.clone.strip() or os.environ.get("RAGRAF_PUBLICATION_CLONE", "").strip()
    if clone_arg == "":
        print("ОКРУЖЕНИЕ: клон публикации не задан (--clone или RAGRAF_PUBLICATION_CLONE)")
        return 2
    clone = pathlib.Path(clone_arg).resolve()

    doc = tree / SOURCES_DOC
    if not doc.is_file():
        print(f"ОКРУЖЕНИЕ: нет {SOURCES_DOC} в {tree}")
        return 2
    found = HEAD_RE.search(doc.read_text(encoding="utf-8"))
    if not found:
        print(f"ОТКАЗ: {SOURCES_DOC} не называет голову публикации")
        return 1
    named = found.group(1)

    if not clone.is_dir():
        print(f"ОКРУЖЕНИЕ: нет клона публикации {clone}")
        return 2
    code, _ = git(clone, "cat-file", "-e", f"{named}^{{commit}}")
    if code != 0:
        print(f"ОКРУЖЕНИЕ: головы {named[:7]} нет в клоне {clone} (git fetch?)")
        return 2

    _, head_output = git(clone, "rev-parse", "HEAD")
    head = head_output.strip().splitlines()[0] if head_output.strip() else ""

    if args.mode == "build":
        count_code, count_output = git(clone, "rev-list", "--count", f"{named}..HEAD")
        if count_code != 0:
            print(f"ОКРУЖЕНИЕ: git rev-list не отработал: {count_output.strip()[:200]}")
            return 2
        distance = int(count_output.strip() or "0")
        if distance == 0:
            print(f"OK: таблица называет текущую голову {named[:7]}; релизный коммит станет следующим")
            return 0
        if distance == 1:
            # Возобновление после сбоя: коммит уже сделан из ЭТОЙ сборки. Признак — не
            # расстояние, а содержимое: иначе любой посторонний коммит прошёл бы как выпуск.
            same, why = tree_equals_head(clone, tree)
            if same:
                print(f"OK: голова {head[:7]} — уже релизный коммит этой сборки; "
                      f"повтор продолжит с тега и релиза")
                return 0
            print(f"ОТКАЗ: голова {head[:7]} на один коммит впереди {named[:7]}, "
                  f"но это не эта сборка: {why}")
            return 1
        _, log = git(clone, "log", "--oneline", f"{named}..HEAD")
        print(f"ОТКАЗ: таблица называет {named[:7]}, а голова клона {head[:7]} — "
              f"{distance} коммит(ов) разницы. В архиве будет не та голова: "
              f"пересчитайте таблицу в рабочем дереве и не коммитьте её отдельно")
        for line in log.strip().splitlines()[:5]:
            print(f"  · {line}")
        return 1

    tag = args.tag.strip()
    if tag == "":
        version_source = pathlib.Path(args.source).resolve() if args.source else tree
        init = version_source / "src" / "ragraf" / "__init__.py"
        version = VERSION_RE.search(init.read_text(encoding="utf-8")) if init.is_file() else None
        if not version:
            print("ОКРУЖЕНИЕ: тег не задан и версия не прочитана")
            return 2
        tag = "v" + version.group(1)
    code, tag_output = git(clone, "rev-parse", f"{tag}^{{commit}}")
    if code != 0:
        print(f"ОКРУЖЕНИЕ: тега {tag} нет в клоне (публикация не сделана?)")
        return 2
    tag_commit = tag_output.strip().splitlines()[0]
    _, count_output = git(clone, "rev-list", "--count", f"{named}..{tag_commit}")
    distance = int(count_output.strip() or "0")
    if distance == 1:
        print(f"OK: голова в таблице {named[:7]}, тег {tag} — ровно один коммит")
        return 0
    _, log = git(clone, "log", "--oneline", f"{named}..{tag_commit}")
    print(f"ОТКАЗ: голова в таблице {named[:7]}, тег {tag} ({tag_commit[:7]}) — "
          f"{distance} коммит(ов) вместо одного")
    for line in log.strip().splitlines()[:5]:
        print(f"  · {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
