"""Проверка BOM и CRLF у всех файлов, изменённых в этой задаче.

Запуск: python tools/check_bom_crlf.py

Требование: файлы с кодом должны быть без BOM и с переводами строк LF.
Проверка печатает состояние каждого файла и итог: сколько требуют очистки.
Скрипт ничего не меняет — только сообщает (очистка выполняется инструментом
clean-bom-senior с явными путями).
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]

# Файлы, созданные или изменённые в этой задаче. Список явный: рекурсивный
# обход запрещён правилами очистки.
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
    "editions/EXCLUDED-FROM-COMMUNITY.md",
    "pro/README.md",
    "enterprise/README.md",
    "tools/check_release_manifest.py",
    "docs/03_implementation/RELEASE-PIPELINE.md",
]

# Проверка выполняется на копии файла: сравнение идёт по байтам.
need_cleanup: list[str] = []
missing: list[str] = []
clean: list[str] = []

for rel in FILES:
    path = REPO / rel
    if not path.exists():
        missing.append(rel)
        continue
    data = path.read_bytes()
    has_bom = data.startswith(b"\xef\xbb\xbf")
    has_crlf = b"\r\n" in data
    if has_bom or has_crlf:
        need_cleanup.append(rel)
        marks = []
        if has_bom:
            marks.append("BOM")
        if has_crlf:
            marks.append("CRLF")
        print(f"  требует очистки: {rel}  [{' '.join(marks)}]")
    else:
        clean.append(rel)

print()
print(f"всего проверено:  {len(FILES)}")
print(f"чистых:           {len(clean)}")
print(f"требуют очистки:  {len(need_cleanup)}")
if missing:
    print(f"не найдены ({len(missing)}):")
    for name in missing:
        print(f"  {name}")

if need_cleanup:
    print("\nсписок для очистки (через инструмент clean-bom-senior, явными путями):")
    for name in need_cleanup:
        print(f"  {name}")
