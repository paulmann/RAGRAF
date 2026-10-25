"""Обновление конфигов существующих корпусов: добавление новых языков.

Причина. Расширения добавлены в общую карту (config.py), но у каждого корпуса
есть свой список `languages`, и он фильтрует файлы в corpus.py. Поэтому у
корпусов, подключённых раньше, новые языки не появятся сами.

Что делает скрипт: для каждого корпуса смотрит, какие файлы новых типов там
реально есть, и добавляет только те языки, файлы которых найдены. Пустых
языков в конфиг не попадает: иначе конфиг декларирует то, чего нет.

Списки include и exclude сохраняются: скрипт дописывает недостающее, а не
переписывает файл целиком. Это важно — конфиги уже настроены вручную
(например, в z исключены служебные каталоги).
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import LANG_BY_EXT, load as load_config  # noqa: E402

# Корпуса, подключённые до появления новых языков.
TARGETS = ["z-batteryservice.yaml", "revoap.yaml", "md.viewer.yaml"]

SKIP = {".git", "node_modules", "vendor", "obj", "bin", "publish", "dist",
        "build", ".ragraf", ".zwork", "target", "__pycache__", ".venv", "venv",
        "packages", "TestResults", ".dev_agent"}

# Расширения, которые индексируются как «текст» напрямую (без разбора).
TEXT_LANGS = {"markdown", "sql", "json", "yaml", "toml", "ini", "xml",
              "text", "rst", "batch"}


def scan_languages(root: pathlib.Path) -> dict[str, Counter]:
    """Какие языки и по сколько файлов есть в каталоге корпуса."""
    found: dict[str, Counter] = {}
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts
        except (OSError, ValueError):
            continue
        if any(p in SKIP for p in parts):
            continue
        low_name = path.name.lower()
        if low_name.endswith((".bak", ".old", ".orig", ".tmp", ".save")):
            continue
        ext = path.suffix.lower()
        lang = LANG_BY_EXT.get(ext)
        if not lang:
            continue
        found.setdefault(lang, Counter())[ext] += 1
    return found


print("=" * 78)
print("ОБНОВЛЕНИЕ КОНФИГОВ КОРПУСОВ")
print("=" * 78)

for name in TARGETS:
    config_path = REPO / "config" / name
    if not config_path.exists():
        print(f"\n{name}: конфига нет")
        continue
    cfg = load_config(config_path)
    root = pathlib.Path(str(cfg.root))
    print(f"\n{name}")
    print("-" * 70)
    print(f"  корень: {root}")
    print(f"  языков в конфиге сейчас: {len(cfg.languages)}")
    print(f"    {', '.join(sorted(cfg.languages))}")

    if not root.exists():
        print("  каталога нет — пропуск")
        continue

    found = scan_languages(root)
    current = set(cfg.languages)
    missing = {lang: exts for lang, exts in found.items() if lang not in current}

    if not missing:
        print("  новых языков нет — конфиг актуален")
        continue

    print(f"\n  НАЙДЕНЫ языки, отсутствующие в конфиге: {len(missing)}")
    for lang, exts in sorted(missing.items(),
                             key=lambda kv: -sum(kv[1].values())):
        total = sum(exts.values())
        detail = ", ".join(f"{e}({n})" for e, n in exts.most_common())
        kind = "текстом" if lang in TEXT_LANGS else "разбор деревом"
        print(f"    {lang:<14} {total:>5} файлов  {kind:<15} {detail}")

    # Правка конфига: дописываются недостающие языки и расширения.
    text = config_path.read_text(encoding="utf-8")
    original = text

    # 1. Языки: добавляются в конец списка languages.
    lang_lines = []
    for lang, exts in sorted(missing.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(exts.values())
        lang_lines.append(f"  - {lang}    # файлов: {total}")

    match = re.search(r"^languages:\n((?:  - .*\n)+)", text, re.MULTILINE)
    if not match:
        print("  НЕ НАЙДЕН список languages — конфиг не изменён")
        continue
    block = match.group(1)
    new_block = block + "\n".join(lang_lines) + "\n"
    text = text[: match.start(1)] + new_block + text[match.end(1) :]

    # 2. Include: добавляются расширения новых языков.
    new_includes = []
    existing_inc = {
        m.group(1) for m in re.finditer(r'^  - "\*\*/\*(\.[a-z0-9]+)"', text,
                                        re.MULTILINE)
    }
    for lang, exts in missing.items():
        for ext in sorted(exts):
            if ext not in existing_inc:
                new_includes.append(f'  - "**/*{ext}"    # {lang}')

    match_inc = re.search(r"^include:\n((?:  - .*\n)+)", text, re.MULTILINE)
    if match_inc and new_includes:
        block = match_inc.group(1)
        new_block = block + "\n".join(new_includes) + "\n"
        text = text[: match_inc.start(1)] + new_block + text[match_inc.end(1) :]

    config_path.write_text(text, encoding="utf-8", newline="\n")
    print(f"\n  КОНФИГ ОБНОВЛЁН: +{len(missing)} языков, "
          f"+{len(new_includes)} шаблонов include")
    print(f"    было {len(original)} байт, стало {len(text)} байт")

print()
print("=" * 78)
print("ЧТО ДАЛЬШЕ")
print("=" * 78)
print("""
  Обновление конфига само по себе индекс не меняет: нужна переиндексация.
  Запускать её следует по каждому корпусу отдельно:
    python tools/ragraf.py -p config/<корпус>.yaml reindex

  Проверить результат можно так:
    python tools/ragraf.py -p config/<корпус>.yaml query "..." --op search
""")
