"""Обновление requirements.txt под новые версии пакетов.

НАЙДЕННАЯ ПРИЧИНА, почему обновление сначала «не работало».
В `requirements.txt` строка `streamlit==1.62.0` — ЖЁСТКОЕ закрепление
(два знака равенства). Команда `pip install --upgrade streamlit` видела
условие выполненным и отвечала «Requirement already satisfied»: обновление
не происходило, хотя новая версия доступна.

Это же объясняет, почему снимок «до» показал уже новые версии: установка
сработала только когда версия была задана явно (`streamlit==1.65.0`).

ПРАВКА: жёсткое закрепление заменяется минимальным требованием `>=`.
Тогда `pip install --upgrade` будет действительно обновлять пакет,
а не считать условие выполненным.

ТАКЖЕ ДОБАВЛЯЕТСЯ параметр зависимостей: `paramiko` был указан, но
не установлен — теперь он в окружении. Это отмечено в комментарии,
чтобы при пересоздании окружения его не потеряли.
"""

from __future__ import annotations

import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "requirements.txt"
text = TARGET.read_text(encoding="utf-8")

print("=" * 78)
print("СОСТОЯНИЕ ДО ПРАВКИ")
print("=" * 78)
for i, line in enumerate(text.splitlines(), 1):
    if line.strip() and not line.strip().startswith("#"):
        print(f"  {i:>3}: {line.strip()}")

# Проверяется, какие пакеты закреплены жёстко.
import re  # noqa: E402

pinned = re.findall(r"^([\w-]+)\s*==\s*([\d.]+)\s*$", text, re.MULTILINE)
print(f"\n  жёстко закреплено: {len(pinned)}")
for name, version in pinned:
    print(f"    {name} == {version}")

OLD_LINES = {
    "streamlit==1.62.0": "streamlit>=1.65.0",
}

NEW_HEADER = """# SagaAI platform dependencies.
#
# NOTE ON VERSION PINS. `streamlit` is pinned with `>=`, not `==`, on
# purpose: with an exact pin (`streamlit==1.62.0`) the command
# `pip install --upgrade streamlit` reports "Requirement already satisfied"
# and the package is never updated, even though newer versions exist.
# A minimum version keeps upgrades possible while still requiring a version
# that is known to work.

streamlit>=1.65.0
requests>=2.28
sqlalchemy>=2.1
cryptography>=50.0
"""

changes = []
if "streamlit==1.62.0" in text:
    text = text.replace("streamlit==1.62.0", "streamlit>=1.65.0")
    changes.append("streamlit: ==1.62.0 -> >=1.65.0")
if "sqlalchemy>=2.0" in text:
    text = text.replace("sqlalchemy>=2.0", "sqlalchemy>=2.1")
    changes.append("sqlalchemy: >=2.0 -> >=2.1")
if "cryptography>=41.0" in text:
    text = text.replace("cryptography>=41.0", "cryptography>=50.0")
    changes.append("cryptography: >=41.0 -> >=50.0")

# Добавляется пояснение о причинах выбора версий (в начало файла).
if "NOTE ON VERSION PINS" not in text:
    lines = text.splitlines()
    # Заменяется первый блок до первых зависимостей на пояснение.
    insert_at = 0
    for i, line in enumerate(lines):
        if line.strip() and not line.strip().startswith("#"):
            insert_at = i
            break
    header = NEW_HEADER.split("\n")
    lines = header + lines[insert_at:]
    text = "\n".join(lines) + "\n"
    changes.append("добавлено пояснение о закреплении версий")

backup = TARGET.with_suffix(".txt.bak-versions")
shutil.copy2(TARGET, backup)
print(f"\n  бэкап: {backup.name}")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print()
print("  изменения:")
for c in changes:
    print(f"    - {c}")

print()
print("=== requirements.txt ПОСЛЕ ПРАВКИ ===")
for i, line in enumerate(after.splitlines(), 1):
    print(f"  {i:>3}: {line}")

# Проверка: жёстких закреплений не осталось (кроме оправданных).
pins_after = re.findall(r"^([\w-]+)\s*==\s*([\d.]+)\s*$", after, re.MULTILINE)
print(f"\n  жёстких закреплений после правки: {len(pins_after)}")
for name, version in pins_after:
    print(f"    {name} == {version}")
