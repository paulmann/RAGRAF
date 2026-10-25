"""Прогон инструмента очистки по правилу проекта: явные пути.

Предыдущая проверка показала, что файлы уже чистые (нет BOM, LF). Но правило
требует прогонять инструмент, а не только проверять состояние: инструмент —
единственный авторизованный способ нормализации, и его результат проверяется
в своём репозитории, а не принимается на веру.

Запуск идёт явными путями. Без аргументов инструмент обошёл бы текущий каталог
и переписал посторонние файлы; кроме того, рекурсивный режим работает только
по списку расширений (php css js txt xml htm html) и .py/.ps1 молча пропустил бы.

Бинарные файлы и .bat/.cmd не подаются: инструмент нормализует CRLF→LF
у всего, что получил явным путём.
"""

from __future__ import annotations

import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe"
TOOL = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.sh")
TOOL_PS1 = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.ps1")

# Те же файлы, что в списке изменённых.
FILES = [
    "src/ragraf/config.py", "src/ragraf/indexer.py", "src/ragraf/lexvec.py",
    "src/ragraf/revisions.py", "src/ragraf/facade.py", "src/ragraf/init_corpus.py",
    "src/ragraf/docrole.py", "src/ragraf/extract/generic.py",
    "src/ragraf/extract/css_html.py",
    "tests/test_lang_routing.py", "tests/test_generic_extract.py",
    "tests/test_csharp_records.py", "tests/test_docrole.py",
    "tests/test_doc_role_index.py", "tests/test_rdpmon_ops.py",
    "tests/test_node_accounting.py", "tests/test_agents_prompt.py",
    "tests/test_new_langs_indexed.py", "tests/test_codes_in_index.py",
    "tests/test_report_facts.py",
    "tools/scan_stack.py", "tools/scan_scripts.py", "tools/check_grammars.py",
    "tools/check_parse_real.py", "tools/check_powershell_parse.py",
    "tools/check_kinds.py", "tools/check_schema.py", "tools/check_chunk_storage.py",
    "tools/check_lex_schema.py", "tools/check_md_chunks.py",
    "tools/check_excluded_dirs.py", "tools/check_disputed_files.py",
    "tools/check_publish_sources.py", "tools/check_new_exts_in_corpora.py",
    "tools/check_cugraph_windows.py", "tools/diag_bash_kinds.py",
    "tools/diag_csharp_record.py", "tools/diag_z_nodes.py",
    "tools/diag_node_count.py", "tools/measure_node_dupes.py",
    "tools/measure_node_dupes2.py", "tools/probe_facade_api.py",
    "tools/probe_three_findings.py", "tools/update_corpus_configs.py",
    "tools/update_agents_langs_docs.py", "tools/fix_cs_numbers_in_prompt.py",
    "tools/fix_prompt_numbers_split.py", "tools/collect_final_numbers.py",
    "config/z-batteryservice.yaml", "config/revoap.yaml",
    "config/md.viewer.yaml", "config/rdpmon.yaml",
    "docs/RAGRAF-REPORT.md",
]

paths = [REPO / rel for rel in FILES if (REPO / rel).exists()]
print(f"файлов к прогону: {len(paths)}")

use_bash = pathlib.Path(BASH).exists() and pathlib.Path(TOOL).exists()
# Прогон идёт пакетами: командная строка Windows ограничена ~32 КБ,
# и одна команда на 53 длинных пути к этой границе подходит.
BATCH = 12
ok_batches = 0
failed = []

if use_bash:
    tool_arg = TOOL.replace("\\", "/")
    for start in range(0, len(paths), BATCH):
        part = paths[start : start + BATCH]
        args = " ".join(f"'{p}'" for p in part)
        cmd = f"bash '{tool_arg}' {args}"
        result = subprocess.run([BASH, "-c", cmd], capture_output=True, text=True)
        if result.returncode == 0:
            ok_batches += 1
        else:
            failed.append((start, result.returncode,
                           (result.stderr or result.stdout)[:200]))
    print(f"  пакетов выполнено: {ok_batches} из {(len(paths) + BATCH - 1) // BATCH}")
    for start, code, err in failed:
        print(f"    пакет с {start}: код {code}: {err}")
else:
    print("  Git Bash недоступен — используется PowerShell-порт")
    for path in paths:
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
             TOOL_PS1, str(path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            failed.append((str(path), result.returncode, "")[:3])
    print(f"  файлов обработано, ошибок: {len(failed)}")

print()
print("=" * 78)
print("КОНТРОЛЬ: BOM И CRLF ПОСЛЕ ПРОГОНА")
print("=" * 78)
bad = []
for path in paths:
    data = path.read_bytes()
    bom = data[:3] == b"\xef\xbb\xbf"
    crlf = b"\r\n" in data
    if bom or crlf:
        bad.append((path.relative_to(REPO), bom, crlf))
print(f"  проверено файлов: {len(paths)}")
print(f"  с BOM или CRLF:   {len(bad)}")
for rel, bom, crlf in bad:
    print(f"    {rel}: BOM={bom} CRLF={crlf}")

print()
print(f"  РЕЗУЛЬТАТ: {'всё чисто' if not bad else 'ЕСТЬ ПРОБЛЕМЫ'}")
raise SystemExit(0 if not bad else 1)
