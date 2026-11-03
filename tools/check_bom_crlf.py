"""Чистота текста: BOM и переводы строк у файлов, изменённых в задаче.

Запуск:
    python tools/check_bom_crlf.py                      # список этой задачи
    python tools/check_bom_crlf.py --paths a.py b.md    # явный список
    python tools/check_bom_crlf.py --paths-from list.txt

ПОЧЕМУ ПРОВЕРКА ПЕРЕПИСАНА (31.10.2026). Прежняя версия отвечала на вопрос
«есть ли в байтах 0D 0A» и считала дефектом всё, что ответило «да». Измерено
на живом дереве: так числились `assets/social-preview.png`,
`catalog/secrets.sqlite` и `workspace/rev-0099.cpg.bin/cpg.bin` — в них `0D 0A`
не перевод строки, а содержимое двоичного формата. Отдельно: проверка не
различала файлы, у которых CRLF функционален (`.bat`, `.cmd`, `.reg`, данные
RFC 4180 в `.csv`/`.tsv`), и записи с зафиксированным `sha256` (`archive/**`,
копии `community/**`): «исправить» их значило сломать сверку с источником.

Теперь решение принимает `tools/text_hygiene.py`: он смотрит фактические байты
и называет причину. Проверка печатает пять групп — чистые, требующие очистки,
только BOM, защищённые причиной и отклонённые (не текст), — и код возврата 1
только за первую из них. Сама она по-прежнему НИЧЕГО не меняет: очистка —
`tools/text_hygiene.py --paths ... --apply`.

Список `FILES` ниже — история задачи: он собран правками этапов И1…И32, и в
нём стоят причины, по которым файл в него попал. Он сохранён, потому что по
нему видно, что уже осматривалось; новым файлам он не нужен — их подают
через `--paths`.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import text_hygiene as hygiene  # noqa: E402

FILES = [
    # ядро RAGRAF
    "src/ragraf/facade.py",
    "src/ragraf/store.py",
    "src/ragraf/revisions.py",
    "src/ragraf/indexer.py",
    "src/ragraf/cpg.py",
    "src/ragraf/extract/php_ast.py",
    "src/ragraf/watch.py",
    "src/ragraf/config.py",
    "src/ragraf/settings.py",
    "src/ragraf/admin_server.py",
    # Словарь переводов панели: без него новый модуль остался бы вне штатной
    # очистки — своя же проверка гигиены его не осматривала.
    "src/ragraf/i18n.py",
    "src/ragraf/mcp_server.py",
    "src/ragraf/cli.py",
    # панель управления
    "admin/static/style.css",
    "admin/static/app.js",
    # тесты
    "tests/test_threshold.py",
    # Проверки, добавленные 12 октября 2026 (текст из индекса, адресация хита,
    # честный бюджет). Без них новый код остался бы вне штатной очистки.
    "tests/test_text_from_index.py",
    "src/ragraf/lexvec.py",
    "tools/Invoke-ProjectDoctrine.ps1",
    "docs/02_architecture/ARCHITECTURE.md",
    "docs/AGENTS-context.md",
    "docs/03_implementation/INTEGRATION.md",
    "AGENTS.md",
    # инструменты
    "tools/audit_defects.py",
    "tools/bench_index_modes.py",
    "tools/check_threshold.py",
    "tools/ragraf-admin.py",
    "tools/check_admin.py",
    "tools/check_admin_project_settings.py",
    "tools/snapshot_admin.py",
    "tools/fix_admin_repo.py",
    "tools/fix_admin_check.py",
    "tools/update_agents_ops.py",
    "tools/fix_agents_table.py",
    "tools/check_bom_crlf.py",
    # Этап И17: документы единственной поставки и два уровня лицензии.
    "tools/check_single_delivery.py",
    # Этап И21: состояние переводов README (33-я проверка чекера). Новый файл
    # вне списка остался бы вне штатной очистки — так уже было с i18n.py.
    "tools/check_translations.py",
    # Этап И22: объявление состояний переводов стало данными, а не словарём в
    # коде. Файл публикуется (docs/ входит в поставку целиком), поэтому он
    # обязан попасть и в штатную очистку — иначе гигиена BOM/CRLF его не
    # осматривала бы, а вместе с ним и регулярные выражения состояний.
    "docs/03_implementation/translations.json",
    # конфигурация корпусов
    "config/z-batteryservice.yaml",
    "config/revoap.yaml",
    "config/fixture.yaml",
    # файл настроек панели
    "catalog/settings.json",
    # Юридические метаданные и проверка лицензии (ПРОМТ-И1, 24.10.2026):
    # без этих записей новые файлы остались бы вне штатной очистки.
    "LICENSE",
    "NOTICE",
    "CLA.md",
    "SUPPORT.md",
    "README.md",
    "community/README.de.md",
    "community/README.es.md",
    "community/README.hi.md",
    "community/README.zh.md",
    "pyproject.toml",
    "docs/03_implementation/LICENSING.md",
    "docs/skills/ragraf-core/reference/repair.md",
    "tools/check_licensing.py",
    # Обвязка поставки (ПРОМТ-И2, 24.10.2026): документ источников и проверка.
    # Сам community/** в список НЕ входит намеренно: там побайтовые копии
    # публичного main, и любая «очистка» изменила бы их хэши, то есть сломала
    # бы сверку с источником. Их состояние проверяется сверкой хэшей.
    "docs/03_implementation/RELEASE-SOURCES.md",
    "tools/check_community_wrapper.py",
    # Конвейер поставки (ПРОМТ-И3, 24.10.2026). Обвязка community/** в список
    # по-прежнему НЕ входит: очистка изменила бы хэши, а они зафиксированы
    # таблицей источников. Пять файлов обвязки, чьи тексты лицензии правились в
    # этом этапе, помечены в таблице признаком «нет» — то есть их отличие от
    # публичного main названо, а не спрятано.
    "tools/release/manifest.toml",
    "tools/release/relcommon.py",
    "tools/release/build_release.py",
    "tools/release/verify_release.py",
    "tools/release/gate_release.py",
    "tools/release/editions_report.py",
    "tools/release/replace_staging_tree.ps1",
    "editions/editions.toml",
    "editions/README.md",
    "editions/legacy-v0.1.0/README.md",
    "archive/README.md",
    "tools/keep_cut.py",
    "editions/EXCLUDED-FROM-COMMUNITY.md",
    "enterprise/PRO-TIER.md",
    "enterprise/README.md",
    "enterprise/inventory.toml",
    "enterprise/ENTERPRISE-DELIVERY.md",
    "tools/check_release_manifest.py",
    "docs/03_implementation/RELEASE-PIPELINE.md",
    # Домашняя страница сайта (ПРОМТ-И4.5, 24.10.2026): новый файл, без записи
    # в этом списке он остался бы вне штатной очистки — своя проверка его не
    # осматривала бы, а именно его читают первым.
    "docs/index.md",
    # Внешний доступ (ПРОМТ-И7, 24.10.2026): два новых модуля, проверка, страница
    # панели и документ слоя. Без этих записей новые файлы остались бы вне
    # штатной очистки BOM/CRLF, и своя же проверка их не осматривала бы.
    "src/ragraf/sso.py",
    "src/ragraf/siem.py",
    "src/ragraf/edition_baseline.json",
    "src/ragraf/signing.py",
    "tests/test_external_access.py",
    "tests/test_editions.py",
    "admin/external-access.html",
    "docs/03_implementation/EXTERNAL-ACCESS.md",
    # Закрытый контур (ПРОМТ-И8, 25.10.2026): два новых модуля, проверка и
    # документ. Без этих записей новые файлы остались бы вне штатной очистки, и
    # своя же проверка гигиены их не осматривала бы.
    "src/ragraf/license_offline.py",
    "src/ragraf/attest.py",
    "tests/test_offline_attest.py",
    "docs/03_implementation/OFFLINE-AND-ATTESTATION.md",
    # Контур качества (ПРОМТ-И9, 25.10.2026): новый модуль, проверка и документ
    # слоя. Без этих записей новые файлы остались бы вне штатной очистки, и своя
    # же проверка гигиены их не осматривала бы.
    "src/ragraf/quality.py",
    "tests/test_quality_loop.py",
    "docs/03_implementation/EDITIONS-PRO.md",
    # ПРОМТ-И10, 25.10.2026: файлы, которые этот этап правил, но прежний список
    # не осматривал. Без них своя же проверка гигиены молчала бы именно о тех
    # файлах, которые менялись.
    "tests/test_editions.py",
    "docs/05_article/RAGRAF_ARTICLE.md",
    "docs/skills/ragraf-core/reference/operations.md",
    # ПРОМТ-И11, 25.10.2026: объявление вариантов навыка и два инструмента.
    # Без этих записей своя же проверка гигиены не осматривала бы именно те
    # файлы, которые этап создал.
    "docs/skills/ragraf-core/VARIANTS.json",
    "docs/skills/ragraf-core/SKILL.md",
    "tools/check_skill_variants.py",
    "tools/sync_skill_variants.py",
    # ПРОМТ-И13, 25.10.2026: три инструмента этапа и документы, которые он правил.
    # Без этих записей своя же проверка гигиены молчала бы именно о тех файлах,
    # которые этап создал и изменил.
    "tools/check_line_refs.py",
"tools/check_tools_classes.py",
    "tools/rev_snapshot.py",
    "tools/count_surface.py",
    "AGENTS-TRAPS.md",
    "docs/03_implementation/HARNESS_INTEGRATION.md",
    "docs/03_implementation/AUTOCLAW_INTEGRATION.md",
    "docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md",
    "harness/skills/ragraf-core/reference/repair.md",
    # ПРОМТ-И14, 25.10.2026: правки этапа — снимок ревизии, свойство «мусор
    # оболочки», три файла с путями машины и таблица источников обвязки.
    "tests/test_shell_artifact.py",
    # Продолжение И14, 25.10.2026: три исхода проверки чисел в документах.
    # Без записи своя же проверка гигиены молчала бы о новом файле этапа.
    "tests/test_documented_numbers.py",
    "tools/release/editions_report.py",
    "tools/check_release_manifest.py",
    "tools/Invoke-ProjectDoctrine.ps1",
    "src/ragraf/route.py",
    "tools/Fix-LowIntegritySandbox.ps1",
    "community/install.ps1",
    "docs/03_implementation/RELEASE-SOURCES.md",
    # Проза переводов (этап И28, 27.10.2026): объявление допусков и сама
    # проверка. Без этих записей новые файлы этапа остались бы вне штатной
    # очистки BOM/CRLF, и своя же проверка гигиены о них молчала бы.
    "docs/03_implementation/prose.json",
    "tools/check_prose_profile.py",
    # Адреса переводов (этап И29, 27.10.2026): объявление строгости и сама
    # проверка. Причина та же, и она измерена на прошлом этапе: файл, которого
    # нет в этом списке, гигиеной не осматривается, и проверка о нём молчит.
    "docs/03_implementation/addresses.json",
    "tools/check_document_addresses.py",
    # Блоки команд переводов (этап И30, 27.10.2026): объявление правила разбора и
    # сама проверка. Причина та же и измерена дважды: файл, которого нет в этом
    # списке, гигиеной не осматривается, и проверка о нём молчит.
    "docs/03_implementation/commands.json",
    "tools/check_command_blocks.py",
    # Ячейки таблиц переводов (этап И31, 27.10.2026): объявление правила разбора,
    # видов колонок и строгости плюс сама проверка. Причина та же и измерена
    # трижды: файл, которого нет в этом списке, гигиеной не осматривается, и
    # проверка о нём молчит.
    "docs/03_implementation/tables.json",
    "tools/check_table_rows.py",
    # Пункты списков (этап И32, 27.10.2026): объявление правила разбора, видов
    # пунктов, перечня списков и якорей по языку плюс сама проверка. Причина та же
    # и измерена четырежды: файл, которого нет в этом списке, гигиеной не
    # осматривается, и проверка о нём молчит.
    "docs/03_implementation/lists.json",
    "tools/check_list_items.py",
]

def collect(paths: list[str]) -> tuple[hygiene.Plan, int]:
    """Осмотреть пути модулем политики. Возвращает план и число отсутствующих."""
    missing = 0
    existing = []
    for rel in paths:
        path = pathlib.Path(rel)
        if not path.is_absolute():
            path = REPO / rel
        if not path.exists():
            missing += 1
            continue
        existing.append(path)
    return hygiene.plan(existing, REPO), missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Чистота текста: BOM и переводы строк.")
    parser.add_argument("--paths", nargs="+", default=[], help="явные пути (файлы, не каталоги)")
    parser.add_argument("--paths-from", help="файл со списком путей, по одному в строке")
    args = parser.parse_args(argv)

    chosen = list(args.paths)
    if args.paths_from:
        source = pathlib.Path(args.paths_from)
        if not source.exists():
            print(f"нет файла списка: {source}", file=sys.stderr)
            return 2
        chosen.extend(line.strip() for line in source.read_text(encoding="utf-8").splitlines()
                      if line.strip() and not line.startswith("#"))
    listing = chosen or FILES

    plan, missing = collect(listing)
    print("=" * 78)
    print("ЧИСТОТА ТЕКСТА: BOM И ПЕРЕВОДЫ СТРОК")
    print("=" * 78)
    print()
    print(f"  осмотрено:                 {len(listing) - missing}")
    print(f"  чистых:                    {len(plan.already)}")
    print(f"  требуют очистки:           {len(plan.clean)}")
    print(f"  только BOM (CRLF остаётся): {len(plan.bom_only)}")
    print(f"  защищены причиной:         {len(plan.keep)}")
    print(f"  отклонены (не текст):      {len(plan.refused)}")
    if missing:
        print(f"  нет в дереве:              {missing}")

    for title, group in (("ТРЕБУЮТ ОЧИСТКИ", plan.clean), ("ТОЛЬКО BOM", plan.bom_only),
                         ("ЗАЩИЩЕНЫ", plan.keep), ("ОТКЛОНЕНЫ", plan.refused)):
        if not group:
            continue
        print()
        print(f"  {title}:")
        for finding in group:
            print(f"    {finding.rel:<58} {finding.flags:<22} {finding.reason}")

    if plan.changes:
        print()
        print("  очистка — явными путями и только после просмотра списка:")
        print("    python tools/text_hygiene.py --paths " + " ".join(
            str(f.rel) for f in plan.changes[:8]) + (" ..." if len(plan.changes) > 8 else ""))
        print("  добавить --apply, чтобы выполнить.")
    print()
    print(f"  РЕЗУЛЬТАТ: {'требуется очистка' if plan.changes else 'чисто'}")
    return 1 if plan.changes else 0


if __name__ == "__main__":
    sys.exit(main())
