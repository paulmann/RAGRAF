"""Публикация выпуска одним проходом: таблица, сборка, архив, тег, релиз.

ЗАЧЕМ. Измеренный дефект 22.11.2026: таблица источников пересчитывалась ОТДЕЛЬНЫМ
коммитом после релиза, и в архиве 0.13.5 её голова оказалась на два коммита позади
тега. Правило «так нельзя» лежало в документе и не сработало — теперь пересчёт
входит в сам проход публикации.

ПОРЯДОК И ПОЧЕМУ ОН ТАКОЙ:
  1. `--stage` — голова клона публикации H становится головой таблицы; столбцы
     сверки пересчитываются (хэш публикации читается ИЗ КЛОНА, хэш дерева — из
     живого файла, «совпадает» следует из их равенства); дерево собирается по
     манифесту; проверяются `verify_release` и «таблица называет текущую голову».
     Дерево получает отпечаток (`<out>.stamp.json`) — по нему `--publish` узнаёт,
     что публикует именно проверенное дерево, а не подменённое между шагами.
  2. Архив собирается ИЗ ЭТОГО ЖЕ дерева: содержимое сверяется с `checksums.sha256`
     внутри дерева, а файл `<архив>.sha256` считается от готового архива.
  3. `--publish` — коммит от имени владельца, тег, push, релиз; после тега
     `check_sources_lag --mode published` обязан сказать «ровно один».

ОДИН ПРОХОД. `--release` = шаги 1–3 без ручных действий между ними; повторный запуск
после сбоя на любом шаге доходит до «ровно один» (релиз перезаливается с --clobber,
пустой коммит не считается отказом, архив пересобирается).

Запуск:
    python tools/release/publish_release.py --release --out <каталог> \
        --clone <клон публикации> --version 0.13.14 --notes-file <файл>
    python tools/release/publish_release.py --stage --out <каталог> --clone <клон>
    python tools/release/publish_release.py --publish --tree <каталог> --clone <клон> \
        --version 0.13.14 --archive <zip> --notes-file <файл>
    python tools/release/publish_release.py --check --tree <каталог> --clone <клон>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES_DOC = "docs/03_implementation/RELEASE-SOURCES.md"
ANY_SHA = re.compile(r"\b[0-9a-f]{40}\b")
CHECKSUMS = "checksums.sha256"


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


def tree_files(tree: pathlib.Path) -> list[str]:
    names: list[str] = []
    for base, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            names.append((pathlib.Path(base) / name).relative_to(tree).as_posix())
    return sorted(names)


def tree_digest(tree: pathlib.Path) -> str:
    """Отпечаток содержимого дерева: путь + sha256 каждого файла."""
    digest = hashlib.sha256()
    for rel in tree_files(tree):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(tree / rel).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def stamp_path(tree: pathlib.Path) -> pathlib.Path:
    return tree.with_name(tree.name + ".stamp.json")


def write_stamp(tree: pathlib.Path) -> None:
    stamp_path(tree).write_text(
        json.dumps({"tree": tree.name, "digest": tree_digest(tree), "files": len(tree_files(tree))},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_stamp(tree: pathlib.Path) -> str:
    """Пустая строка — дерево то же, что проверили; иначе причина отказа."""
    path = stamp_path(tree)
    if not path.is_file():
        return f"нет отпечатка сборки {path}: публиковать можно только проверенное дерево"
    recorded = json.loads(path.read_text(encoding="utf-8")).get("digest", "")
    actual = tree_digest(tree)
    if recorded != actual:
        return ("дерево изменилось после проверки: отпечаток "
                f"{recorded[:12]}…, сейчас {actual[:12]}…")
    return ""


def rewrite_sources(root: pathlib.Path, head: str, date: str) -> int:
    """Голова сверки, адреса строк таблицы и дата — на голову публикации."""
    path = root / SOURCES_DOC
    text = path.read_text(encoding="utf-8")
    before = text
    text = re.sub(r"(Публичный `main` на момент сверки:\*\*\s*`)[0-9a-f]{40}(`)",
                  lambda m: f"{m.group(1)}{head}{m.group(2)}", text)
    text = ANY_SHA.sub(head, text)
    text = re.sub(r"(сверки:\*\*\s*`[0-9a-f]{40}`\s*\n\()(\d{4}-\d{2}-\d{2})",
                  lambda m: f"{m.group(1)}{date}", text)
    path.write_text(text, encoding="utf-8", newline="\n")
    return 0 if text == before else 1


def refresh_hashes(root: pathlib.Path, clone: pathlib.Path, head: str) -> int:
    """Столбцы сверки: хэш публикации — из клона, хэш дерева — из живого файла.

    Строка таблицы:
        | файл в дереве | источник (URL+коммит) | sha256 в публичном main |
          sha256 в дереве | совпадает | путь в поставке |
    «Публичный main» — факт внешний, поэтому читается из клона на голове `head`, а не
    из живого файла: иначе проверяльщик ловит ложное «совпадает» (замер 23.11.2026:
    семь строк с живым хэшем в столбце публикации).
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


