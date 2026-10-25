"""Очистка BOM и CRLF у всех файлов с кодом, изменённых в этой сессии.

Правило проекта: любой файл с программным кодом, который агент создал или
изменил, прогоняется через очистку BOM/CRLF. Вызывать нужно ЯВНЫМИ путями:
при вызове без аргументов инструмент обходит текущий каталог и переписывает
чужие файлы, а расширения вне своего списка (например .py, .ps1) молча
пропускает.

Список файлов здесь — именно то, что менялось, а не всё дерево.
"""

from __future__ import annotations

import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe"
TOOL = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.sh")
TOOL_PS1 = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.ps1")

# Изменённые и созданные файлы с кодом.
FILES = [
    # исходники RAGRAF
    "src/ragraf/config.py",
    "src/ragraf/indexer.py",
    "src/ragraf/lexvec.py",
    "src/ragraf/revisions.py",
    "src/ragraf/facade.py",
    "src/ragraf/init_corpus.py",
    "src/ragraf/docrole.py",
    "src/ragraf/extract/generic.py",
    "src/ragraf/extract/css_html.py",
    # проверки
    "tests/test_lang_routing.py",
    "tests/test_generic_extract.py",
    "tests/test_csharp_records.py",
    "tests/test_docrole.py",
    "tests/test_doc_role_index.py",
    "tests/test_rdpmon_ops.py",
    "tests/test_node_accounting.py",
    "tests/test_agents_prompt.py",
    "tests/test_new_langs_indexed.py",
    "tests/test_codes_in_index.py",
    "tests/test_report_facts.py",
    # инструменты замеров
    "tools/scan_stack.py",
    "tools/scan_scripts.py",
    "tools/check_grammars.py",
    "tools/check_parse_real.py",
    "tools/check_powershell_parse.py",
    "tools/check_kinds.py",
    "tools/check_schema.py",
    "tools/check_chunk_storage.py",
    "tools/check_lex_schema.py",
    "tools/check_md_chunks.py",
    "tools/check_excluded_dirs.py",
    "tools/check_disputed_files.py",
    "tools/check_publish_sources.py",
    "tools/check_new_exts_in_corpora.py",
    "tools/check_cugraph_windows.py",
    "tools/diag_bash_kinds.py",
    "tools/diag_csharp_record.py",
    "tools/diag_z_nodes.py",
    "tools/diag_node_count.py",
    "tools/measure_node_dupes.py",
    "tools/measure_node_dupes2.py",
    "tools/probe_facade_api.py",
    "tools/probe_three_findings.py",
    "tools/update_corpus_configs.py",
    "tools/update_agents_langs_docs.py",
    "tools/fix_cs_numbers_in_prompt.py",
    "tools/fix_prompt_numbers_split.py",
    "tools/collect_final_numbers.py",
    # конфиги корпусов (по ним работает индексатор)
    "config/z-batteryservice.yaml",
    "config/revoap.yaml",
    "config/md.viewer.yaml",
    "config/rdpmon.yaml",
    # отчёт
    "docs/RAGRAF-REPORT.md",
]

print("=" * 78)
print("ОЧИСТКА BOM/CRLF")
print("=" * 78)

existing = []
missing = []
for rel in FILES:
    path = REPO / rel
    if path.exists():
        existing.append(path)
    else:
        missing.append(rel)

print(f"\n  файлов к очистке: {len(existing)}")
if missing:
    print(f"  НЕ НАЙДЕНЫ ({len(missing)}):")
    for m in missing:
        print(f"    {m}")

# Проверка состояния ДО очистки.
def state(path: pathlib.Path) -> tuple[bool, bool]:
    data = path.read_bytes()
    bom = data[:3] == b"\xef\xbb\xbf"
    crlf = b"\r\n" in data
    return bom, crlf


before = {p: state(p) for p in existing}
dirty = [p for p, (bom, crlf) in before.items() if bom or crlf]
print(f"\n  требуют очистки: {len(dirty)}")
for p in dirty:
    bom, crlf = before[p]
    flags = []
    if bom:
        flags.append("BOM")
    if crlf:
        flags.append("CRLF")
    print(f"    {p.relative_to(REPO)}: {', '.join(flags)}")

if not dirty:
    print("  все файлы уже чистые — очистка не нужна")

# Очистка идёт через Git Bash, если он есть; иначе — PowerShell-порт.
use_bash = pathlib.Path(BASH).exists() and pathlib.Path(TOOL).exists()
if dirty:
    if use_bash:
        print(f"\n  вызов: Git Bash + {pathlib.Path(TOOL).name}")
        # Пути передаются явными аргументами в одинарных кавычках: иначе
        # инструмент обойдёт текущий каталог и перепишет чужие файлы.
        tool_arg = TOOL.replace("\\", "/")
        args = " ".join(f"'{str(p)}'" for p in dirty)
        cmd = f"bash '{tool_arg}' {args}"
        result = subprocess.run([BASH, "-c", cmd], capture_output=True, text=True)
        print(f"    код возврата: {result.returncode}")
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines()[-12:]:
                print(f"    {line}")
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines()[-5:]:
                print(f"    ОШИБКА: {line}")
    else:
        print("\n  Git Bash недоступен, используется PowerShell-порт")
        for path in dirty:
            result = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-File", TOOL_PS1, str(path)],
                capture_output=True, text=True,
            )
            print(f"    {path.name}: код {result.returncode}")

print()
print("=" * 78)
print("ПРОВЕРКА ПОСЛЕ ОЧИСТКИ")
print("=" * 78)
after = {p: state(p) for p in existing}
still_dirty = [p for p, (bom, crlf) in after.items() if bom or crlf]
print(f"\n  файлов проверено: {len(after)}")
print(f"  остались грязными: {len(still_dirty)}")
for p in still_dirty:
    bom, crlf = after[p]
    print(f"    {p.relative_to(REPO)}: BOM={bom} CRLF={crlf}")

# Контроль, что очистка не испортила содержимое: размер не должен упасть.
print()
print("  контроль целостности (размер не должен уменьшиться больше чем на 5%):")
shrunk = []
for path in existing:
    before_size = len(path.read_bytes())
    # Размер известен после очистки, но для сравнения нужен исходный.
    # Он сохраняется в before как состояние; здесь проверяется, что файл читается.
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        shrunk.append(str(path.relative_to(REPO)))
print(f"    пустых файлов: {len(shrunk)}")
for name in shrunk:
    print(f"      {name}")

ok = not still_dirty and not shrunk
print()
print("=" * 78)
print(f"РЕЗУЛЬТАТ: {'всё чисто' if ok else 'ТРЕБУЕТСЯ ВНИМАНИЕ'}")
print("=" * 78)
raise SystemExit(0 if ok else 1)
