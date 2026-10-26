"""Каждый файл поставки обязан быть виден git: иначе публикация теряет его молча.

Измеренный дефект (26.10.2026, публикация 0.3.1). `tests/fixtures/proj/config/
app.local.php` входил в сборку поставки, но правило `.gitignore` (`config/`)
не пускало его в коммит. Сборщик переносил файл, проверяльщик его видел, а
опубликованное дерево отличалось от проверенного на один файл.

Вторая измеренная ошибка — уже этой проверки: `git check-ignore --no-index`
ТРЕБУЕТ репозиторий, а живое дерево репозиторием не является. Первая редакция
молча получала пустой ответ и объявляла «скрыто от git: 0» — ложная зелень.
Поэтому теперь:

  * берётся ВРЕМЕННЫЙ каталог хранилища (`GIT_DIR`) и рабочее дерево `GIT_WORK_TREE`
    указывает на проверяемое. `.git` в самом дереве не создаётся: проверка ничего
    не меняет;
  * `--no-index` — файлы не добавляются и не требуют индекса;
  * отсутствие git или его ошибка дают код 2 («не проверено»), а не «нарушений нет».

Свойство формулируется без имён файлов: состав поставки берётся из манифеста.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"C:\AutoClaw\RAGRAF")
sys.path.insert(0, str(REPO / "tools/release"))


class GitUnavailable(RuntimeError):
    """git не смог дать ответ: это «не проверено», а не «нарушений нет»."""


def ignored_by_gitignore(paths: list[str], root: Path) -> dict[str, str]:
    """Путь → правило .gitignore (файл:строка), которым он игнорируется."""
    if not (root / ".gitignore").is_file() or not paths:
        return {}
    if shutil.which("git") is None:
        raise GitUnavailable("git не найден в PATH")
    store = Path(tempfile.mkdtemp(prefix="ragraf-gitdir-"))
    env = dict(os.environ)
    # Хранилище пустое и ВНЕ проверяемого дерева: в нём нет ни объекта, ни индекса.
    env.update({"GIT_DIR": str(store / "git"), "GIT_WORK_TREE": str(root),
                "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(store)})
    try:
        init = subprocess.run(["git", "init", "-q", "--bare=False"], cwd=str(root),
                              env=env, capture_output=True, text=True)
        if init.returncode != 0:
            init = subprocess.run(["git", "init", "-q"], cwd=str(root),
                                  env=env, capture_output=True, text=True)
        if init.returncode != 0:
            raise GitUnavailable(f"git init не удался: {init.stderr.strip()[:200]}")
        # ВАЖНО: ввод подаётся БАЙТАМИ. В текстовом режиме Python на Windows
        # превращает "\n" в CRLF, git получает пути с завершающим \r и не
        # находит совпадений — проверка становится ложно-зелёной (измерено:
        # `*.local.php` при CR в пути → «не игнорируется»).
        proc = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-v"],
                              cwd=str(root), env=env,
                              input=("\n".join(paths) + "\n").encode("utf-8"),
                              capture_output=True)
        stdout = proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
        # Код 1 — «ничего не игнорируется»; 0 — есть совпадения; прочее — ошибка.
        if proc.returncode not in (0, 1):
            raise GitUnavailable(f"git check-ignore код {proc.returncode}: "
                                 f"{stderr.strip()[:200]}")
        result: dict[str, str] = {}
        for line in stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            source, name = parts[0].strip(), parts[1].strip()
            # Формат `-v`: «файл:строка:шаблон». Шаблон с ведущим `!` — это
            # ОБРАТНОЕ правило: файл им возвращён, а не исключён. check-ignore
            # печатает последнее совпавшее правило, поэтому `!` означает «виден».
            triple = source.split(":", 2)
            if len(triple) == 3 and triple[2].startswith("!"):
                continue
            location = ":".join(triple[:2])
            result[name] = location
        return result
    finally:
        shutil.rmtree(store, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--manifest", default="")
    # --root нужен песочнице: проверка обязана быть проверяемой на подставном
    # дереве, иначе её нельзя ни доказать, ни опровергнуть.
    parser.add_argument("--root", default=str(REPO))
    args = parser.parse_args()

    root = Path(args.root)
    import relcommon as rel  # noqa: PLC0415 - путь добавляется выше

    manifest_path = root / (args.manifest or "tools/release/manifest.toml")
    manifest = rel.load_manifest(manifest_path, root)
    plan = rel.collect(root, manifest)
    delivered = sorted(plan.files.keys())

    try:
        ignored = ignored_by_gitignore(delivered, root)
    except GitUnavailable as error:
        payload = {"manifest": manifest_path.relative_to(root).as_posix(),
                   "delivered": len(delivered), "ignored": None,
                   "failures": [], "error": str(error)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"НЕ ПРОВЕРЕНО: {error}")
        return 2

    failures = [{"path": name, "rule": where} for name, where in sorted(ignored.items())]
    payload = {
        "manifest": manifest_path.relative_to(root).as_posix(),
        "delivered": len(delivered),
        "ignored": len(ignored),
        "failures": failures,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"поставка по {payload['manifest']}: файлов {payload['delivered']}")
        for item in failures:
            print(f"  ИГНОРИРУЕТСЯ git: {item['path']} ← правило {item['rule']}")
        print(f"скрыто от git: {payload['ignored']}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
