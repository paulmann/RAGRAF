#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Хранилище вырезанного: сохранить функциональность перед удалением и вернуть её.

Правило владельца (24.10.2026): **при вырезании функционала или функций копия
остаётся на этой машине, в хранилище знаний RAGRAF**, чтобы работа не пропадала и
могла быть быстро восстановлена.

Задача, из которой это выросло. Выпуск 0.2.0 публиковался «как собрано», и 17
файлов исчезли из публичного репозитория. Проверка показала: их не было ни в живом
дереве, ни в снимке — только в истории git. То есть «ничего не потеряно» было
верно лишь до тех пор, пока кто-то помнит нужный коммит. Хуже: файл, которого нет
ни в одной рабочей папке, не находится поиском — его как будто не писали.

Что делает инструмент:

1. `--paths` — копирует указанные файлы и каталоги в
   `archive/<метка>-<тема>/files/`, сохраняя относительные пути;
2. пишет `MANIFEST.md`: что вырезано, откуда, почему, sha256 каждого файла и
   готовую команду восстановления; манифест — часть индекса, поэтому поиск по
   причине и по имени файла находит запись;
3. `--restore` — возвращает файлы из архива, СВЕРЯЯ sha256: если файл на месте и
   отличается, без `--force` отказывается (восстановление не должно молча
   перезаписать новую работу);
4. `--list` — что лежит в архиве.

Границы, названные честно:

* инструмент не удаляет и не изменяет источник: он только читает его и копирует;
* архив лежит в живом дереве RAGRAF, поэтому попадает в корпус и в поиск; в
  публикацию он не входит (манифест поставки — белый список) и объявлен
  удержанным в `editions/editions.toml`;
* файлы копируются побайтово как есть: кодировки и переводы строк не
  переписываются — иначе восстановление вернуло бы не то, что было;
* «быстро восстановлена» проверяется прогоном `--restore` на копии, а не обещанием.

Запуск:
    python tools/keep_cut.py --paths src/ragraf/old_op.py --reason "операция снята" --tag old-op
    python tools/keep_cut.py --list
    python tools/keep_cut.py --restore 20261024-201500-old-op --to %TEMP%\\restore-check

Коды возврата: 0 — сделано, 1 — нарушение (нет источника, хэш не сошёлся,
цель существует), 2 — ошибка аргументов.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import pathlib
import re
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "archive"  # значение по умолчанию; --archive перекрывает


def show(path: pathlib.Path) -> str:
    """Путь для человека: относительно RAGRAF, если внутри, иначе абсолютный."""
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def slug(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_-]+", "-", text.strip()).strip("-")
    return (cleaned or "cut")[:48].lower()


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_files(paths: list[str]) -> list[tuple[pathlib.Path, str]]:
    """Развернуть аргументы в список (абсолютный путь, путь внутри архива)."""
    resolved: list[tuple[pathlib.Path, str]] = []
    for raw in paths:
        source = pathlib.Path(raw)
        if not source.is_absolute():
            source = (pathlib.Path.cwd() / source)
        source = source.resolve()
        if not source.exists():
            raise SystemExit(f"FAIL: нет такого пути: {source}")
        if source.is_file():
            try:
                relative = source.relative_to(REPO).as_posix()
            except ValueError:
                relative = source.name
            resolved.append((source, relative))
            continue
        for item in sorted(source.rglob("*")):
            if item.is_file():
                try:
                    relative = item.relative_to(REPO).as_posix()
                except ValueError:
                    relative = f"{source.name}/{item.relative_to(source).as_posix()}"
                resolved.append((item, relative))
    return resolved


def make_manifest(entry: pathlib.Path, files: list[tuple[pathlib.Path, str]],
                  reasons: str, origin: str) -> pathlib.Path:
    rows = []
    for source, relative in files:
        rows.append(f"| `{relative}` | {source.stat().st_size} | {digest(source)} |")
    lines = [
        f"# Вырезано {entry.name}",
        "",
        f"- дата: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- причина: {reasons}",
        f"- источник на момент вырезания: `{origin}`",
        f"- файлов: {len(files)}, байт: {sum(source.stat().st_size for source, _ in files)}",
        "",
        "Каталог создан `tools/keep_cut.py`. Он входит в корпус RAGRAF, поэтому",
        "поиск находит и путь, и причину; в публикацию не входит.",
        "",
        "| путь | байт | sha256 |",
        "|---|---|---|",
        *rows,
        "",
        "## Как вернуть",
        "",
        "```powershell",
        f"python tools/keep_cut.py --restore {entry.name} --to <каталог назначения>",
        "```",
        "",
        "Восстановление сверяет sha256 и без `--force` не перезаписывает файл,",
        "который на месте и отличается — иначе возврат старого затёр бы новую работу.",
        "",
        "## Как найти поиском",
        "",
        "```powershell",
        f'python tools/ragraf.py -p config/ragraf.yaml query search "{pathlib.Path(files[0][1]).name}"',
        "```",
        "",
    ]
    manifest = entry / "MANIFEST.md"
    manifest.write_bytes(("\n".join(lines)).encode("utf-8"))
    return manifest


