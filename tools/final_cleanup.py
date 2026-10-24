"""Финальная очистка BOM/CRLF: строго по списку файлов сессии.

ВАЖНО — исправление предыдущей версии. Первый вариант этого скрипта собирал
файлы обходом рабочего дерева по расширениям и нашёл 713 файлов, включая
зависимости (`.ort_gpu_probe/numpy`, `onnxruntime`) и лицензии JDK
(`.tools/jdk/legal`). Их очистка была бы порчей чужих файлов:
перевод строк в них не наш, и менять его незачем.

Проверено, что ущерба не случилось: `.ort_gpu_probe/numpy/__init__.py` и
`.ort_gpu_probe/onnxruntime/__init__.py` сохранили CRLF, время изменения
у них прежнее (10:49 утра, а не полночь). Изменились только мои файлы.

Правило проекта прямо запрещает рекурсивный прогон: «Всегда передавать
явные пути. Никогда не запускать без аргументов». Здесь список задан
перечислением, и только он подаётся инструменту.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe"
TOOL = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.sh")
TOOL_PS1 = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.ps1")

# Только то, что создано или изменено в этой сессии. Ничего лишнего.
FILES = [
    # исходники RAGRAF
    "src/ragraf/config.py", "src/ragraf/indexer.py", "src/ragraf/lexvec.py",
    "src/ragraf/revisions.py", "src/ragraf/facade.py",
    "src/ragraf/init_corpus.py", "src/ragraf/docrole.py",
    "src/ragraf/extract/generic.py", "src/ragraf/extract/css_html.py",
    # проверки
    "tests/test_lang_routing.py", "tests/test_generic_extract.py",
    "tests/test_csharp_records.py", "tests/test_docrole.py",
    "tests/test_doc_role_index.py", "tests/test_rdpmon_ops.py",
    "tests/test_node_accounting.py", "tests/test_agents_prompt.py",
    "tests/test_new_langs_indexed.py", "tests/test_codes_in_index.py",
    "tests/test_report_facts.py", "tests/test_css_layer.py",
    "tests/test_class_usage.py",
    # инструменты сессии
    "tools/scan_stack.py", "tools/scan_scripts.py", "tools/check_grammars.py",
    "tools/check_parse_real.py", "tools/check_powershell_parse.py",
    "tools/check_kinds.py", "tools/check_schema.py",
    "tools/check_chunk_storage.py", "tools/check_lex_schema.py",
    "tools/check_md_chunks.py", "tools/check_excluded_dirs.py",
    "tools/check_disputed_files.py", "tools/check_publish_sources.py",
    "tools/check_new_exts_in_corpora.py", "tools/check_cugraph_windows.py",
    "tools/diag_bash_kinds.py", "tools/diag_csharp_record.py",
    "tools/diag_z_nodes.py", "tools/diag_node_count.py",
    "tools/diag_css_regression.py", "tools/measure_node_dupes.py",
    "tools/measure_node_dupes2.py", "tools/probe_facade_api.py",
    "tools/probe_three_findings.py", "tools/probe_class_usage.py",
    "tools/find_chunk_store.py", "tools/update_corpus_configs.py",
    "tools/update_agents_langs_docs.py", "tools/update_report_revisions.py",
    "tools/fix_cs_numbers_in_prompt.py", "tools/fix_prompt_numbers_split.py",
    "tools/fix_css_tests.py", "tools/fix_class_usage_test.py",
    "tools/collect_final_numbers.py", "tools/clean_changed_files.py",
    "tools/run_cleanup.py", "tools/final_verification.py",
    "tools/add_doc_role.py", "tools/final_cleanup.py",
    # конфиги корпусов
    "config/z-batteryservice.yaml", "config/revoap.yaml",
    "config/md.viewer.yaml", "config/rdpmon.yaml",
    # отчёт и статья
    "docs/RAGRAF-REPORT.md",
]

paths = [REPO / rel for rel in FILES]
missing = [rel for rel, p in zip(FILES, paths) if not p.exists()]
paths = [p for p in paths if p.exists()]

print("=" * 78)
print("ФИНАЛЬНАЯ ОЧИСТКА (строго по списку)")
print("=" * 78)
print(f"\n  файлов в списке: {len(FILES)}")
print(f"  существует:      {len(paths)}")
if missing:
    print(f"  отсутствуют ({len(missing)}):")
    for rel in missing:
        print(f"    {rel}")


def state(p: pathlib.Path) -> tuple[bool, bool]:
    data = p.read_bytes()
    return data[:3] == b"\xef\xbb\xbf", b"\r\n" in data


dirty = [p for p in paths if any(state(p))]
print(f"\n  требуют очистки: {len(dirty)}")
for p in dirty:
    bom, crlf = state(p)
    flags = ", ".join(f for f, on in (("BOM", bom), ("CRLF", crlf)) if on)
    print(f"    {p.relative_to(REPO)}: {flags}")

# Пакетами: командная строка Windows ограничена, путей много.
use_bash = pathlib.Path(BASH).exists() and pathlib.Path(TOOL).exists()
BATCH = 12
targets = paths
ok = 0
errors: list[tuple[int, str]] = []

if use_bash:
    tool_arg = TOOL.replace("\\", "/")
    for start in range(0, len(targets), BATCH):
        part = targets[start : start + BATCH]
        args = " ".join(f"'{p}'" for p in part)
        result = subprocess.run([BASH, "-c", f"bash '{tool_arg}' {args}"],
                                capture_output=True, text=True)
        if result.returncode == 0:
            ok += 1
        else:
            errors.append((start, (result.stderr or result.stdout)[:160]))
    total_batches = (len(targets) + BATCH - 1) // BATCH
    print(f"\n  пакетов выполнено: {ok} из {total_batches}")
    for start, err in errors:
        print(f"    ошибка на позиции {start}: {err}")
else:
    print("\n  Git Bash недоступен — используется PowerShell-порт")
    for path in targets:
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
             TOOL_PS1, str(path)],
            capture_output=True, text=True,
        )
        ok += 1 if result.returncode == 0 else 0
    print(f"  обработано: {ok}")

print()
print("=" * 78)
print("КОНТРОЛЬ")
print("=" * 78)
bad = [(p.relative_to(REPO), *state(p)) for p in paths if any(state(p))]
print(f"  проверено файлов: {len(paths)}")
print(f"  с BOM или CRLF:   {len(bad)}")
for rel, bom, crlf in bad:
    print(f"    {rel}: BOM={bom} CRLF={crlf}")

empty = [p.relative_to(REPO) for p in paths
         if not p.read_text(encoding="utf-8", errors="replace").strip()]
print(f"  пустых файлов:    {len(empty)}")

check = subprocess.run(
    [sys.executable, "-m", "compileall", "-q", str(REPO / "src" / "ragraf")],
    capture_output=True, text=True,
)
print(f"  компиляция:       код {check.returncode}")

good = not bad and not empty and check.returncode == 0
print()
print(f"  РЕЗУЛЬТАТ: {'всё чисто' if good else 'ТРЕБУЕТСЯ ВНИМАНИЕ'}")
raise SystemExit(0 if good else 1)
