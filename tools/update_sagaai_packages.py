"""Обновление пакетов SagaAI. Снимок «до» и безопасная установка.

КРИТИЧНО ПЕРЕД РАБОТОЙ. В окружении задано `PIP_USER=1`. При этой
переменной pip ставит пакеты в ПОЛЬЗОВАТЕЛЬСКИЙ каталог
(`PYTHONUSERBASE=...AutoClaw-oversea-official\\toolchain\\python`), а не
в `.venv` проекта. Проверено замером: `pip list` при `PIP_USER=1` видит
0 пакетов, без него — 68.

Значит без снятия переменной команда `pip install --upgrade streamlit`
НЕ обновит проект, зато изменит общее окружение AutoClaw.

ПОРЯДОК РАБОТЫ:
  1. снимается снимок текущих версий (без него откат — по памяти);
  2. выполняется обновление со снятым `PIP_USER`;
  3. проверяется результат: версии, импорт, работоспособность приложения;
  4. при неудаче — откат по снимку.

Обновляется то, что предлагает pip: streamlit 1.62 → 1.65 и зависимости.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
SNAPSHOT = pathlib.Path(__file__).resolve().parents[1].parent / "docs/SagaAI/Fixes" / "перед-обновлением-версии.json"
SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)


def pip(args: list[str], timeout: int = 1800) -> tuple[int, str, str]:
    """pip со снятой переменной PIP_USER.

    Без снятия pip работает с пользовательским каталогом и не видит
    окружение проекта (проверено замером: 0 против 68 пакетов).
    """
    env = dict(os.environ)
    env.pop("PIP_USER", None)
    proc = subprocess.run([str(PY), "-m", "pip"] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env,
                          cwd=str(SAGA))
    return proc.returncode, proc.stdout or "", proc.stderr or ""


print("=" * 78)
print("1. СНИМОК ВЕРСИЙ ДО ОБНОВЛЕНИЯ")
print("=" * 78)
code, out, err = pip(["list", "--format=json"])
before: dict[str, str] = {}
try:
    for item in json.loads(out or "[]"):
        before[str(item.get("name", ""))] = str(item.get("version", ""))
except json.JSONDecodeError:
    print(f"  список не разобран: {(out or '')[:200]}")

print(f"  пакетов: {len(before)}")
SNAPSHOT.write_text(
    json.dumps(before, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8", newline="\n",
)
print(f"  снимок сохранён: {SNAPSHOT}")
print(f"\n  ключевые версии до обновления:")
for name in ("streamlit", "altair", "pandas", "numpy", "pyarrow",
             "sqlalchemy", "requests", "cryptography"):
    key = next((k for k in before if k.lower() == name), None)
    if key:
        print(f"    {key:<16} {before[key]}")

print()
print("=" * 78)
print("2. ЧТО БУДЕТ ОБНОВЛЕНО (проверка без установки)")
print("=" * 78)
code, out, err = pip(["install", "--dry-run", "--upgrade",
                      "streamlit", "sqlalchemy", "cryptography", "pypdf",
                      "markdown", "paramiko"])
text = (out or "") + (err or "")
for line in text.splitlines():
    if "Would install" in line or "would install" in line:
        print(f"  {line.strip()[:220]}")
if code != 0 and not text.strip():
    print(f"  код {code}, вывода нет")

print()
print("=" * 78)
print("3. ОБНОВЛЕНИЕ")
print("=" * 78)
targets = ["streamlit", "sqlalchemy", "cryptography", "pypdf", "markdown",
           "paramiko"]
print(f"  обновляются: {', '.join(targets)}")
code, out, err = pip(["install", "--upgrade"] + targets)
print(f"  код возврата: {code}")
# Печатается итог: что установлено.
text = (out or "") + (err or "")
for line in text.splitlines():
    s = line.strip()
    if s.startswith(("Successfully installed", "Requirement already",
                     "ERROR", "error:")):
        print(f"    {s[:200]}")

print()
print("=" * 78)
print("4. СНИМОК ВЕРСИЙ ПОСЛЕ ОБНОВЛЕНИЯ")
print("=" * 78)
code, out, err = pip(["list", "--format=json"])
after: dict[str, str] = {}
try:
    for item in json.loads(out or "[]"):
        after[str(item.get("name", ""))] = str(item.get("version", ""))
except json.JSONDecodeError:
    print(f"  список не разобран: {(out or '')[:200]}")

changed = [(k, before[k], after.get(k, "—"))
           for k in sorted(before) if before[k] != after.get(k)]
added = [k for k in sorted(after) if k not in before]

print(f"  пакетов было: {len(before)}, стало: {len(after)}")
print(f"\n  изменились версии ({len(changed)}):")
for name, old_v, new_v in changed:
    print(f"    {name:<24} {old_v:<14} -> {new_v}")
print(f"\n  добавлены ({len(added)}):")
for name in added:
    print(f"    {name:<24} {after[name]}")

# Снимок «после» тоже сохраняется: по нему видно результат.
SNAPSHOT_AFTER = SNAPSHOT.with_name("после-обновления-версии.json")
SNAPSHOT_AFTER.write_text(
    json.dumps(after, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8", newline="\n",
)
print(f"\n  снимок после обновления: {SNAPSHOT_AFTER}")
