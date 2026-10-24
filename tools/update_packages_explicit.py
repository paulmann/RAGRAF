"""Обновление пакетов до конкретных версий.

ПОЧЕМУ ПРЕДЫДУЩАЯ ПОПЫТКА НЕ СРАБОТАЛА (замерено):
    `pip install --upgrade streamlit` -> "Requirement already satisfied"
    Причина: requirements.txt требует `streamlit>=1.62.0`, а установлена
    ровно 1.62.0. Условие выполнено, поэтому pip ничего не меняет.
    Флаг `--upgrade` поднимает версию только в пределах ограничений,
    заданных требованиями.

РЕШЕНИЕ: целевые версии задаются явно — те, что pip сам предложил
в режиме проверки (`--dry-run`):

    Markdown 3.10.3 -> 3.11
    SQLAlchemy 2.0.52 -> 2.1.3
    cryptography 50.0.1 -> 50.0.2
    paramiko НЕ УСТАНОВЛЕН -> 5.0.0
    pypdf 6.18.0 -> 6.19.0
    streamlit 1.62.0 -> 1.65.0
    (плюс зависимости: bcrypt 5.0.0, invoke 3.0.3)

Оговорка про SQLAlchemy 2.1.3: это переход на новую минорную версию
(2.0 → 2.1). В таких версиях бывают изменения поведения, поэтому после
обновления обязательно проверяется работоспособность приложения и тесты.
Если что-то сломается — откат по снимку.

Снимок версий уже сохранён перед первой попыткой.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
SNAPSHOT = pathlib.Path(__file__).resolve().parents[1].parent / "docs/SagaAI/Fixes" / "перед-обновлением-версии.json"

# Целевые версии: те, что pip предложил в режиме проверки.
TARGETS = [
    "streamlit==1.65.0",
    "sqlalchemy==2.1.3",
    "cryptography==50.0.2",
    "pypdf==6.19.0",
    "Markdown==3.11",
    "paramiko==5.0.0",
]


def pip(args: list[str], timeout: int = 2400) -> tuple[int, str, str]:
    """pip со снятой переменной PIP_USER.

    При `PIP_USER=1` pip ставит пакеты в пользовательский каталог, а не
    в `.venv` проекта (проверено: 0 против 68 видимых пакетов).
    """
    env = dict(os.environ)
    env.pop("PIP_USER", None)
    proc = subprocess.run([str(PY), "-m", "pip"] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env,
                          cwd=str(SAGA))
    return proc.returncode, proc.stdout or "", proc.stderr or ""


print("=" * 78)
print("СНИМОК ДО ОБНОВЛЕНИЯ")
print("=" * 78)
code, out, _ = pip(["list", "--format=json"])
before = {}
try:
    for item in json.loads(out or "[]"):
        before[str(item.get("name", ""))] = str(item.get("version", ""))
except json.JSONDecodeError:
    pass
print(f"  пакетов: {len(before)}")
if not SNAPSHOT.exists():
    SNAPSHOT.write_text(
        json.dumps(before, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"  снимок сохранён: {SNAPSHOT.name}")
else:
    print(f"  снимок уже есть: {SNAPSHOT.name}")

print()
print("=" * 78)
print("ОБНОВЛЕНИЕ ДО ЦЕЛЕВЫХ ВЕРСИЙ")
print("=" * 78)
print(f"  целевые версии: {', '.join(TARGETS)}")
print()
code, out, err = pip(["install", "--upgrade"] + TARGETS)
text = (out or "") + (err or "")
print(f"  код возврата: {code}")
for line in text.splitlines():
    s = line.strip()
    if s.startswith(("Successfully installed", "ERROR", "error:")):
        print(f"    {s[:220]}")
    elif "WARNING" in s and ("deprecat" in s.lower() or "conflict" in s.lower()):
        print(f"    {s[:180]}")

print()
print("=" * 78)
print("РЕЗУЛЬТАТ: ИЗМЕНЕНИЯ ВЕРСИЙ")
print("=" * 78)
code, out, _ = pip(["list", "--format=json"])
after = {}
try:
    for item in json.loads(out or "[]"):
        after[str(item.get("name", ""))] = str(item.get("version", ""))
except json.JSONDecodeError:
    pass

changed = [(k, before.get(k, "—"), after.get(k, "—"))
           for k in sorted(set(before) | set(after))
           if before.get(k) != after.get(k)]

print(f"  пакетов было: {len(before)}, стало: {len(after)}")
print(f"\n  изменилось ({len(changed)}):")
for name, old_v, new_v in changed:
    marker = " (ДОБАВЛЕН)" if old_v == "—" else ""
    print(f"    {name:<24} {old_v:<14} -> {new_v}{marker}")

SNAPSHOT_AFTER = SNAPSHOT.with_name("после-обновления-версии.json")
SNAPSHOT_AFTER.write_text(
    json.dumps(after, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8", newline="\n",
)
print(f"\n  снимок после: {SNAPSHOT_AFTER.name}")
