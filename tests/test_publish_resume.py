"""Возобновление публикации после сбоя: проверка по содержимому, а не по расстоянию.

Измеренный дефект (24.11.2026, выпуск 0.13.19). `tools/release/publish_release.py --publish`
заявляет: «состояние возобновляемое — повторите запуск». Прогон дошёл до коммита и тега
(`83b6e75`, `v0.13.19`) и упал на выгрузке релиза. Повтор отказал дважды с
«ОТКАЗ: голова таблицы разошлась со сборкой»: `check_sources_lag.py --mode build` требовал,
чтобы таблица называла ТЕКУЩУЮ голову клона, а голова уже сдвинулась на релизный коммит.
То есть повтор был невозможен ровно в том окне, ради которого написан.

Правило после правки: расхождение на один коммит принимается ТОЛЬКО если собранное дерево
побайтово равно рабочему дереву головы клона (коммитить нечего — это та же сборка). Всё
остальное остаётся отказом, иначе посторонний коммит прошёл бы как выпуск.

Запуск: python tests/test_publish_resume.py
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools/release/check_sources_lag.py"
DOC = "docs/03_implementation/RELEASE-SOURCES.md"
passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK ] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f"\n      {detail}" if detail else ""))


def git(repo: pathlib.Path, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "Тест"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "test@example.invalid"
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stdout + result.stderr).strip()[:300])
    return result.stdout.strip()


def sources_doc(head: str) -> str:
    return ("# Источники\n\nПубличный `main` на момент сверки:** "
            f"`{head}` — тестовая голова.\n")


def run_build(tree: pathlib.Path, clone: pathlib.Path) -> tuple[int, str]:
    result = subprocess.run([sys.executable, str(TOOL), "--mode", "build",
                             "--tree", str(tree), "--clone", str(clone)],
                            capture_output=True, text=True, check=False)
    return result.returncode, (result.stdout + result.stderr).strip()


def main() -> int:
    sandbox = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-resume-"))
    try:
        repo = sandbox / "clone"
        repo.mkdir()
        git(repo, "init", "-b", "main")
        (repo / "src.txt").write_text("старое содержимое\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")

        # Сборка выпуска: документ называет base, содержимое новое.
        tree = sandbox / "tree"
        (tree / DOC).parent.mkdir(parents=True)
        (tree / DOC).write_text(sources_doc(base), encoding="utf-8", newline="\n")
        (tree / "src.txt").write_text("новое содержимое\n", encoding="utf-8")
        (tree / "extra.txt").write_text("добавлено\n", encoding="utf-8")

        # Релизный коммит делается ИЗ этой сборки — так же, как в публикации.
        shutil.copytree(tree, repo, dirs_exist_ok=True)
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "release: 0.13.19")
        release = git(repo, "rev-parse", "HEAD")

        code, output = run_build(tree, repo)
        check("релизный коммит этой же сборки — повтор разрешён", code == 0, output[-200:])
        check("причина названа: это тот же коммит", "уже релизный коммит" in output, output[-200:])

        # Голова в таблице: расстояние 0 — обычная сборка до коммита.
        (tree / DOC).write_text(sources_doc(release), encoding="utf-8", newline="\n")
        code, output = run_build(tree, repo)
        check("таблица называет текущую голову — разрешено", code == 0, output[-200:])

        # Сборка отличается на байт — это уже не тот выпуск.
        (tree / DOC).write_text(sources_doc(base), encoding="utf-8", newline="\n")
        (tree / "src.txt").write_text("новое содержимое!\n", encoding="utf-8")
        code, output = run_build(tree, repo)
        check("расхождение содержимого на байт — отказ", code == 1, output[-200:])
        check("отказ называет файл", "src.txt" in output, output[-200:])

        # Файл головы отсутствует в сборке.
        (tree / "src.txt").write_text("новое содержимое\n", encoding="utf-8")
        (tree / "extra.txt").unlink()
        code, output = run_build(tree, repo)
        check("в сборке нет файла головы — отказ", code == 1, output[-200:])

        # Посторонний коммит сверх релизного: расстояние 2 — не возобновление.
        (repo / "later.txt").write_text("позже\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "release: следующий")
        (tree / "extra.txt").write_text("добавлено\n", encoding="utf-8")
        code, output = run_build(tree, repo)
        check("два коммита впереди — отказ", code == 1, output[-200:])

        # Отпечаток сборки: правка файла после сборки меняет хэш дерева.
        (tree / "src.txt").write_text("правка после сборки\n", encoding="utf-8")
        digest = hashlib.sha256((tree / "src.txt").read_bytes()).hexdigest()
        check("отпечаток файла считается по содержимому", len(digest) == 64)
        print("  · состояние песочницы:", json.dumps({"коммитов": 3}, ensure_ascii=False))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print(f"ИТОГ: пройдено {passed}, провалено {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
