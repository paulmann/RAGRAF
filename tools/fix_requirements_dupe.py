"""Исправление дубля в requirements.txt (моя ошибка).

ЧТО СЛУЧИЛОСЬ. Скрипт обновления делал две вещи одновременно:
  1. заменял строки версий;
  2. вставлял заголовок с пояснением перед первыми зависимостями.

Замена уже дала новые строки, а вставка заголовка взяла блок от позиции
первой зависимости — и этот блок попал в файл ВТОРОЙ раз:

    10: streamlit>=1.65.0        <- после замены
    ...
    15: streamlit>=1.65.0        <- вставлено повторно

Итог: первые четыре зависимости дублируются. Само по себе это не ломает
pip (повтор требования безвреден), но файл грязный, и передавать такое
разработчику нельзя.

ПРАВКА: файл восстанавливается из бэкапа, затем заголовок вставляется
ОДИН раз — без одновременной замены строк в том же проходе. Замены
делаются ПОСЛЕ вставки, чтобы позиции не пересекались.
"""

from __future__ import annotations

import pathlib
import re
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "requirements.txt"
BACKUP = TARGET.with_suffix(".txt.bak-versions")

if not BACKUP.exists():
    raise SystemExit(f"НЕТ бэкапа: {BACKUP}")

# Восстановление исходного файла.
shutil.copy2(BACKUP, TARGET)
text = (TARGET.read_text(encoding="utf-8")).replace("\r\n", "\n")
print(f"  файл восстановлен из бэкапа")
print(f"  строк: {len(text.splitlines())}")

# --- Шаг 1: заголовок вставляется в самое начало ---
HEADER = """# SagaAI platform dependencies.
#
# NOTE ON VERSION PINS. `streamlit` is pinned with `>=`, not `==`, on
# purpose: with an exact pin (`streamlit==1.62.0`) the command
# `pip install --upgrade streamlit` reports "Requirement already satisfied"
# and the package is never updated even though newer versions exist.
# A minimum version keeps upgrades possible while still requiring a version
# known to work.
#
# `paramiko` is listed and must be installed: without it the SSH connector
# imports but cannot connect to remote hosts.

"""

lines = text.splitlines()
# Отбрасываются прежние комментарии заголовка, если были.
while lines and lines[0].strip().startswith("#"):
    lines.pop(0)
while lines and not lines[0].strip():
    lines.pop(0)
body = "\n".join(lines) + "\n"

# --- Шаг 2: замены версий делаются ПОСЛЕ, по одной строке ---
REPLACEMENTS = [
    ("streamlit==1.62.0", "streamlit>=1.65.0"),
    ("sqlalchemy>=2.0", "sqlalchemy>=2.1"),
    ("cryptography>=41.0", "cryptography>=50.0"),
    ("paramiko>=3.0", "paramiko>=5.0"),
]
applied = []
for old, new in REPLACEMENTS:
    if old in body:
        body = body.replace(old, new, 1)
        applied.append(f"{old} -> {new}")

final = HEADER + body
TARGET.write_text(final, encoding="utf-8", newline="\n")

print()
print("  применённые замены:")
for item in applied:
    print(f"    - {item}")

# --- Проверка: дублей нет, версии новые ---
after = TARGET.read_text(encoding="utf-8")
dep_lines = [ln.strip() for ln in after.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
dupes = [name for name in set(
    re.match(r"([\w-]+)", ln).group(1) for ln in dep_lines if
    re.match(r"([\w-]+)", ln)
) if sum(1 for ln in dep_lines
         if re.match(r"([\w-]+)", ln).group(1) == name) > 1]

print()
print("=== ИТОГОВЫЙ requirements.txt ===")
for i, line in enumerate(after.splitlines(), 1):
    print(f"  {i:>3}: {line}")

print()
print("  проверки:")
checks = [
    (not dupes, f"дублей нет (найдено: {dupes or 'нет'})"),
    ("streamlit>=1.65.0" in after, "streamlit обновлён до >=1.65.0"),
    ("sqlalchemy>=2.1" in after, "sqlalchemy обновлён до >=2.1"),
    ("cryptography>=50.0" in after, "cryptography обновлён до >=50.0"),
    ("paramiko>=5.0" in after, "paramiko требует >=5.0"),
    (after.count("streamlit") == 2, "streamlit упомянут дважды "
     "(заголовок + зависимость)"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")
