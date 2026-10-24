"""Удаление повторяющихся правил exclude из конфигов корпусов.

Найдено: в конфигах накопились дубли правил — `**/.zwork/**` и другие
встречаются по два раза. Появились они от прошлых правок (правила
дописывались, не проверяя наличие).

Вред: дубль правила ничего не ломает, но делает конфиг нечитаемым —
человек видит одно и то же дважды и не понимает, зачем. Кроме того,
дальнейшие правки накапливают новые дубли.

Зачем чистить сейчас: после расширения базового списка часть правил стала
ИЗБЫТОЧНОЙ (они теперь действуют из базового списка). Но убирать их из
файлов нельзя бездумно: правило в файле — это ЯВНОЕ указание для человека,
который откроет конфиг. Поэтому удаляются ТОЛЬКО ДУБЛИ внутри файла,
а не правила, дублирующие базовый список.

Правка консервативная: текст перебирается построчно, порядок и комментарии
сохраняются. Резервные копии делаются перед каждой правкой.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

CONFIGS = [
    "z-batteryservice.yaml",
    "revoap.yaml",
    "md.viewer.yaml",
    "rdpmon.yaml",
    "clean_bom_senior.yaml",
    "fixture.yaml",
]

# Строка правила в файле: `  - "**/что-то/**"` плюс возможный комментарий.
RULE_RE = re.compile(r'^\s*-\s*"([^"]+)"')

print("=" * 78)
print("ПОИСК И УДАЛЕНИЕ ДУБЛЕЙ В КОНФИГАХ")
print("=" * 78)

total_removed = 0
for name in CONFIGS:
    path = REPO / "config" / name
    if not path.exists():
        continue
    original = path.read_text(encoding="utf-8")
    lines = original.split("\n")

    # Дубли считаются в пределах ОДНОЙ секции: одно правило может законно
    # стоять в exclude и в secrets_exclude, это не дубль.
    seen_in_section: dict[str, set[str]] = {}
    current_section = ""
    keep: list[str] = []
    removed: list[str] = []

    for line in lines:
        # Смена секции: строка верхнего уровня вида `exclude:`.
        if line and not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            current_section = line.strip().rstrip(":")
        match = RULE_RE.match(line)
        if match and current_section in ("exclude", "secrets_exclude", "include"):
            rule = match.group(1)
            section_seen = seen_in_section.setdefault(current_section, set())
            if rule in section_seen:
                removed.append(f"{current_section}: {rule}")
                continue
            section_seen.add(rule)
        keep.append(line)

    if not removed:
        print(f"\n  {name}: дублей нет")
        continue

    backup = REPO / ".staging" / f"{name}.before-dedupe-rules"
    if not backup.exists():
        shutil.copy2(path, backup)

    path.write_text("\n".join(keep), encoding="utf-8", newline="\n")
    total_removed += len(removed)
    print(f"\n  {name}: удалено дублей {len(removed)}")
    for r in removed:
        print(f"    {r}")
    print(f"    резервная копия: {backup.name}")
    print(f"    размер: {len(original.encode('utf-8'))} → "
          f"{len(path.read_text(encoding='utf-8').encode('utf-8'))} байт")

print()
print("=" * 78)
print("ПРОВЕРКА ПОСЛЕ ПРАВКИ")
print("=" * 78)
print(f"  всего удалено дублей: {total_removed}")

from collections import Counter  # noqa: E402

from ragraf.config import DEFAULT_EXCLUDES, load as load_config  # noqa: E402

for name in CONFIGS:
    path = REPO / "config" / name
    if not path.exists():
        continue
    # Проверка 1: файл читается.
    try:
        cfg = load_config(path)
    except Exception as exc:
        print(f"  {name}: ОШИБКА ЧТЕНИЯ {type(exc).__name__}: {exc}")
        continue

    # Проверка 2: дублей в файле больше нет.
    raw = path.read_text(encoding="utf-8")
    file_rules = RULE_RE.findall(raw)
    file_dupes = {p: n for p, n in Counter(file_rules).items() if n > 1}

    # Проверка 3: все базовые правила действуют.
    missing_base = [p for p in DEFAULT_EXCLUDES if p not in cfg.exclude]

    print(f"\n  {name}")
    print(f"    правил в объединённом списке: {len(cfg.exclude)}")
    print(f"    дублей в файле: {len(file_dupes)}"
          + (f" — {list(file_dupes)[:3]}" if file_dupes else ""))
    print(f"    базовых правил не хватает: {len(missing_base)}"
          + (f" — {missing_base[:2]}" if missing_base else ""))
    ok = not file_dupes and not missing_base
    print(f"    итог: {'верно' if ok else 'ТРЕБУЕТСЯ ВНИМАНИЕ'}")