def verify_tree_checksums(tree: pathlib.Path) -> list[str]:
    """Строки `checksums.sha256` обязаны совпадать с файлами дерева."""
    listing = tree / CHECKSUMS
    if not listing.is_file():
        return [f"в сборке нет {CHECKSUMS}"]
    problems: list[str] = []
    listed: set[str] = set()
    for line in listing.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            problems.append(f"{CHECKSUMS}: строка не разобрана: {line[:60]}")
            continue
        expected, rel = parts[0].lower(), parts[1].lstrip("*").strip()
        listed.add(rel)
        target = tree / rel
        if not target.is_file():
            problems.append(f"{CHECKSUMS}: назван отсутствующий файл {rel}")
            continue
        actual = sha256(target)
        if actual != expected:
            problems.append(f"{CHECKSUMS}: {rel}: ожидался {expected[:12]}…, получен {actual[:12]}…")
    extra = [rel for rel in tree_files(tree) if rel not in listed and rel != CHECKSUMS]
    if extra:
        problems.append(f"{CHECKSUMS}: не перечислены файлы сборки: {', '.join(extra[:5])}")
    return problems


def build_archive(tree: pathlib.Path, archive: pathlib.Path) -> None:
    """Архив собирается из проверенного дерева; файл `<архив>.sha256` — от архива."""
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for rel in tree_files(tree):
            bundle.write(tree / rel, rel)
    digest = sha256(archive)
    pathlib.Path(str(archive) + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")


def verify_archive(archive: pathlib.Path, tree: pathlib.Path) -> list[str]:
    """Архив обязан содержать ровно файлы дерева с теми же хэшами."""
    if not archive.is_file():
        return [f"архива нет: {archive}"]
    problems: list[str] = []
    with zipfile.ZipFile(archive) as bundle:
        names = sorted(bundle.namelist())
        if names != tree_files(tree):
            missing = sorted(set(tree_files(tree)) - set(names))
            extra = sorted(set(names) - set(tree_files(tree)))
            if missing:
                problems.append(f"в архиве нет файлов: {', '.join(missing[:5])}")
            if extra:
                problems.append(f"в архиве лишние файлы: {', '.join(extra[:5])}")
        for rel in names:
            if rel not in set(tree_files(tree)):
                continue
            data = bundle.read(rel)
            if hashlib.sha256(data).hexdigest() != sha256(tree / rel):
                problems.append(f"в архиве файл {rel} не совпадает с деревом")
    recorded = pathlib.Path(str(archive) + ".sha256")
    if not recorded.is_file():
        problems.append(f"нет файла {recorded.name}")
    else:
        stated = recorded.read_text(encoding="utf-8").split()[0].lower()
        if stated != sha256(archive):
            problems.append(f"{recorded.name} называет {stated[:12]}…, архив — {sha256(archive)[:12]}…")
    return problems


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

    checksums = verify_tree_checksums(out)
    for problem in checksums[:5]:
        print(f"  · {problem}")
    if checksums:
        print("ОТКАЗ: собственный список контрольных сумм сборки не сходится")
        return 1
    write_stamp(out)
    print(f"ГОТОВО к публикации: {out}")
    return 0


def publish(args: argparse.Namespace) -> int:
    clone = pathlib.Path(args.clone).resolve()
    tree = pathlib.Path(args.tree).resolve()
    version = args.version
    tag = f"v{version}" if version else ""

    problem = check_stamp(tree)
    if problem:
        print(f"ОТКАЗ: {problem}")
        return 1
    code, text = run([sys.executable, str(ROOT / "tools/release/verify_release.py"), "--tree", str(tree)], ROOT)
    if code != 0:
        print("ОТКАЗ: дерево не прошло сверку с источником перед публикацией")
        print("\n".join(text.strip().splitlines()[-3:]))
        return 1
    checksums = verify_tree_checksums(tree)
    if checksums:
        print(f"ОТКАЗ: {checksums[0]}")
        return 1
    if args.archive:
        archive_problems = verify_archive(pathlib.Path(args.archive).resolve(), tree)
        if archive_problems:
            print(f"ОТКАЗ: {archive_problems[0]}")
            return 1
    code, text = run([sys.executable, str(ROOT / "tools/release/check_sources_lag.py"),
                      "--mode", "build", "--tree", str(tree), "--clone", str(clone)], ROOT)
    print(text.strip().splitlines()[-1] if text.strip() else "")
    if code != 0:
        print("ОТКАЗ: голова таблицы разошлась со сборкой")
        return 1
    if args.check:
        print("ПРОВЕРКА ПРОЙДЕНА: дерево, список сумм, архив и голова таблицы согласованы")
        return 0
    if not tag:
        print("ОКРУЖЕНИЕ: для публикации нужна --version")
        return 2

    built = {rel: tree / rel for rel in tree_files(tree)}
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
    subprocess.run(["git", *identity, "add", "-u"], cwd=str(clone), check=True, env=env)
    if add:
        subprocess.run(["git", *identity, "add", "--"] + add, cwd=str(clone), check=True, env=env)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(clone))
    if staged.returncode == 0:
        # Нечего коммитить: дерево уже опубликовано (повторный запуск после сбоя на
        # шаге релиза). Это не отказ — проверка «ровно один» ниже скажет правду.
        print("коммит не нужен: в публикации уже это дерево")
    else:
        commit = subprocess.run(["git", *identity, "commit", "-m", message], cwd=str(clone),
                                capture_output=True, text=True, env=env)
        if commit.returncode != 0:
            print(f"ОТКАЗ: коммит не сделан: {(commit.stdout + commit.stderr).strip()[:300]}")
            return 1
    subprocess.run(["git", "tag", "-f", "-a", tag, "-m", f"RAGRAF {version}"], cwd=str(clone),
                   check=True, env=env)
    for cmd in (["git", "push", "origin", "main"], ["git", "push", "--force", "origin", tag]):
        result = subprocess.run(cmd, cwd=str(clone), capture_output=True, text=True, env=env)
        output = (result.stdout + result.stderr).strip()
        if output:
            print(output.splitlines()[-1])
        if result.returncode != 0:
            print(f"ОТКАЗ: {cmd[1]} {cmd[2] if len(cmd) > 2 else ''} не удался")
            return 1

    archive = pathlib.Path(args.archive).resolve() if args.archive else None
    notes = pathlib.Path(args.notes_file).resolve() if args.notes_file else None
    if notes and not notes.is_file():
        print(f"ОКРУЖЕНИЕ: файла заметок нет: {notes}")
        return 2
    if archive and archive.is_file():
        # Повторный запуск: релиз уже есть — догружаем с заменой, а не падаем.
        existing = subprocess.run(["gh", "release", "view", tag, "--json", "tagName"],
                                  cwd=str(clone), capture_output=True, text=True, env=env)
        assets = [str(archive), str(archive) + ".sha256"]
        if existing.returncode == 0:
            result = subprocess.run(["gh", "release", "upload", tag, *assets, "--clobber"],
                                    cwd=str(clone), capture_output=True, text=True, env=env)
        else:
            result = subprocess.run(["gh", "release", "create", tag, *assets,
                                     "--title", f"RAGRAF {version}",
                                     "--notes-file", str(notes)], cwd=str(clone),
                                    capture_output=True, text=True, env=env)
        print((result.stdout + result.stderr).strip().splitlines()[-1] if (result.stdout + result.stderr).strip() else "")
        if result.returncode != 0:
            print("ОТКАЗ: релиз не выложен")
            return 1

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
    parser = argparse.ArgumentParser(description="публикация выпуска: таблица, сборка, архив, тег, релиз")
    parser.add_argument("--stage", action="store_true", help="пересчитать таблицу и собрать дерево")
    parser.add_argument("--publish", action="store_true", help="закоммитить, поставить тег, выложить релиз")
    parser.add_argument("--release", action="store_true", help="один проход: stage → архив → publish")
    parser.add_argument("--check", action="store_true",
                        help="только проверки публикации (отпечаток, сверка, суммы, архив, голова)")
    parser.add_argument("--out", default="", help="каталог сборки (для --stage и --release)")
    parser.add_argument("--tree", default="", help="собранное дерево (для --publish и --check)")
    parser.add_argument("--clone", default=os.environ.get("RAGRAF_PUBLICATION_CLONE", ""),
                        help="клон публикации (или RAGRAF_PUBLICATION_CLONE)")
    parser.add_argument("--version", default="", help="версия выпуска, например 0.13.14")
    parser.add_argument("--date", default="", help="дата сверки (по умолчанию сегодня)")
    parser.add_argument("--message", default="", help="сообщение коммита")
    parser.add_argument("--archive", default="", help="zip-архив релиза")
    parser.add_argument("--notes-file", default="", help="файл заметок релиза")
    args = parser.parse_args(argv)
    if args.clone == "":
        print("ОКРУЖЕНИЕ: клон публикации не задан (--clone или RAGRAF_PUBLICATION_CLONE)")
        return 2
    if not args.date:
        import datetime
        args.date = datetime.date.today().isoformat()

    if args.release:
        if not (args.out and args.version):
            print("ОКРУЖЕНИЕ: для --release нужны --out и --version")
            return 2
        tree = pathlib.Path(args.out).resolve()
        code = stage(args)
        if code != 0:
            return code
        archive = pathlib.Path(args.archive).resolve() if args.archive else \
            pathlib.Path(str(tree) + f"-{args.version}.zip")
        build_archive(tree, archive)
        problems = verify_archive(archive, tree)
        print(f"архив: {archive.name} ({archive.stat().st_size} Б, sha256 {sha256(archive)[:16]}…)")
        for problem in problems[:5]:
            print(f"  · {problem}")
        if problems:
            print("ОТКАЗ: архив не совпадает с проверенным деревом")
            return 1
        args.tree = str(tree)
        args.archive = str(archive)
        return publish(args)

    if args.check or args.publish:
        if not args.tree:
            print("ОКРУЖЕНИЕ: нужен --tree")
            return 2
        return publish(args)
    if args.stage:
        if not args.out:
            print("ОКРУЖЕНИЕ: для --stage нужен --out")
            return 2
        return stage(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
