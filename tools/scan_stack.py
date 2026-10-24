"""Сканирование каталогов на состав кода: какие расширения и сколько файлов.

Задача: понять фактический стек разработки в C:\\AutoClaw и C:\\1st_RdpMON\\Service,
чтобы решить, какие языки добавлять в RAGRAF.

Считается по каждому расширению: число файлов, суммарный объём, глубина
вложенности. Исключаются каталоги зависимостей и служебные.

Ничего не меняет: только читает структуру каталогов.
"""

from __future__ import annotations

import pathlib
from collections import defaultdict

REPOS = [
    pathlib.Path(r"C:\AutoClaw"),
    pathlib.Path(r"C:\1st_RdpMON\Service"),
]

# Каталоги, которые не интересуют: зависимости, сборка, кеши, служебные.
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "bower_components", "dist", "build",
    "target", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".idea", ".vscode", "coverage", ".zwork", ".ragraf", "site-packages",
    ".tools", "downloads", "exp_graphson", "exp_try_default_cpg",
    "exp_try_j18_all", "exp_try_j18_neo4jcsv", "export_test_outer",
    "joern-cli", "jdk", "bin", "obj", "packages", "TestResults",
}

for repo in REPOS:
    print("=" * 78)
    print(f"КАТАЛОГ: {repo}")
    print("=" * 78)
    if not repo.exists():
        print("  НЕ СУЩЕСТВУЕТ")
        continue

    by_ext: dict[str, list[int]] = defaultdict(list)   # расширение → [размеры]
    by_ext_dir: dict[str, set[str]] = defaultdict(set)
    total_files = 0
    total_bytes = 0

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
            ext = entry.suffix.lower() or "(без расширения)"
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            by_ext[ext].append(size)
            # Первый уровень вложенности от корня: показывает, где что лежит.
            try:
                rel = entry.relative_to(repo)
                parts = rel.parts
                top = parts[0] if len(parts) > 1 else "(корень)"
            except ValueError:
                top = "?"
            by_ext_dir[ext].add(top)
            total_files += 1
            total_bytes += size

    print(f"\n  всего файлов (без зависимостей): {total_files}")
    print(f"  объём: {total_bytes / 1024 / 1024:.1f} МБ")
    print(f"\n  {'расширение':<18} {'файлов':>8} {'КБ':>10}  где лежит (первые каталоги)")

    # Сортировка по числу файлов: интересны массовые типы.
    rows = sorted(by_ext.items(), key=lambda kv: -len(kv[1]))
    for ext, sizes in rows[:28]:
        count = len(sizes)
        kb = sum(sizes) / 1024
        tops = sorted(by_ext_dir[ext])[:4]
        where = ", ".join(tops)
        if len(by_ext_dir[ext]) > 4:
            where += f" … ещё {len(by_ext_dir[ext]) - 4}"
        print(f"  {ext:<18} {count:>8} {kb:>10.0f}  {where[:60]}")

    others = len(rows) - 28
    if others > 0:
        print(f"\n  … и ещё {others} типов расширений")
    print()
