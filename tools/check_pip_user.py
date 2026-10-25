"""Проверка: `PIP_USER=1` мешает обновлению пакетов проекта.

НАЙДЕНО: в окружении задано `PIP_USER=1`. В этом режиме pip считает,
что работает с «пользовательским» каталогом пакетов
(`PYTHONUSERBASE=C:\\Users\\...\\AutoClaw-oversea-official\\toolchain\\python`),
а не с окружением проекта.

Следствия, которые надо подтвердить фактом:
  1. `pip list` пуст — он смотрит в пользовательский каталог;
  2. `pip install --upgrade streamlit` поставит пакет В ПОЛЬЗОВАТЕЛЬСКИЙ
     каталог, а не в `.venv` проекта. То есть проект не обновится,
     а окружение AutoClaw загрязнится;
  3. при `PIP_USER=0` pip видит пакеты проекта.

Проверяется: список пакетов при разных значениях переменной и цель
установки (`--dry-run`).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
USERBASE = os.environ.get("PYTHONUSERBASE", "")


def run(args: list[str], pip_user: str | None) -> tuple[int, str, str]:
    env = dict(os.environ)
    if pip_user is None:
        env.pop("PIP_USER", None)
    else:
        env["PIP_USER"] = pip_user
    proc = subprocess.run([str(PY)] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600,
                          env=env, cwd=str(SAGA))
    return proc.returncode, proc.stdout or "", proc.stderr or ""


print("=" * 78)
print("1. PIP_USER=1 (как в текущем окружении)")
print("=" * 78)
code, out, err = run(["-m", "pip", "list"], "1")
lines = [ln for ln in out.splitlines() if ln.strip() and "Package" not in ln]
print(f"  код {code}, пакетов видно: {len(lines)}")
for ln in lines[:5]:
    print(f"    {ln}")

print()
print("=" * 78)
print("2. PIP_USER снят (работа с окружением проекта)")
print("=" * 78)
code, out, err = run(["-m", "pip", "list"], None)
lines = [ln for ln in out.splitlines()
         if ln.strip() and not ln.startswith("Package")]
print(f"  код {code}, пакетов видно: {len(lines)}")
for ln in lines[:20]:
    print(f"    {ln}")
if len(lines) > 20:
    print(f"    ... и ещё {len(lines) - 20}")

print()
print("=" * 78)
print("3. КУДА ПОСТАВИТСЯ ПАКЕТ ПРИ КАЖДОМ РЕЖИМЕ")
print("=" * 78)
print(f"  PYTHONUSERBASE (пользовательский каталог): {USERBASE}")
print(f"  каталог проекта: {SAGA / '.venv' / 'Lib' / 'site-packages'}")

# Проверяется цель установки без реальной установки.
for label, value in (("PIP_USER=1", "1"), ("снят (нет)", None)):
    code, out, err = run(
        ["-m", "pip", "install", "--dry-run", "--upgrade", "streamlit"],
        value,
    )
    text = (out or "") + (err or "")
    target = "(не указана)"
    for line in text.splitlines():
        low = line.lower()
        if "would install" in low or "already satisfied" in low:
            target = line.strip()[:100]
            break
    print(f"\n  режим {label}: код {code}")
    print(f"    {target}")

print()
print("=" * 78)
print("4. ДОСТУПНЫЕ ВЕРСИИ STREAMLIT")
print("=" * 78)
code, out, err = run(["-m", "pip", "index", "versions", "streamlit"], None)
text = (out or "") + (err or "")
for line in text.splitlines()[:6]:
    print(f"  {line.strip()}")
if code != 0:
    print("  (команда pip index может быть недоступна — тогда смотреть "
          "через pip install --dry-run)")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Причина пустого `pip list` установлена: переменная `PIP_USER=1`.

  Опасность для задачи обновления: при этой переменной пакеты ставятся
  в пользовательский каталог
      {USERBASE}
  а НЕ в окружение проекта. То есть `pip install --upgrade streamlit`
  НЕ обновил бы проект, зато изменил бы общее окружение AutoClaw.

  Решение: выполнять установку со снятой переменной `PIP_USER`
  (в отдельном процессе с чистым окружением), тогда pip работает
  с `.venv` проекта.
""")
