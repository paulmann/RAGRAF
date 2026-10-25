"""Синхронизация списка init с базовым списком исключений.

Зачем. Найдено расхождение: `EXCLUDE_DIRS` в init_corpus.py (28 каталогов)
и `DEFAULT_EXCLUDES` в config.py (28 шаблонов) — два независимых списка
одного и того же. Такое расхождение и было причиной проблемы: корпус,
созданный НЕ через init, не получал исключений для кешей и окружений.

Что делается: `EXCLUDE_DIRS` остаётся описанием «что исключать и почему»
(он нужен init для вывода и для сканирования), но теперь он обязан
СОГЛАСОВЫВАТЬСЯ с базовым списком. Скрипт:
  1. проверяет, что каждый каталог из базового списка есть в EXCLUDE_DIRS;
  2. добавляет отсутствующие с пояснением;
  3. отдельно проверяет каталоги, которые НАМЕРЕННО не в базовом списке,
     и оставляет их в EXCLUDE_DIRS (init их исключает для конкретного
     корпуса — это правильное поведение).

Правка идёт заменой конкретного блока словаря, а не переписыванием файла.
Скрипт идемпотентен.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

TARGET = REPO / "src" / "ragraf" / "init_corpus.py"
text = TARGET.read_text(encoding="utf-8")

from ragraf.config import DEFAULT_EXCLUDES, NON_DEFAULT_EXCLUDES  # noqa: E402
from ragraf.init_corpus import EXCLUDE_DIRS  # noqa: E402

# Извлекаются имена каталогов из базового списка.
def dir_of(pattern: str) -> str | None:
    m = re.fullmatch(r"\*\*/([A-Za-z0-9_.\-]+)/\*\*", pattern.strip())
    return m.group(1) if m else None


base_dirs = {d for d in (dir_of(p) for p in DEFAULT_EXCLUDES) if d}
print("=" * 78)
print("СРАВНЕНИЕ СПИСКОВ")
print("=" * 78)
print(f"  каталогов в базовом списке: {len(base_dirs)}")
print(f"  каталогов в EXCLUDE_DIRS:   {len(EXCLUDE_DIRS)}")

missing = sorted(base_dirs - set(EXCLUDE_DIRS))
print(f"\n  есть в базовом, НЕТ в init ({len(missing)}):")
for name in missing:
    print(f"    {name}")

# Каталоги из init, которых нет в базовом списке: они исключаются только
# при создании корпуса. Это НЕ ошибка — так и задумано для `bin`, `obj`,
# `publish`, потому что по имени их назначение неоднозначно.
only_init = sorted(set(EXCLUDE_DIRS) - base_dirs)
print(f"\n  есть в init, НЕТ в базовом ({len(only_init)}):")
for name in only_init:
    kind = "намеренно" if name in NON_DEFAULT_EXCLUDES else "ПРОВЕРИТЬ"
    print(f"    {name:<20} {kind}")

doubtful = [n for n in only_init if n not in NON_DEFAULT_EXCLUDES]
if doubtful:
    print(f"\n  ВНИМАНИЕ: необъяснённых расхождений {len(doubtful)}: {doubtful}")

if not missing:
    print("\n  расхождений, требующих правки, нет")
    raise SystemExit(0)

# --- правка: недостающие каталоги дописываются в EXCLUDE_DIRS ---
REASONS = {
    ".dev_agent": "служебный каталог агента: резервные копии и журналы",
    ".pi": "служебный каталог среды агента",
    ".nuget": "кеш пакетов NuGet",
    "packages": "зависимости NuGet",
    "target": "собранные файлы Java и Rust",
    "bower_components": "зависимости JavaScript",
}

block_start = text.find("EXCLUDE_DIRS: dict[str, str] = {")
if block_start < 0:
    raise SystemExit("НЕ НАЙДЕН блок EXCLUDE_DIRS")
block_end = text.find("\n}\n", block_start)
if block_end < 0:
    raise SystemExit("НЕ НАЙДЕН конец блока EXCLUDE_DIRS")

block = text[block_start : block_end + 3]
lines_to_add = []
for name in missing:
    reason = REASONS.get(name, "служебный каталог")
    lines_to_add.append(f'    "{name}": "{reason}",')

insert = "\n".join(lines_to_add)
# Строки вставляются перед закрывающей скобкой словаря.
new_block = block[: block.rfind("}")] + insert + "\n}"

# Добавляется пояснение, откуда взялись новые строки.
comment = (
    "    # --- согласовано с DEFAULT_EXCLUDES (config.py) ---\n"
    "    # Эти каталоги должны быть в списке init, иначе `init` не исключит\n"
    "    # их при первом осмотре, хотя базовый список их уже закрывает.\n"
)
new_block = new_block.replace(insert, comment + insert)

text = text[:block_start] + new_block + text[block_end + 3 :]
TARGET.write_text(text, encoding="utf-8", newline="\n")
print(f"\n  дописано каталогов: {len(missing)}")

# Проверка результата.
import importlib  # noqa: E402

import ragraf.init_corpus as ic  # noqa: E402

importlib.reload(ic)
after = set(ic.EXCLUDE_DIRS)
still = sorted(base_dirs - after)
print(f"  каталогов в EXCLUDE_DIRS после правки: {len(after)}")
print(f"  осталось расхождений: {len(still)}")
if still:
    print(f"    {still}")
else:
    print("  списки согласованы")
