"""Свойство блока 27: публикация без архива не трогает публикацию.

Измерено 24.11.2026 на выпуске 0.13.19: `--publish` без `--archive` сделал коммит `83b6e75`,
поставил и запушил тег `v0.13.19`, и только потом выяснилось, что выкладывать нечего —
выгрузка релиза молча пропускалась. Наружу ушёл тег без релиза.

Проверка идёт на песочнице: git-репозиторий «клон публикации» и каталог «сборки». Инструмент
обязан отказать ДО первого шага, который меняет состояние, назвать причину и вернуть код 2
(окружение), оставив голову и теги как были.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools/release/publish_release.py"
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


def publish(tree: pathlib.Path, clone: pathlib.Path, *extra: str) -> tuple[int, str]:
    command = [sys.executable, str(TOOL), "--publish", "--tree", str(tree),
               "--clone", str(clone), "--version", "0.13.20", *extra]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode, (result.stdout + result.stderr).strip()


def main() -> int:
    sandbox = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-archive-guard-"))
    try:
        clone = sandbox / "clone"
        clone.mkdir()
        git(clone, "init", "-b", "main")
        (clone / "src.txt").write_text("публикация\n", encoding="utf-8")
        git(clone, "add", "-A")
        git(clone, "commit", "-m", "base")
        head_before = git(clone, "rev-parse", "HEAD")
        tags_before = git(clone, "tag", "--list")

        tree = sandbox / "tree"
        tree.mkdir()
        (tree / "src.txt").write_text("публикация\n", encoding="utf-8")
        (tree / "new.txt").write_text("новый файл\n", encoding="utf-8")

        code, output = publish(tree, clone)
        check("без --archive — отказ окружения (код 2)", code == 2, f"код {code}: {output[-200:]}")
        check("отказ называет причину", "без --archive" in output, output[-200:])
        check("голова клона не сдвинулась", git(clone, "rev-parse", "HEAD") == head_before)
        check("файл сборки в клон не скопирован", not (clone / "new.txt").exists())
        check("тегов не появилось", git(clone, "tag", "--list") == tags_before)

        # Архив задан, но его нет: тот же отказ до первого шага.
        code, output = publish(tree, clone, "--archive", str(sandbox / "нет-такого.zip"))
        check("несуществующий архив — отказ окружения", code == 2, f"код {code}: {output[-200:]}")
        check("причина названа: архива нет", "архива нет" in output, output[-200:])
        check("голова всё ещё та же", git(clone, "rev-parse", "HEAD") == head_before)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print(f"ИТОГ: пройдено {passed}, провалено {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
