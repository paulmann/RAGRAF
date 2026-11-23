"""Публикация выпуска одним проходом: таблица источников, сборка, тег, релиз.

ЗАЧЕМ. Измеренный дефект 22.11.2026: таблица источников пересчитывалась ОТДЕЛЬНЫМ
коммитом после релиза, и в архиве 0.13.5 её голова оказалась на два коммита позади
тега (`check_sources_lag --mode published` отказывает именно на этом). Правило
«так нельзя» лежало в документе и не сработало; теперь пересчёт — шаг самого
инструмента публикации, а не действие человека.

ПОРЯДОК (и почему он такой):
  1. `--stage` — голова клона публикации H становится головой таблицы; хэши
     столбца сверки пересчитываются из живых файлов; дерево собирается по манифесту;
     проверяются `verify_release` (9 правил) и `check_sources_lag --mode build`
     (таблица называет ТЕКУЩУЮ голову клона — релизный коммит станет следующим).
  2. коммит в клоне несёт и дерево, и таблицу: значит после тега разница
     «голова таблицы → тег» равна ровно одному коммиту.
  3. `--publish` — коммит от имени владельца, тег, push, архив в релиз, и проверка
     `--mode published` обязана сказать «ровно один». Иначе выпуск назван
     несостоявшимся, а не «почти готовым».

Запуск:
    python tools/release/publish_release.py --stage --out <каталог сборки> \
        --clone <клон публикации> --version 0.13.13
    python tools/release/publish_release.py --publish --tree <каталог сборки> \
        --clone <клон публикации> --version 0.13.13 --notes-file <файл>
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES_DOC = "docs/03_implementation/RELEASE-SOURCES.md"
ANY_SHA = re.compile(r"\b[0-9a-f]{40}\b")
ROW = re.compile(r"^\|\s*([A-Za-z0-9_./\-]+)\s*\|\s*\S+\s*\|\s*([0-9a-f]{12,64})?\s*\|")


def run(cmd: list[str], cwd: pathlib.Path | None = None) -> tuple[int, str]:
    completed = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def git(clone: pathlib.Path, *args: str) -> str:
    code, out = run(["git", "-C", str(clone), *args])
    if code != 0:
        raise SystemExit(f"git {' '.join(args)}: {out.strip()[:300]}")
    return out.strip()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rewrite_sources(root: pathlib.Path, head: str, date: str) -> int:
    """Голова сверки и адреса строк таблицы — на голову публикации."""
    path = root / SOURCES_DOC
    text = path.read_text(encoding="utf-8")
    before = text
    text = re.sub(r"(Публичный `main` на момент сверки:\*\*\s*`)[0-9a-f]{40}(`)",
                  lambda m: f"{m.group(1)}{head}{m.group(2)}", text)
    text = ANY_SHA.sub(head, text)
    changed = 0 if text == before else 1
    # Дата сверки в той же строке: без неё таблица выглядит свежей, когда это не так.
    text = re.sub(r"(сверки:\*\*\s*`[0-9a-f]{40}`\s*\n\()(\d{4}-\d{2}-\d{2})",
                  lambda m: f"{m.group(1)}{date}", text)
    path.write_text(text, encoding="utf-8", newline="\n")
    return changed


def refresh_hashes(root: pathlib.Path, clone: pathlib.Path, head: str) -> int:
    """Столбцы сверки: хэш публикации — из клона, хэш дерева — из живого файла.

    Строка таблицы:
        | файл в дереве | источник (URL+коммит) | sha256 в публичном main |
          sha256 в дереве | совпадает | путь в поставке |
    «Публичный main» — факт внешний, поэтому читается из клона на голове `head`,
    а не из живого файла: иначе проверяльщик ловит ложное «совпадает» (замер
    23.11.2026: семь строк с живым хэшем в столбце публикации).
    """
    doc = root / SOURCES_DOC
    lines = doc.read_text(encoding="utf-8").splitlines()
    updated = 0
    for index, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not cells[1].startswith("http"):
            continue
        source = root / cells[0]
        if not source.is_file():
            continue
        published_path = cells[5] or cells[0].split("/", 1)[-1]
        raw = subprocess.run(["git", "-C", str(clone), "cat-file", "blob",
                              f"{head}:{published_path}"], capture_output=True)
        tree_hash = sha256(source)
        main_hash = hashlib.sha256(raw.stdout).hexdigest() if raw.returncode == 0 else ""
        cells[2] = main_hash or cells[2]
        cells[3] = tree_hash
        cells[4] = "да" if main_hash and main_hash == tree_hash else "нет"
        lines[index] = "| " + " | ".join(cells) + " |"
        updated += 1
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return updated


def stage(args: argparse.Namespace) -> int:
    clone = pathlib.Path(args.clone).resolve()
    head = git(clone, "rev-parse", "origin/main")
    print(f"голова публикации: {head[:7]}")
    changed = rewrite_sources(ROOT, head, args.date)
    hashes = refresh_hashes(ROOT, clone, head)
    print(f"таблица источников: голова переписана ({changed}), хэшей обновлено: {hashes}")

    out = pathlib.Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    code, text = run([sys.executable, str(ROOT / "tools/release/build_release.py"), "--out", str(out)], ROOT)
    print(text.strip().splitlines()[-1] if text.strip() else "")
    if code != 0:
        print("ОТКАЗ: сборка не удалась")
        return 1

    code, text = run([sys.executable, str(ROOT / "tools/release/verify_release.py"), "--tree", str(out)], ROOT)
    print("\n".join(text.strip().splitlines()[-4:]))
    if code != 0:
        print("ОТКАЗ: сверка собранного с источником не прошла")
        return 1

    code, text = run([sys.executable, str(ROOT / "tools/release/check_sources_lag.py"),
                      "--mode", "build", "--tree", str(out), "--clone", str(clone)], ROOT)
    print(text.strip().splitlines()[-1] if text.strip() else "")
    if code != 0:
        print("ОТКАЗ: таблица называет не ту голову — публикация дала бы расхождение")
        return 1
    print(f"ГОТОВО к публикации: {out}")
    return 0


def publish(args: argparse.Namespace) -> int:
    clone = pathlib.Path(args.clone).resolve()
    tree = pathlib.Path(args.tree).resolve()
    version = args.version
    tag = f"v{version}"

    built: dict[str, pathlib.Path] = {}
    for base, dirs, names in os.walk(tree):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            full = pathlib.Path(base) / name
            built[full.relative_to(tree).as_posix()] = full
    tracked = set(git(clone, "ls-files").splitlines())
    add = sorted(set(built) - tracked)
    remove = sorted(tracked - set(built))
    change = [rel for rel in sorted(set(built) & tracked)
              if sha256(built[rel]) != sha256(clone / rel)]
    print(f"в сборке {len(built)}; добавить {len(add)}, изменить {len(change)}, удалить {len(remove)}")
    if remove:
        print("ОТКАЗ: сборка убирает файлы из публикации — это решение владельца, а не шаг инструмента:")
        for rel in remove[:10]:
            print(f"  - {rel}")
        return 1
    for rel in add + change:
        target = clone / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built[rel], target)

    env = {k: v for k, v in os.environ.items()
           if k not in {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}}
    env["GIT_TERMINAL_PROMPT"] = "0"
    message = args.message or f"release: {version}"
    identity = ["-c", "user.name=Mikhail Deynekin", "-c", "user.email=Mikhail@Deynekin.com"]
    for cmd in (["git", *identity, "add", "-u"],):
        subprocess.run(cmd, cwd=str(clone), check=True, env=env)
    if add:
        subprocess.run(["git", *identity, "add", "--"] + add, cwd=str(clone), check=True, env=env)
    commit = subprocess.run(["git", *identity, "commit", "-m", message], cwd=str(clone),
                            capture_output=True, text=True, env=env)
    if commit.returncode != 0:
        print(f"ОТКАЗ: коммит не сделан: {(commit.stdout + commit.stderr).strip()[:300]}")
        return 1
    subprocess.run(["git", "tag", "-f", "-a", tag, "-m", f"RAGRAF {version}"], cwd=str(clone),
                   check=True, env=env)
    for cmd in (["git", "push", "origin", "main"], ["git", "push", "--force", "origin", tag]):
        result = subprocess.run(cmd, cwd=str(clone), capture_output=True, text=True, env=env)
        print((result.stdout + result.stderr).strip().splitlines()[-1] if (result.stdout + result.stderr).strip() else "")
        if result.returncode != 0:
            print(f"ОТКАЗ: {cmd[1]} {cmd[2] if len(cmd) > 2 else ''} не удался")
            return 1

    archive = pathlib.Path(args.archive).resolve() if args.archive else None
    if archive and archive.is_file():
        subprocess.run(["gh", "release", "create", tag, str(archive),
                        str(archive) + ".sha256",
                        "--title", f"RAGRAF {version}",
                        "--notes-file", args.notes_file], cwd=str(clone), check=True, env=env)

    code, text = run([sys.executable, str(ROOT / "tools/release/check_sources_lag.py"),
                      "--mode", "published", "--tree", str(tree), "--clone", str(clone),
                      "--tag", tag], ROOT)
    print(text.strip().splitlines()[-1] if text.strip() else "")
    if code != 0:
        print("ОТКАЗ: после публикации таблица и тег расходятся — выпуск назван несостоявшимся")
        return 1
    print(f"ОПУБЛИКОВАНО: {tag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="публикация выпуска: таблица, сборка, тег, релиз")
    parser.add_argument("--stage", action="store_true", help="пересчитать таблицу и собрать дерево")
    parser.add_argument("--publish", action="store_true", help="закоммитить, поставить тег, выложить релиз")
    parser.add_argument("--out", default="", help="каталог сборки (для --stage)")
    parser.add_argument("--tree", default="", help="собранное дерево (для --publish)")
    parser.add_argument("--clone", default=os.environ.get("RAGRAF_PUBLICATION_CLONE", ""),
                        help="клон публикации (или RAGRAF_PUBLICATION_CLONE)")
    parser.add_argument("--version", default="", help="версия выпуска, например 0.13.13")
    parser.add_argument("--date", default="", help="дата сверки (по умолчанию сегодня)")
    parser.add_argument("--message", default="", help="сообщение коммита")
    parser.add_argument("--archive", default="", help="zip-архив для релиза")
    parser.add_argument("--notes-file", default="", help="файл заметок релиза")
    args = parser.parse_args(argv)
    if args.clone == "":
        print("ОКРУЖЕНИЕ: клон публикации не задан (--clone или RAGRAF_PUBLICATION_CLONE)")
        return 2
    if not args.date:
        import datetime
        args.date = datetime.date.today().isoformat()
    if args.stage:
        if not args.out:
            print("ОКРУЖЕНИЕ: для --stage нужен --out")
            return 2
        return stage(args)
    if args.publish:
        if not (args.tree and args.version):
            print("ОКРУЖЕНИЕ: для --publish нужны --tree и --version")
            return 2
        return publish(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
