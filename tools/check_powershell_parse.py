"""Почему у PowerShell половина файлов с ошибками разбора.

Установлено: из 12 файлов .ps1 разобралось 6. Для проекта, где PowerShell —
основной язык автоматизации, это важно понять: то ли грамматика слабая,
то ли файлы содержат нестандартный синтаксис.

Проверяются все найденные .ps1 и .psm1 с указанием файлов,
на которых разбор ломается.
"""

from __future__ import annotations

import pathlib
from tree_sitter_language_pack import get_parser  # type: ignore

REPOS = [pathlib.Path(__file__).resolve().parents[1].parent, pathlib.Path(__file__).resolve().parents[1].parent.parent / "1st_RdpMON" / "Service"]
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target", ".venv",
    "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "coverage", ".ragraf", "site-packages", "joern-cli", "jdk", "obj",
    "packages", "TestResults", "publish", ".tools",
}

files: list[pathlib.Path] = []
for repo in REPOS:
    if not repo.exists():
        continue
    stack = [repo]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name in SKIP_DIRS:
                        continue
                    stack.append(entry)
                    continue
            except OSError:
                continue
            if entry.suffix.lower() in (".ps1", ".psm1"):
                files.append(entry)

print("=" * 78)
print(f"РАЗБОР ВСЕХ POWERSHELL-ФАЙЛОВ: {len(files)}")
print("=" * 78)

parser = get_parser("powershell")
good: list[pathlib.Path] = []
bad: list[tuple[pathlib.Path, str]] = []

for path in sorted(files):
    try:
        data = path.read_bytes()
        tree = parser.parse(data)
    except Exception as exc:
        bad.append((path, f"исключение {type(exc).__name__}"))
        continue
    if tree.root_node.has_error:
        # Ищется первый узел с ошибкой: по нему видна причина.
        error_node = None
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "ERROR" or node.is_missing:
                error_node = node
                break
            stack.extend(node.children)
        if error_node is not None:
            line = error_node.start_point[0] + 1
            # Строка кода с ошибкой: показывает, что именно не разобрано.
            try:
                text = data.decode("utf-8", errors="replace").splitlines()
                snippet = text[line - 1].strip()[:70] if line <= len(text) else ""
            except Exception:
                snippet = ""
            bad.append((path, f"строка {line}: {snippet}"))
        else:
            bad.append((path, "ошибка без явного узла"))
    else:
        good.append(path)

print(f"\n  разобрано без ошибок: {len(good)}")
print(f"  с ошибками: {len(bad)}")

print("\n  --- файлы с ошибками ---")
for path, reason in bad[:30]:
    rel = str(path)
    if len(rel) > 75:
        rel = "…" + rel[-72:]
    print(f"    {rel}")
    print(f"      {reason}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
share = len(good) / len(files) * 100 if files else 0
print(f"""
  Разбирается без ошибок: {len(good)} из {len(files)} ({share:.0f}%).

  Причины ошибок видны в списке выше. Типичные для PowerShell:
    * параметры с атрибутами и сложные блоки param(...);
    * here-strings и вложенные кавычки;
    * классы PowerShell (class ...) — их грамматика поддерживает неполно.

  Практическое решение: PowerShell подключается как язык С РАЗБОРОМ,
  но с оговоркой. Даже при ошибке разбора файл попадает в индекс текстовыми
  чанками, то есть поиск по нему работает. Узлы (функции) по таким файлам
  не появятся, и это честнее, чем выдумывать структуру.
""")
