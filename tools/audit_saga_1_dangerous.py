"""Аудит SagaAI шаг 1: опасные примитивы выполнения кода и запросов.

Собираются ФАКТЫ: где именно в коде проекта есть потенциально опасные
конструкции, с путём и номером строки. Ничего не оценивается «на глаз» —
только найденные строки.

Ищутся:
  * выполнение кода и процессов: exec, eval, compile, subprocess, os.system,
    Popen, __import__;
  * SQL: подстановка значений в строку запроса (f-строка, %, конкатенация),
    use `text()` в SQLAlchemy;
  * десериализация: pickle, yaml.load без SafeLoader;
  * файлы: open() с путём из переменной, распаковка архивов.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")

# Каталоги, которые не относятся к исходникам проекта.
SKIP_DIRS = {".venv", ".git", ".dev_agent", "__pycache__", ".ragraf",
             "node_modules", "history", "storage"}

# Что ищется: имя проверки -> регулярное выражение.
PATTERNS: dict[str, str] = {
    # --- выполнение кода и процессов ---
    "exec(": r"\bexec\s*\(",
    "eval(": r"\beval\s*\(",
    "compile(": r"\bcompile\s*\(",
    "subprocess": r"\bsubprocess\.(run|Popen|call|check_output|check_call)",
    "os.system": r"\bos\.system\s*\(",
    "os.popen": r"\bos\.popen\s*\(",
    "__import__": r"\b__import__\s*\(",
    "importlib": r"importlib\.(import_module|util\.spec_from_file_location)",
    # --- SQL ---
    "SQL f-строка": r"(?:execute|executemany|text)\s*\(\s*f[\"']",
    "SQL конкатенация": r"(?:execute|executemany)\s*\([^)]*\+",
    "SQL %-формат": r"(?:execute|executemany)\s*\([^)]*%",
    "SQL text()": r"\btext\s*\(\s*f?[\"']",
    # --- десериализация ---
    "pickle": r"\bpickle\.(load|loads)\s*\(",
    "yaml.load": r"\byaml\.load\s*\(",
    "marshal": r"\bmarshal\.(load|loads)\s*\(",
    # --- файлы и архивы ---
    "распаковка архива": r"\b(?:zipfile|tarfile)\.",
    "скачивание по URL": r"(?:requests|urllib)\.(?:get|post|urlopen)\s*\(",
    # --- аутентификация ---
    "сравнение пароля": r"(?:password|passwd|pwd|secret)\w*\s*==\s*",
    "hmac.compare_digest": r"hmac\.compare_digest",
    # --- прочее ---
    "shell=True": r"shell\s*=\s*True",
    "verify=False": r"verify\s*=\s*False",
    "отключение проверок": r"(?:--insecure|ssl_verify\s*=\s*False)",
}


def files_of_interest() -> list[pathlib.Path]:
    """Файлы исходников проекта."""
    out = []
    for p in SAGA.rglob("*.py"):
        if set(p.parts) & SKIP_DIRS:
            continue
        rel = p.relative_to(SAGA)
        # Тесты тоже полезны, но отдельным списком.
        out.append(rel)
    return sorted(out)


files = files_of_interest()
src_files = [f for f in files if not str(f).startswith("tests")]
test_files = [f for f in files if str(f).startswith("tests")]
print(f"  файлов исходников: {len(src_files)}")
print(f"  файлов тестов:     {len(test_files)}")

print()
print("=" * 78)
print("НАЙДЕННЫЕ КОНСТРУКЦИИ (только исходники, без тестов)")
print("=" * 78)

results: dict[str, list[tuple[str, int, str]]] = {}
for name, pattern in PATTERNS.items():
    regex = re.compile(pattern)
    hits: list[tuple[str, int, str]] = []
    for rel in src_files:
        path = SAGA / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append((str(rel).replace("\\", "/"), line_no,
                             line.strip()[:100]))
    if hits:
        results[name] = hits

# Печатается по проверкам, с ограничением вывода.
for name, hits in sorted(results.items(), key=lambda kv: -len(kv[1])):
    print(f"\n  === {name}: {len(hits)} ===")
    for path, line_no, line in hits[:6]:
        print(f"    {path}:{line_no}")
        print(f"      {line}")
    if len(hits) > 6:
        print(f"    ... и ещё {len(hits) - 6}")

print()
print("=" * 78)
print("СВОДКА ПО ПРОВЕРКАМ")
print("=" * 78)
for name, hits in sorted(results.items(), key=lambda kv: -len(kv[1])):
    files_count = len({h[0] for h in hits})
    print(f"  {name:<24} вхождений {len(hits):<5} в файлах {files_count}")

print()
print("=" * 78)
print("ЧЕГО НЕ НАЙДЕНО (тоже результат)")
print("=" * 78)
for name in PATTERNS:
    if name not in results:
        print(f"  {name}: не найдено")
