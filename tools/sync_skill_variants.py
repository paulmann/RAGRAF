# -*- coding: utf-8 -*-
"""Синхронизация копий навыка из проекта — только по объявлению, с копией ДО записи.

Зачем инструмент, а не `copy`. Копий навыка несколько (в дереве и у учётной
записи), и у варианта HARNESS часть файлов намеренно отличается. Ручное
копирование «всей папки» затирает объявленный вариант, а копирование «по памяти»
оставляет расхождение незамеченным. Замер 25.10.2026: `reference/operations.md`
в двух копиях отставал от проекта (в копии «39 операций»), и увидеть это было
нечем — копии совпадали МЕЖДУ СОБОЙ.

Что делает. Читает `docs/skills/ragraf-core/VARIANTS.json` и синхронизирует
ТОЛЬКО файлы с режимом `identical`. Файлы с режимом `declared` не пишутся
никогда: они написаны под свой рантайм, и различие у них объявлено, а не забыто.

Границы. Посторонние файлы в каталоге копии (резервные копии, вложения) не
трогаются и перечисляются в отчёте. Перед перезаписью файл сохраняется в каталог
копий (`--backup-dir`, по умолчанию `.ragraf/backup-i11/skill-before`), поэтому
откат — это копирование обратно, а не поиск прежней версии.

Коды возврата: 0 — синхронизировано либо уже совпадает; 1 — запись не совпала с
источником после записи; 2 — аргументы или объявление не годятся.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
DECLARATION = REPO / "docs" / "skills" / "ragraf-core" / "VARIANTS.json"


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_roots(entry: dict) -> list[pathlib.Path]:
    """Корни копий по объявлению: `~` разворачивается, маска раскрывается."""
    raw = str(entry.get("root") or "")
    if raw.startswith("~"):
        raw = str(pathlib.Path.home()) + raw[1:]
    candidate = pathlib.Path(raw)
    if not candidate.is_absolute():
        candidate = REPO / raw
    pattern = str(candidate)
    if "*" in pattern or "?" in pattern:
        return [pathlib.Path(item) for item in sorted(glob.glob(pattern, recursive=True))]
    return [candidate]


def same_path(left: pathlib.Path, right: pathlib.Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def files_in(root: pathlib.Path) -> list[str]:
    return sorted(str(path.relative_to(root)).replace("\\", "/")
                  for path in root.rglob("*") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="Синхронизация копий навыка раграф-core")
    parser.add_argument("--to", action="append", default=[],
                        help="каталог копии навыка; можно несколько раз")
    parser.add_argument("--variant", default="",
                        help="имя варианта из VARIANTS.json (по умолчанию — единственный)")
    parser.add_argument("--allow-undeclared", action="store_true",
                        help="разрешить каталог, которого нет в объявлении (песочница)")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что было бы")
    parser.add_argument("--backup-dir", default=".ragraf/backup-i11/skill-before",
                        help="куда сохранить файл ДО перезаписи")
    args = parser.parse_args()

    if not args.to:
        print("ОТКАЗ: не указан --to: синхронизировать нечего")
        return 2
    if not DECLARATION.is_file():
        print("ОТКАЗ: нет " + str(DECLARATION) + " — без объявления синхронизация запрещена")
        return 2
    declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))
    variants = declaration.get("variants") or []
    if not variants:
        print("ОТКАЗ: в объявлении нет ни одного варианта")
        return 2
    wanted = args.variant.strip()
    if wanted:
        picked = [item for item in variants if str(item.get("name")) == wanted]
        if not picked:
            print("ОТКАЗ: варианта " + repr(wanted) + " нет в объявлении")
            return 2
        variant = picked[0]
    else:
        variant = variants[0]
        if len(variants) > 1:
            print("ОТКАЗ: вариантов несколько, нужен --variant")
            return 2

    source = REPO / str(declaration.get("source") or "docs/skills/ragraf-core")
    modes = variant.get("files") or {}
    declared_files = [str(name) for name in (declaration.get("files") or [])]
    roots: list[pathlib.Path] = []
    for item in (variant.get("copies") or []):
        roots.append(resolve_roots(item))
    roots = [path for group in roots for path in (group if isinstance(group, list) else [group])
             if path]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = (pathlib.Path(args.backup_dir) if pathlib.Path(args.backup_dir).is_absolute()
                   else REPO / args.backup_dir)

    exit_code = 0
    for raw_target in args.to:
        target = pathlib.Path(raw_target)
        if not target.is_absolute():
            target = REPO / target
        declared = any(same_path(target, root) for root in roots)
        if not declared and not args.allow_undeclared:
            print("ОТКАЗ: каталог " + str(target) + " не объявлен как копия варианта "
                  + str(variant.get("name")) + "; для песочницы нужен --allow-undeclared")
            exit_code = 2
            continue
        print("копия: " + str(target) + (" (объявлена)" if declared else " (песочница)"))
        if not target.is_dir():
            print("  ОТКАЗ: каталога нет — создавать копию навыка синхронизатор не имеет права")
            exit_code = 2
            continue
        for name in declared_files:
            mode = str((modes.get(name) or {}).get("mode") or "")
            source_file = source / name
            target_file = target / name
            if mode == "declared":
                print("  " + name + ": объявлен различающимся — не копируется")
                continue
            if mode != "identical":
                print("  " + name + ": режим " + repr(mode) + " неизвестен — пропущен")
                exit_code = 1
                continue
            if not source_file.is_file():
                print("  " + name + ": НЕТ в источнике " + str(source_file))
                exit_code = 1
                continue
            source_bytes = source_file.read_bytes()
            source_hash = hashlib.sha256(source_bytes).hexdigest()
            if target_file.is_file():
                target_bytes = target_file.read_bytes()
                if hashlib.sha256(target_bytes).hexdigest() == source_hash:
                    print("  " + name + ": уже совпадает (" + source_hash[:12]
                          + "), файл не перезаписан")
                    continue
                before = hashlib.sha256(target_bytes).hexdigest()
                saved = backup_root / stamp / str(target.name) / name
                if args.dry_run:
                    print("  " + name + ": было бы обновлено (" + before[:12] + " → "
                          + source_hash[:12] + "), копия ушла бы в " + str(saved))
                    continue
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_bytes(target_bytes)
            else:
                before = ""
                if args.dry_run:
                    print("  " + name + ": был бы создан (" + source_hash[:12] + ")")
                    continue
                target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(source_bytes)
            after = sha256_of(target_file)
            if after != source_hash:
                print("  " + name + ": ПОСЛЕ ЗАПИСИ хеш не совпал (" + after[:12]
                      + " вместо " + source_hash[:12] + ")")
                exit_code = 1
                continue
            print("  " + name + ": обновлён" + (" (было " + before[:12] + ")" if before else
                                                " (создан)") + " → " + after[:12])

        # Посторонние файлы не трогаются и называются: приманка в песочнице и
        # резервная копия в дереве выглядят одинаково — и обе должны остаться.
        extra = [name for name in files_in(target) if name not in declared_files]
        print("  посторонние файлы в копии: " + str(len(extra)) + " (не трогаются)"
              + (": " + ", ".join(extra[:6]) + ("…" if len(extra) > 6 else "") if extra else ""))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