def do_archive(args: argparse.Namespace) -> int:
    if not args.paths:
        print("FAIL: не указано, что вырезано (--paths)")
        return 2
    if not args.reason.strip():
        print("FAIL: причина обязательна: архив без причины — это склад, а не знание")
        return 2

    origin = pathlib.Path.cwd().resolve()
    files = collect_files(args.paths)
    if not files:
        print("FAIL: указанные пути не содержат файлов")
        return 1

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    entry = ARCHIVE / f"{stamp}-{slug(args.tag or pathlib.Path(args.paths[0]).stem)}"
    if entry.exists():
        print(f"FAIL: запись уже существует: {entry}")
        return 1

    if args.dry_run:
        print(f"DRY RUN: было бы создано {entry}")
        for source, relative in files:
            print(f"  {relative} ({source.stat().st_size} Б)")
        return 0

    for source, relative in files:
        target = entry / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest = make_manifest(entry, files, args.reason.strip(), str(origin))

    print(f"архив: {show(entry)}")
    print(f"файлов: {len(files)}")
    print(f"манифест: {show(manifest)}")
    print("теперь проиндексируйте корпус, чтобы поиск находил вырезанное:")
    print("  python tools/ragraf.py -p config/ragraf.yaml reindex")
    return 0


def read_manifest(entry: pathlib.Path) -> list[tuple[str, int, str]]:
    manifest = entry / "MANIFEST.md"
    if not manifest.is_file():
        raise SystemExit(f"FAIL: в записи нет MANIFEST.md: {entry}")
    rows = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        rows.append((cells[0].strip("`"), int(cells[1]), cells[2]))
    return rows


def do_restore(args: argparse.Namespace) -> int:
    entry = ARCHIVE / args.restore
    if not entry.is_dir():
        print(f"FAIL: нет такой записи архива: {entry}")
        return 1
    destination = pathlib.Path(args.to).resolve()
    rows = read_manifest(entry)

    planned = []
    for relative, size, expected in rows:
        stored = entry / "files" / relative
        if not stored.is_file():
            print(f"FAIL: в архиве нет файла: {relative}")
            return 1
        if digest(stored) != expected:
            print(f"FAIL: хэш архивной копии не совпал: {relative}")
            return 1
        target = destination / pathlib.Path(relative).name if args.flat else destination / relative
        if target.exists() and digest(target) != expected and not args.force:
            print(f"FAIL: {target} уже есть и отличается — без --force не перезаписываю")
            return 1
        planned.append((stored, target, size))

    if args.dry_run:
        print(f"DRY RUN: было бы восстановлено в {destination}")
        for _stored, target, size in planned:
            print(f"  {target} ({size} Б)")
        return 0

    for stored, target, _size in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stored, target)
        if digest(target) != digest(stored):
            print(f"FAIL: после восстановления хэш не совпал: {target}")
            return 1
    print(f"восстановлено файлов: {len(planned)} в {destination}")
    return 0


def do_list() -> int:
    if not ARCHIVE.is_dir():
        print("архив пуст: каталога archive/ нет")
        return 0
    entries = sorted(p for p in ARCHIVE.iterdir() if p.is_dir())
    if not entries:
        print("архив пуст")
        return 0
    for entry in entries:
        rows = read_manifest(entry) if (entry / "MANIFEST.md").is_file() else []
        print(f"{entry.name}: файлов {len(rows)}, байт {sum(row[1] for row in rows)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="keep_cut.py", description="Хранилище вырезанного")
    parser.add_argument("--paths", nargs="*", default=[], help="что вырезано (файлы и каталоги)")
    parser.add_argument("--reason", default="", help="почему вырезано (обязательно)")
    parser.add_argument("--tag", default="", help="короткая тема для имени записи")
    parser.add_argument("--list", action="store_true", help="показать записи архива")
    parser.add_argument("--restore", default="", help="имя записи для восстановления")
    parser.add_argument("--to", default="", help="куда восстанавливать")
    parser.add_argument("--flat", action="store_true", help="класть файлы без структуры каталогов")
    parser.add_argument("--force", action="store_true", help="перезаписать отличающийся файл")
    parser.add_argument("--dry-run", action="store_true", help="ничего не менять")
    parser.add_argument("--archive", default="", help="каталог хранилища (по умолчанию archive/ в корне RAGRAF)")
    args = parser.parse_args(argv)

    global ARCHIVE
    if args.archive:
        ARCHIVE = pathlib.Path(args.archive).resolve()

    if args.list:
        return do_list()
    if args.restore:
        if not args.to:
            print("FAIL: для --restore нужен --to <каталог>")
            return 2
        return do_restore(args)
    return do_archive(args)


if __name__ == "__main__":
    sys.exit(main())
