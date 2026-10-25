#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборщик поставки RAGRAF: живое дерево → публикуемое дерево по манифесту.

Задача, из которой он вырос (этапы 1–2 модернизации, 24.10.2026): публикация
собиралась руками в `.staging/release-v0.2` и отстала от кода на десять дней.
Следствия измерены: обвязки (`CHANGELOG`, `CONTRIBUTING`, `SECURITY`, `CITATION`,
`README.ru`, `.github/**`) в живом дереве не было вовсе, установщик существовал
в трёх версиях (34 242 / 37 794 / 51 062 байта), а `.github/workflows/ci.yml:125`
вызывал `-SummaryJson`, которого живой установщик не объявлял.

Что делает:

1. читает `tools/release/manifest.toml` — отображение «живое → поставка»;
2. собирает список кандидатов, отсекая исключения манифеста (и печатает, сколько
   файлов отсекло каждое правило: «мёртвое» правило видно сразу);
3. отказывается собирать внутрь живого дерева и в каталог, который с ним
   пересекается: такая сборка попала бы в собственный индекс RAGRAF и следующая
   реиндексация считала бы её частью корпуса;
4. отказывается писать в непустой приёмник без `--force`, а с `--force` сначала
   переносит прежнее содержимое в `<приёмник>.bak-<метка времени>` и печатает,
   что именно убрано;
5. пишет `checksums.sha256` — по строке на файл, порядок путей лексикографический.

Детерминизм не обещается, а проверяется: два прогона на неизменном дереве дают
побайтово одинаковый `checksums.sha256`. Этого требует сторож свежести самого
RAGRAF: если состав поставки «дрожит» от прогона к прогону, сравнивать релизы
нечем.

Запуск:
    python tools/release/build_release.py --out %TEMP%\\ragraf-release-sbx
    python tools/release/build_release.py --out D:\\rel --force

Коды возврата: 0 — собрано, 1 — дефект дерева или манифеста,
2 — ошибка аргументов (в том числе непустой приёмник без --force),
3 — ошибка окружения (нет python-модуля tomllib, нет прав на запись).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

# Замер (24.10.2026): у вендоренного Python 3.13 этого окружения каталог
# скрипта НЕ попадает в sys.path — `import relcommon` падал с ModuleNotFoundError,
# хотя файл лежит рядом. Путь добавляется явно: конвейер обязан работать
# одинаково и от системного Python, и от вендоренного.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import relcommon as rel  # noqa: E402 - путь к модулю добавлен выше


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_release.py",
        description="Собрать публикуемое дерево RAGRAF по манифесту.",
    )
    parser.add_argument("--out", required=True, help="каталог назначения (сборка)")
    parser.add_argument(
        "--manifest",
        default=None,
        help=f"манифест (по умолчанию {rel.MANIFEST_DEFAULT} в живом дереве)",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="корень живого дерева (по умолчанию — два каталога вверх от этого файла)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="разрешить сборку в непустой приёмник (прежнее содержимое переносится в .bak-)",
    )
    return parser.parse_args(argv)


def write_checksums(tree: Path, targets: list[str]) -> Path:
    path = tree / "checksums.sha256"
    lines = []
    for target in sorted(targets):
        lines.append(f"{rel.sha256_file(tree / target)}  {target}")
    # Перевод строки LF и завершающий перевод строки: файл должен быть
    # побайтово одинаковым на Windows и Linux, иначе «детерминизм» окажется
    # детерминизмом одной машины.
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    source = Path(args.source).resolve() if args.source else rel.default_source()
    out = Path(args.out).expanduser().resolve()
    manifest_path = (
        Path(args.manifest).resolve()
        if args.manifest
        else source / rel.MANIFEST_DEFAULT
    )

    if not manifest_path.is_file():
        return rel.fail(f"нет манифеста: {manifest_path}", 2)
    if not source.is_dir():
        return rel.fail(f"нет корня живого дерева: {source}", 2)

    # Пересечение путей проверяется ДО любой записи: сборка внутрь живого дерева
    # попала бы в собственный индекс, а сборка «поверх» источника уничтожила бы
    # исходники.
    if rel.overlaps(source, out):
        return rel.fail(
            f"приёмник пересекается с живым деревом: {out} против {source}. "
            f"Сборка внутрь дерева запрещена: она попадёт в свой же индекс",
            2,
        )

    try:
        manifest = rel.load_manifest(manifest_path, source)
    except Exception as error:  # noqa: BLE001 - манифест может быть калечным
        return rel.fail(f"манифест не разбирается: {error}", 1)

    if out.exists():
        existing = sorted(item.name for item in out.iterdir())
        if existing and not args.force:
            return rel.fail(
                f"приёмник не пуст: {out} ({len(existing)} объектов). "
                f"Нужен --force (прежнее содержимое будет перенесено в .bak-)",
                2,
            )
        if existing and args.force:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = out.with_name(f"{out.name}.bak-{stamp}")
            shutil.move(str(out), str(backup))
            print(f"прежнее содержимое приёмника перенесено: {backup}")
            print(f"  объектов: {len(existing)}")
            for name in existing[:20]:
                print(f"  убрано: {name}")
            if len(existing) > 20:
                print(f"  … ещё {len(existing) - 20}")

    plan = rel.collect(source, manifest)
    if plan.problems:
        for problem in plan.problems:
            print(f"FAIL: {problem}")
        return 1

    out.mkdir(parents=True, exist_ok=True)

    copied = 0
    for target in sorted(plan.files):
        destination = out / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plan.files[target], destination)
        copied += 1

    checksums = write_checksums(out, sorted(plan.files))

    print(f"манифест:            {manifest_path}")
    print(f"источник:            {source}")
    print(f"приёмник:            {out}")
    print(f"строк в манифесте:   {len(manifest.includes)} включений, {len(manifest.excludes)} исключений")
    print(f"файлов перенесено:   {copied}")
    print(f"объём:               {plan.total_bytes} байт ({plan.total_bytes / (1 << 20):.1f} МБ)")
    print(f"контрольные суммы:   {checksums.name} ({copied} строк)")

    print("исключения (сколько файлов отсекло каждое правило):")
    for exclude in manifest.excludes:
        hits = plan.excluded.get(exclude.pattern, [])
        kept = plan.kept.get(exclude.pattern, [])
        # Пометка сверяется с числом ПРИМЕНЕНИЙ правила, а не с числом
        # отсечённых файлов: в издании, которое объявило путь в `pro_only`,
        # правило сработало (иначе `active` был бы ложью), но файл опубликован.
        applied = len(hits) + len(kept)
        mark = "" if (exclude.state == "guard") == (applied == 0) else "  ← пометка не соответствует факту"
        tail = f", возвращено объявлением: {len(kept)}" if kept else ""
        print(f"  [{exclude.state:6}] {exclude.pattern:24} отсечено: {len(hits):4}{tail}{mark}")
    if plan.kept:
        print("закрытые модули издания (объявлены в pro_only, в поставке есть):")
        for pattern, paths in sorted(plan.kept.items()):
            for item in paths:
                print(f"  {item}  (исключение {pattern} не применяется)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
