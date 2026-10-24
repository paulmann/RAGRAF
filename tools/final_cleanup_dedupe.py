"""Финальная очистка BOM/CRLF: файлы этой сессии.

Список задан перечислением. Рекурсивный прогон запрещён правилом проекта:
он обошёл бы текущий каталог и переписал посторонние файлы, а расширения
вне своего списка (`.py`, `.ps1`) молча пропустил бы.

Проверяются оба инварианта: отсутствие BOM и отсутствие CRLF. Дополнительно
подтверждается, что очистка не сделала файлы пустыми и что модули
по-прежнему компилируются.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
BASH = r"C:\Program Files\Git\bin\bash.exe"
TOOL = r"C:\AutoClaw\Clean_BOM_Senior\clean-bom-senior.sh"
TOOL_PS1 = r"C:\AutoClaw\Clean_BOM_Senior\clean-bom-senior.ps1"

FILES = [
    # исходники этой сессии
    "src/ragraf/dedupe.py",
    "src/ragraf/corpus.py",
    "src/ragraf/config.py",
    "src/ragraf/indexer.py",
    "src/ragraf/settings.py",
    "src/ragraf/lexvec.py",
    "src/ragraf/revisions.py",
    "src/ragraf/facade.py",
    "src/ragraf/docrole.py",
    "src/ragraf/init_corpus.py",
    "src/ragraf/extract/generic.py",
    "src/ragraf/extract/css_html.py",
    # интерфейс
    "admin/static/app.js",
    # проверки этой сессии
    "tests/test_dedupe.py",
    "tests/test_dedupe_stability.py",
    "tests/test_dedupe_effect.py",
    "tests/test_dedupe_prompt.py",
    "tests/check_admin_dedupe.py",
    # проверки предыдущей сессии (менялись в этой)
    "tests/test_agents_prompt.py",
    "tests/test_report_facts.py",
    "tests/test_lang_routing.py",
    "tests/test_generic_extract.py",
    "tests/test_csharp_records.py",
    "tests/test_docrole.py",
    "tests/test_doc_role_index.py",
    "tests/test_rdpmon_ops.py",
    "tests/test_node_accounting.py",
    "tests/test_new_langs_indexed.py",
    "tests/test_codes_in_index.py",
    "tests/test_css_layer.py",
    "tests/test_class_usage.py",
    # инструменты этой сессии
    "tools/measure_dedupe.py",
    "tools/find_doc_dupes.py",
    "tools/measure_dedupe_rules.py",
    "tools/diag_dedupe_scope.py",
    "tools/diag_admin_state.py",
    "tools/enable_dedupe_revoap.py",
    "tools/add_enum_ui.py",
    "tools/add_dedupe_prompt.py",
    "tools/update_report_corpus_table.py",
    "tools/update_report_langs.py",
    "tools/check_last_files.py",
    "tools/check_environment.py",
    "tools/final_cleanup.py",
    "tools/final_verification.py",
    # конфиги
    "config/revoap.yaml",
    "config/z-batteryservice.yaml",
    "config/md.viewer.yaml",
    "config/rdpmon.yaml",
    "catalog/settings.json",
    # документы
    "docs/RAGRAF-REPORT.md",
]

paths = [REPO / rel for rel in FILES]
existing = [p for p in paths if p.exists()]
missing = [rel for rel, p in zip(FILES, paths) if not p.exists()]

print("=" * 78)
print("ФИНАЛЬНАЯ ОЧИСТКА")
print("=" * 78)
print(f"\n  в списке: {len(FILES)}, существует: {len(existing)}")
for rel in missing:
    print(f"    НЕТ: {rel}")


def state(p: pathlib.Path) -> tuple[bool, bool, int]:
    data = p.read_bytes()
    return data[:3] == b"\xef\xbb\xbf", b"\r\n" in data, len(data)


dirty = [p for p in existing if any(state(p)[:2])]
print(f"  требуют очистки: {len(dirty)}")
for p in dirty:
    bom, crlf, size = state(p)
    flags = ", ".join(f for f, on in (("BOM", bom), ("CRLF", crlf)) if on)
    print(f"    {p.relative_to(REPO)}: {flags} ({size} б)")

# Прогон идёт явными путями, пакетами: командная строка Windows ограничена.
use_bash = pathlib.Path(BASH).exists() and pathlib.Path(TOOL).exists()
BATCH = 12
ok = 0
errors: list[str] = []

if use_bash:
    tool_arg = TOOL.replace("\\", "/")
    for start in range(0, len(existing), BATCH):
        part = existing[start : start + BATCH]
        args = " ".join(f"'{p}'" for p in part)
        result = subprocess.run([BASH, "-c", f"bash '{tool_arg}' {args}"],
                                capture_output=True, text=True)
        if result.returncode == 0:
            ok += 1
        else:
            errors.append(f"пакет {start}: {(result.stderr or '')[:120]}")
    total = (len(existing) + BATCH - 1) // BATCH
    print(f"\n  пакетов выполнено: {ok} из {total}")
else:
    print("\n  Git Bash недоступен — используется PowerShell-порт")
    for path in existing:
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
             TOOL_PS1, str(path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            ok += 1
    print(f"  обработано: {ok}")

for err in errors:
    print(f"    ошибка: {err}")

print()
print("=" * 78)
print("КОНТРОЛЬ")
print("=" * 78)
bad = [(p.relative_to(REPO), *state(p)) for p in existing if any(state(p)[:2])]
print(f"  проверено файлов: {len(existing)}")
print(f"  с BOM или CRLF:   {len(bad)}")
for rel, bom, crlf, size in bad:
    print(f"    {rel}: BOM={bom} CRLF={crlf} ({size} б)")

empty = [p.relative_to(REPO) for p in existing if state(p)[2] == 0]
print(f"  пустых файлов:    {len(empty)}")

# Компиляция: очистка не должна повредить Python-файлы.
compile_check = subprocess.run(
    [sys.executable, "-m", "compileall", "-q",
     str(REPO / "src" / "ragraf"), str(REPO / "tests"), str(REPO / "tools")],
    capture_output=True, text=True,
)
print(f"  компиляция:       код {compile_check.returncode}")
if compile_check.returncode:
    print(f"    {(compile_check.stdout or '')[:400]}")

# Разбор JS: проверяется, что правка интерфейса не сломала синтаксис.
js = REPO / "admin" / "static" / "app.js"
node = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
print(f"  проверка app.js:  код {node.returncode}"
      + (f": {(node.stderr or '')[:120]}" if node.returncode else ""))

good = (not bad and not empty and compile_check.returncode == 0
        and node.returncode == 0)
print()
print(f"  РЕЗУЛЬТАТ: {'всё чисто' if good else 'ТРЕБУЕТСЯ ВНИМАНИЕ'}")
raise SystemExit(0 if good else 1)
