"""Что за скрипты есть в уже подключённых корпусах.

Выяснилось важное: расширения добавлены в карту языков, но у каждого корпуса
в конфиге есть список `languages`, и он фильтрует файлы. Поэтому у существующих
корпусов (z.batteryservice.ru, revoap.com, MD.Viewer) новые языки не появятся,
пока их конфиги не обновлены.

Прежде чем обновлять, надо измерить: а есть ли там вообще такие файлы.
Обновлять конфиг ради нуля файлов — бессмысленная работа.
"""

from __future__ import annotations

import pathlib
from collections import Counter

CORPORA = {
    "z.batteryservice.ru": pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru",
    "revoap.com": pathlib.Path(__file__).resolve().parents[1].parent / "revoap.com",
    "MD.Viewer": pathlib.Path(__file__).resolve().parents[1].parent / "MD.Viewer",
    "RAGRAF": pathlib.Path(__file__).resolve().parents[1],
    "Agents.Creator": pathlib.Path(__file__).resolve().parents[1].parent / "Agents.Creator",
    "Clean_BOM_Senior": pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior",
}

# Расширения, которые появились в карте языков после сканирования стека.
NEW_EXTS = {
    ".ps1": "powershell", ".psm1": "powershell", ".psd1": "powershell",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".bat": "batch", ".cmd": "batch",
    ".cs": "csharp", ".csproj": "xml", ".sln": "text",
    ".pl": "perl", ".pm": "perl",
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".go": "go", ".lua": "lua", ".rb": "ruby", ".java": "java",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini",
    ".xml": "xml", ".manifest": "xml", ".props": "xml",
    ".sql": "sql", ".md": "markdown", ".markdown": "markdown",
    ".rst": "rst", ".txt": "text",
}

# Каталоги, которые индексатор корпуса всё равно отсекает.
SKIP = {".git", "node_modules", "vendor", "obj", "bin", "publish", "dist",
        "build", ".ragraf", ".zwork", "target", "__pycache__", ".venv", "venv",
        "packages", "TestResults", ".dev_agent"}

print("=" * 78)
print("СКРИПТЫ И КОД В УЖЕ ПОДКЛЮЧЁННЫХ КОРПУСАХ")
print("=" * 78)

for name, root in CORPORA.items():
    print(f"\n{name}")
    print("-" * 70)
    if not root.exists():
        print("  каталога нет")
        continue
    found: Counter[str] = Counter()
    by_lang: Counter[str] = Counter()
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts
        except (OSError, ValueError):
            continue
        if any(p in SKIP for p in parts):
            continue
        if path.suffix.lower() not in NEW_EXTS:
            continue
        # Резервные копии не считаются: они и не индексируются.
        low = path.name.lower()
        if low.endswith((".bak", ".old", ".orig", ".tmp", ".save")):
            continue
        found[path.suffix.lower()] += 1
        by_lang[NEW_EXTS[path.suffix.lower()]] += 1

    if not found:
        print("  новых типов файлов нет — обновлять конфиг нечего")
        continue

    print(f"  {'язык':<14} {'файлов':>7}   расширения")
    print("  " + "-" * 54)
    for lang, count in by_lang.most_common():
        exts = sorted(e for e, l in NEW_EXTS.items() if l == lang and e in found)
        detail = ", ".join(f"{e}({found[e]})" for e in exts)
        print(f"  {lang:<14} {count:>7}   {detail}")
    print(f"\n  всего файлов новых типов: {sum(found.values())}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если в корпусе есть файлы новых типов, его конфиг нужно обновить:
    * добавить языки в список languages;
    * добавить расширения в include.
  После этого переиндексация подхватит их.

  Если файлов нет — обновлять нечего, и это тоже правильный ответ:
  пустой список языков не должен попадать в конфиг.
""")
