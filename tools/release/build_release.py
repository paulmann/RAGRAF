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
import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Замер (24.10.2026): у вендоренного Python 3.13 этого окружения каталог
# скрипта НЕ попадает в sys.path — `import relcommon` падал с ModuleNotFoundError,
# хотя файл лежит рядом. Путь добавляется явно: конвейер обязан работать
# одинаково и от системного Python, и от вендоренного.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import relcommon as rel  # noqa: E402 - путь к модулю добавлен выше
import release_report as report_mod  # noqa: E402 - отчёт выпуска (X21)

#: Артефакт сборщика: описания операций и подтверждённый набор (этап X9).
#: Объявлен в `artifacts` манифеста — проверка полноты его не считает мусором.
PINS_ARTIFACT = "ops_pins.json"

#: Артефакт сборщика: отчёт выпуска для человека (этап X21). Собирается ИЗ ТОГО
#: ЖЕ `ops_pins.json`, что лежит рядом, поэтому не может ему противоречить.
REPORT_ARTIFACT = report_mod.REPORT_ARTIFACT


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_release.py",
        description="Собрать публикуемое дерево RAGRAF по манифесту.",
    )
    parser.add_argument(
        "--out",
        required=False,
        help="каталог назначения (сборка); при --dry-run необязателен",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="показать, что будет собрано, и ничего не писать",
    )
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
        "--previous",
        default=None,
        help="манифест описаний прошлого выпуска: включает в отчёт раздел «что изменилось»",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="разрешить сборку в непустой приёмник (прежнее содержимое переносится в .bak-)",
    )
    parser.add_argument(
        "--accept-samples",
        dest="accept_samples",
        action="store_true",
        help="принять снятые пробы поведения как поведение выпуска (требует --approved-by)",
    )
    parser.add_argument(
        "--approved-by",
        dest="approved_by",
        default="",
        help="кто принимает пробы (обязательно вместе с --accept-samples)",
    )
    parser.add_argument(
        "--sample-note",
        dest="sample_note",
        default="",
        help="пометка к приёму проб (почему поведение сменилось)",
    )
    return parser.parse_args(argv)


def version_of(source: Path) -> str:
    """Версия из ФАЙЛА исходников, а не из загруженного модуля.

    Импорт вернул бы версию РАБОТАЮЩЕГО процесса — при сборке двух деревьев
    это разные числа, и файл описывал бы не то, что выпущено (замерено
    собственным провалом набора: артефакт песочницы назвал 0.10.0 при
    исходнике 9.9.9).
    """
    path = source / "src" / "ragraf" / "__init__.py"
    if not path.is_file():
        return ""
    body = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', body)
    return match.group(1) if match else ""


def control_db(source: Path) -> Path | None:
    """Журнал корпуса, откуда берётся поставленный пин.

    Сначала машинный конфиг (`control_db`), потом привычное место `.ragraf`:
    сборка идёт и там, где конфига нет (CI из опубликованного дерева), и тогда
    пинов просто нет — это состояние `none`, а не ошибка сборки.
    """
    fallback = source / ".ragraf" / "control.sqlite"
    config_path = source / "config" / "ragraf.yaml"
    if config_path.is_file():
        try:
            from ragraf import config as config_mod

            return Path(config_mod.load(config_path).control_db)
        except Exception:  # noqa: BLE001 — конфиг мог быть чужим или калечным
            pass
    return fallback if fallback.is_file() else None


def package_src(source: Path) -> Path:
    """Каталог пакета, ЧЬИМ кодом считается описание.

    Берётся дерево, в котором лежит сам сборщик, а не `--source`: инструмент
    обязан работать на том коде, с которым собран. Замер 10.11.2026: сборщик из
    нового дерева и источник со старым `pin.py` дали `AttributeError:
    module 'ragraf.pin' has no attribute 'state_payload'` — сборка релиза падала
    бы на разнице версий инструмента и корпуса.

    Описания при этом читаются у ИСТОЧНИКА (`hashes(source)` разбирает его
    файл), то есть чужих чисел здесь не появляется.
    """
    own = Path(__file__).resolve().parents[2] / "src"
    if (own / "ragraf" / "pin.py").is_file():
        return own
    return source / "src"


