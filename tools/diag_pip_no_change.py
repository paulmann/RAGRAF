"""Диагностика: почему pip не меняет версии, хотя целевые заданы явно.

ЗАМЕР: `pip install --upgrade streamlit==1.65.0 ...` вернул код 0,
но НИ ОДНА версия не изменилась. `pip list` до и после совпадают.

Возможные причины:
  1. pip ставит пакеты в другой каталог и не видит уже установленные;
  2. вывод команды не показал главную строку (например была ошибка);
  3. `pip list` читает не то окружение, куда идёт установка.

Проверяется по шагам, с полным выводом команды установки, а не только
итоговой строкой.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
SP = SAGA / ".venv" / "Lib" / "site-packages"


def run(args: list[str], pip_user: str | None = None,
        timeout: int = 1800) -> tuple[int, str]:
    env = dict(os.environ)
    if pip_user is None:
        env.pop("PIP_USER", None)
    else:
        env["PIP_USER"] = pip_user
    proc = subprocess.run([str(PY)] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          env=env, cwd=str(SAGA))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


print("=" * 78)
print("1. ПОЛНЫЙ ВЫВОД КОМАНДЫ УСТАНОВКИ")
print("=" * 78)
code, out = run(["-m", "pip", "install", "streamlit==1.65.0"])
print(f"  код возврата: {code}")
for line in out.splitlines()[:25]:
    print(f"    {line[:150]}")

print()
print("=" * 78)
print("2. ВЕРСИЯ STREAMLIT В site-packages")
print("=" * 78)
dist = sorted(p.name for p in SP.glob("streamlit-*.dist-info"))
print(f"  метаданные: {dist}")
pkg = SP / "streamlit" / "__init__.py"
if pkg.exists():
    text = pkg.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines()[:40]:
        if "__version__" in line:
            print(f"  в коде: {line.strip()}")
            break

print()
print("=" * 78)
print("3. ЧТО ГОВОРИТ ИНТЕРПРЕТАТОР")
print("=" * 78)
code, out = run(["-c", "import streamlit, sys; print(streamlit.__version__); "
                       "print(streamlit.__file__)"])
for line in out.splitlines():
    print(f"    {line}")

print()
print("=" * 78)
print("4. ГДЕ PIP ИЩЕТ ПАКЕТЫ")
print("=" * 78)
code, out = run(["-m", "pip", "list", "--format=freeze"])
lines = [ln for ln in out.splitlines() if "==" in ln]
print(f"  пакетов видит: {len(lines)}")
for ln in lines[:5]:
    print(f"    {ln}")

# Проверяется, куда pip установит пакет сейчас.
code, out = run(["-m", "pip", "install", "--dry-run", "--target", "nonexistent",
                 "streamlit==1.65.0"])
print(f"\n  проверка цели установки (код {code}):")
for line in out.splitlines()[:6]:
    print(f"    {line[:140]}")

print()
print("=" * 78)
print("5. ЕСТЬ ЛИ ДРУГОЙ PYTHON С ТЕМ ЖЕ ПАКЕТОМ")
print("=" * 78)
# Проверяется, не установлен ли streamlit в пользовательском каталоге.
userbase = os.environ.get("PYTHONUSERBASE", "")
print(f"  PYTHONUSERBASE: {userbase}")
if userbase:
    cand = pathlib.Path(userbase) / "Lib" / "site-packages"
    print(f"  каталог: {cand}")
    print(f"  существует: {cand.exists()}")
    if cand.exists():
        hits = sorted(p.name for p in cand.glob("streamlit*"))
        print(f"  streamlit там: {hits or 'нет'}")
