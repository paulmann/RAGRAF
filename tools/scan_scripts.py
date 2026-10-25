"""Точный подсчёт скриптов и кода в обоих каталогах.

Первый обзор показал массовые типы (php, js, css), но среди них не видно
скриптов автоматизации. Задача — найти именно их: .ps1, .sh, .bat, .cmd,
а также C# (.cs), который в 1st_RdpMON/Service составляет основу проекта.

Отдельно считается, где эти файлы лежат: интересны реальные проекты
разработки, а не копии и служебные каталоги.
"""

from __future__ import annotations

import pathlib
from collections import defaultdict

REPOS = [
    pathlib.Path(__file__).resolve().parents[1].parent,
    pathlib.Path(__file__).resolve().parents[1].parent.parent / "1st_RdpMON" / "Service",
]

SKIP_DIRS = {
    ".git", "node_modules", "vendor", "bower_components", "dist", "build",
    "target", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".idea", ".vscode", "coverage", ".ragraf", "site-packages",
    "joern-cli", "jdk", "obj", "packages", "TestResults", "publish",
}

# Целевые расширения: то, о чём спрашивал пользователь, плюс найденный C#.
TARGETS = {
    ".ps1": "PowerShell",
    ".psm1": "PowerShell модуль",
    ".sh": "shell (bash)",
    ".bash": "shell (bash)",
    ".bat": "batch (CMD)",
    ".cmd": "batch (CMD)",
    ".cs": "C#",
    ".csproj": "проект C#",
    ".sln": "решение C#",
    ".pl": "Perl",
    ".py": "Python",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".toml": "TOML",
    ".ini": "INI",
    ".cfg": "конфигурация",
    ".conf": "конфигурация",
    ".env": "окружение",
    ".sql": "SQL",
    ".vue": "Vue",
    ".ts": "TypeScript",
    ".tsx": "TypeScript JSX",
    ".jsx": "JavaScript JSX",
    ".dart": "Dart",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".lua": "Lua",
    ".r": "R",
    ".m": "Objective-C",
    ".cpp": "C++",
    ".cc": "C++",
    ".h": "C/C++ заголовок",
    ".hpp": "C++ заголовок",
    ".c": "C",
}

for repo in REPOS:
    print("=" * 78)
    print(f"КАТАЛОГ: {repo}")
    print("=" * 78)
    if not repo.exists():
        print("  НЕ СУЩЕСТВУЕТ")
        continue

    found: dict[str, list[tuple[str, int]]] = defaultdict(list)
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
                if not entry.is_file():
                    continue
            except OSError:
                continue
            ext = entry.suffix.lower()
            if ext not in TARGETS:
                continue
            try:
                rel = str(entry.relative_to(repo)).replace("\\", "/")
                size = entry.stat().st_size
            except (OSError, ValueError):
                continue
            found[ext].append((rel, size))

    print(f"\n  {'расширение':<12} {'язык':<18} {'файлов':>8} {'КБ':>9}")
    print("  " + "-" * 52)
    total = 0
    for ext in sorted(found, key=lambda e: -len(found[e])):
        files = found[ext]
        total += len(files)
        print(f"  {ext:<12} {TARGETS[ext]:<18} {len(files):>8} "
              f"{sum(s for _, s in files) / 1024:>9.0f}")
    print(f"\n  всего целевых файлов: {total}")

    # Подробно по скриптам: где лежат и что внутри.
    for ext in (".ps1", ".sh", ".bat", ".cmd", ".psm1"):
        if ext not in found:
            continue
        print(f"\n  --- {ext} ({TARGETS[ext]}): {len(found[ext])} файлов ---")
        for rel, size in sorted(found[ext])[:25]:
            print(f"    {rel}  ({size} б)")
        if len(found[ext]) > 25:
            print(f"    … и ещё {len(found[ext]) - 25}")

    # C#: показываются только каталоги верхнего уровня, иначе список огромен.
    if ".cs" in found:
        print(f"\n  --- .cs (C#): {len(found['.cs'])} файлов ---")
        by_top: dict[str, int] = defaultdict(int)
        for rel, _ in found[".cs"]:
            top = rel.split("/")[0] if "/" in rel else "(корень)"
            by_top[top] += 1
        for top, count in sorted(by_top.items(), key=lambda kv: -kv[1])[:12]:
            print(f"    {top}: {count} файлов")
    print()