def accept_samples(source: Path, *, by: str, note: str = "") -> tuple[int, str]:
    """Принять снятые пробы как поведение выпуска: имя обязательно.

    Отдельным шагом, а не побочным эффектом сборки: смена поведения — решение
    человека, и без имени она не выполняется (в том числе когда `--accept-samples`
    передан, а `--approved-by` забыт).
    """
    if not str(by).strip():
        return 2, ("--accept-samples требует --approved-by: смена поведения без "
                   "автора не отличается от самопринятия")
    src = str(package_src(source).resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    from ragraf import pin as pin_mod

    database = control_db(source)
    if database is None:
        return 1, (f"журнала корпуса нет ({source}): приём проб невозможен — "
                   f"сначала `contract_pin_sample --sample-mode take`")
    conn = sqlite3.connect(str(database))
    try:
        try:
            data = pin_mod.accept_stored(conn, by=str(by), note=str(note))
        except Exception as error:  # noqa: BLE001 — причина называется
            return 1, f"приём проб не выполнен: {type(error).__name__}: {error}"
    finally:
        conn.close()
    return 0, f"принято проб: {data.get('total')}"


def pins_document(source: Path) -> dict:
    """Что кладётся в `ops_pins.json`: хеши описаний и подтверждённый набор.

    Хеши считаются у ТОГО кода, который уезжает в поставку (`source/src`), а
    не у чужого корпуса: иначе файл описывал бы не то, что выпущено.
    Детерминизм проверяется делом: ни времени сборки, ни пути машины в файле
    нет — два прогона на неизменном дереве дают одинаковые байты.
    """
    src = str(package_src(source).resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    from ragraf import pin as pin_mod

    ops_current = pin_mod.hashes(source)
    tools_current = pin_mod.tool_hashes(source)
    ops_pins: dict = {}
    tool_pins: dict = {}
    db = control_db(source)
    if db is not None:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            ops_pins = pin_mod.pins(conn, "op")
            tool_pins = pin_mod.pins(conn, "tool")
        finally:
            conn.close()
    ops_payload = pin_mod.state_payload(ops_current, ops_pins)
    tools_payload = pin_mod.state_payload(tools_current, tool_pins)
    samples = {"probes": {}, "state": {"total": 0, "changed": 0, "state": "none"}}
    if db is not None:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            samples = pin_mod.samples_document(conn)
        finally:
            conn.close()
    stamps = [str(item["approved_at"]) for item in
              list(ops_pins.values()) + list(tool_pins.values())]
    oldest = min(stamps) if stamps else ""
    return {
        "schema": 3,
        "version": version_of(source),
        "source": "src/ragraf/facade.py",
        "ops_total": len(ops_current),
        "tools_total": len(tools_current),
        "pin_state": pin_mod.merge_states(ops_payload["state"], tools_payload["state"]),
        "pin_state_ops": str(ops_payload["state"]),
        "pin_state_tools": str(tools_payload["state"]),
        # Возраст в файл НЕ пишется: он меняется каждую секунду, и два прогона
        # на неизменном дереве давали разные байты (замерено набором).
        # В файле — отметка времени, а возраст считает тот, кто читает.
        "approved_at": oldest,
        "diverged": list(ops_payload.get("diverged") or []),
        "tool_diverged": list(tools_payload.get("diverged") or []),
        "unpinned_total": int(ops_payload.get("unpinned") or 0),
        "stale_total": int(ops_payload.get("stale") or 0),
        "tool_unpinned_total": int(tools_payload.get("unpinned") or 0),
        "tool_stale_total": int(tools_payload.get("stale") or 0),
        "ops": dict(sorted(ops_current.items())),
        "tools": dict(sorted(tools_current.items())),
        "details": pin_mod.descriptions(source),
        "tool_details": pin_mod.tool_descriptions(source),
        "pins": {name: {"hash": str(ops_pins[name]["hash"]),
                        "approved_at": str(ops_pins[name]["approved_at"]),
                        "approved_by": str(ops_pins[name]["approved_by"])}
                 for name in sorted(ops_pins)},
        "tool_pins": {name: {"hash": str(tool_pins[name]["hash"]),
                             "approved_at": str(tool_pins[name]["approved_at"]),
                             "approved_by": str(tool_pins[name]["approved_by"])}
                      for name in sorted(tool_pins)},
        # Пробы поведения (X15): только хеши и итог сверки. Значения и время не
        # пишутся — иначе два прогона на неизменном дереве давали бы разные байты.
        "samples": {"probes": samples.get("probes") or {},
                    "pin_state": str((samples.get("state") or {}).get("state") or "none"),
                    "changed": int((samples.get("state") or {}).get("changed") or 0),
                    "total": int((samples.get("state") or {}).get("total") or 0)},
    }


def write_pins(tree: Path, source: Path) -> Path:
    """Положить `ops_pins.json` в поставку. Не собралось — сборка не состоялась.

    Мягкий вариант («предупредить и выпустить без файла») сделал бы ровно ту
    тишину, ради устранения которой этап и делается: релиз без описаний никто
    бы не заметил.
    """
    document = pins_document(source)
    path = tree / PINS_ARTIFACT
    # LF, без BOM, ключи отсортированы: файл должен быть побайтово одинаковым
    # на Windows и Linux (то же требование, что у checksums.sha256).
    body = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_bytes(body.encode("utf-8"))
    return path


def write_report(tree: Path, source: Path, previous: Path | None = None) -> Path:
    """Положить отчёт выпуска в поставку.

    Без `--previous` отчёт просто не содержит раздела «что изменилось»: пустой
    раздел читался бы как «ничего не изменилось», а это утверждение, которого
    никто не проверял.
    """
    return report_mod.write(tree, previous=previous, source=source)


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
    # Приём проб идёт ДО проверки приёмника: это шаг состояния, а не записи
    # поставки; без имени он не выполняется ни при каких других аргументах.
    if getattr(args, "accept_samples", False):
        code, message = accept_samples(source, by=str(getattr(args, "approved_by", "")),
                                       note=str(getattr(args, "sample_note", "")))
        print(("OK: " if code == 0 else "FAIL: ") + message)
        if code != 0:
            return code
    if not args.out and not args.dry_run:
        return rel.fail("не задан --out: собирать некуда (для предпросмотра есть --dry-run)", 2)
    out = Path(args.out).expanduser().resolve() if args.out else None
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
    if out is not None and rel.overlaps(source, out):
        return rel.fail(
            f"приёмник пересекается с живым деревом: {out} против {source}. "
            f"Сборка внутрь дерева запрещена: она попадёт в свой же индекс",
            2,
        )

    try:
        manifest = rel.load_manifest(manifest_path, source)
    except Exception as error:  # noqa: BLE001 - манифест может быть калечным
        return rel.fail(f"манифест не разбирается: {error}", 1)

    if out is not None and out.exists() and not args.dry_run:
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

    copied = 0
    checksums = None
    pins = None
    pins_payload: dict = {}
    report_path = None
    report_payload: dict = {}
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        for target in sorted(plan.files):
            destination = out / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(plan.files[target], destination)
            copied += 1
        # Описания операций кладутся в поставку до сумм: без них релиз не
        # считается собранным (см. `write_pins`), и молча выданная поставка
        # без описаний — та самая тишина, которую этап убирает.
        try:
            pins = write_pins(out, source)
            pins_payload = json.loads(pins.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001 — причина называется в отказе
            print(f"FAIL: описания операций не собраны: {type(error).__name__}: {error}")
            return 1
        # Отчёт кладётся после описаний и до сумм: он читает `ops_pins.json`,
        # а суммы обязаны покрывать ОБА артефакта — иначе файл в поставке никем
        # не проверяется и его подмена останется незамеченной.
        try:
            report_path = write_report(out, source,
                                       Path(args.previous).resolve() if args.previous else None)
            report_payload = report_mod.parse(report_path.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001 — причина называется в отказе
            print(f"FAIL: отчёт выпуска не собран: {type(error).__name__}: {error}")
            return 1
        checksums = write_checksums(out, sorted(set(plan.files) | {PINS_ARTIFACT,
                                                                   REPORT_ARTIFACT}))

    print(f"манифест:            {manifest_path}")
    print(f"источник:            {source}")
    print(f"приёмник:            {out if out is not None else '(не задан, предпросмотр)'}")
    print(f"строк в манифесте:   {len(manifest.includes)} включений, {len(manifest.excludes)} исключений")
    if args.dry_run:
        print(f"файлов к переносу:   {len(plan.files)}")
        print("что записано:        ничего (--dry-run)")
    else:
        print(f"файлов перенесено:   {copied}")
    print(f"объём:               {plan.total_bytes} байт ({plan.total_bytes / (1 << 20):.1f} МБ)")
    if checksums is not None:
        print(f"контрольные суммы:   {checksums.name} ({copied} строк)")
    if pins is not None:
        print(f"описания операций:   {pins.name} ({pins_payload.get('ops_total')} операций, "
              f"пин: {pins_payload.get('pin_state')})")
    if report_path is not None:
        print(f"отчёт выпуска:       {report_path.name} "
              f"(версия {report_payload.get('version') or '—'}, "
              f"вердикт: {report_payload.get('verdict') or '—'})")
    elif args.dry_run:
        print(f"описания операций:   {PINS_ARTIFACT} будет записан (--dry-run ничего не пишет)")

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
