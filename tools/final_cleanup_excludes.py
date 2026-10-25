"""Финальная очистка BOM/CRLF: файлы задачи об исключениях.

Список задан перечислением: рекурсивный прогон запрещён правилом проекта —
он обошёл бы текущий каталог и переписал посторонние файлы (в прошлый раз
так захватило 713 чужих файлов: зависимости numpy и лицензии JDK), а
расширения вне своего списка (.py, .ps1) молча пропустил бы.

Проверяются оба инварианта: нет BOM, нет CRLF. Дополнительно
подтверждается, что модули компилируются.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
# Путь к модулям добавляется СРАЗУ: импорты из ragraf нужны ниже, и первая
# версия скрипта ставила sys.path после них — импорт падал.
sys.path.insert(0, str(REPO / "src"))

BASH = r"C:\Program Files\Git\bin\bash.exe"
TOOL = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.sh")
TOOL_PS1 = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.ps1")

FILES = [
    # исходники этой задачи
    "src/ragraf/config.py",
    "src/ragraf/init_corpus.py",
    "src/ragraf/corpus.py",
    "src/ragraf/dedupe.py",
    "src/ragraf/indexer.py",
    "src/ragraf/settings.py",
    # проверки этой задачи
    "tests/test_exclude_defaults.py",
    "tests/test_exclude_configs.py",
    "tests/test_excludes_prompt.py",
    "tests/test_excludes_summary.py",
    "tests/test_dedupe.py",
    "tests/test_dedupe_stability.py",
    "tests/test_dedupe_effect.py",
    "tests/test_dedupe_prompt.py",
    # изменённые проверки прошлых задач
    "tests/test_report_facts.py",
    "tests/test_agents_prompt.py",
    # инструменты этой задачи
    "tools/check_service_excludes.py",
    "tools/find_missing_ragraf_exclude.py",
    "tools/measure_service_dirs.py",
    "tools/check_exclude_lists_divergence.py",
    "tools/compare_exclude_lists.py",
    "tools/measure_disputed_dirs.py",
    "tools/check_cache_logs_trap.py",
    "tools/check_pattern_matching.py",
    "tools/check_php_in_scan.py",
    "tools/check_case_sensitivity.py",
    "tools/measure_safe_excludes.py",
    "tools/check_rule_sections.py",
    "tools/clean_duplicate_rules.py",
    "tools/check_staging_dir.py",
    "tools/sync_exclude_lists.py",
    "tools/add_excludes_prompt.py",
    "tools/fix_excludes_prompt_numbers.py",
    # инструменты прошлой задачи (менялись сейчас)
    "tools/final_verification.py",
    "tools/final_cleanup_dedupe.py",
    # конфиги корпусов
    "config/z-batteryservice.yaml",
    "config/revoap.yaml",
    "config/md.viewer.yaml",
    "config/rdpmon.yaml",
    "config/clean_bom_senior.yaml",
    "config/fixture.yaml",
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
        ok += 1 if result.returncode == 0 else 0
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

compile_check = subprocess.run(
    [sys.executable, "-m", "compileall", "-q",
     str(REPO / "src" / "ragraf"), str(REPO / "tests"), str(REPO / "tools")],
    capture_output=True, text=True,
)
print(f"  компиляция:       код {compile_check.returncode}")
if compile_check.returncode:
    print(f"    {(compile_check.stdout or '')[:400]}")

# Разбор конфигов: правка исключений не должна была их сломать.
from ragraf.config import load as load_config  # noqa: E402

bad_configs = []
for name in ("z-batteryservice.yaml", "revoap.yaml", "md.viewer.yaml",
             "rdpmon.yaml", "clean_bom_senior.yaml", "fixture.yaml"):
    try:
        cfg = load_config(REPO / "config" / name)
        if not cfg.exclude:
            bad_configs.append(f"{name}: пустой список исключений")
    except Exception as exc:
        bad_configs.append(f"{name}: {type(exc).__name__}")
print(f"  конфиги читаются: {'да' if not bad_configs else bad_configs}")

good = (not bad and not empty and compile_check.returncode == 0
        and not bad_configs)
print()
print(f"  РЕЗУЛЬТАТ: {'всё чисто' if good else 'ТРЕБУЕТСЯ ВНИМАНИЕ'}")
raise SystemExit(0 if good else 1)
