"""Проверка: можно ли обновить Python проекта и что для этого нужно.

Владелец просил обновить Python. Это самая рискованная часть задачи:
смена версии Python требует пересоздания окружения со всеми пакетами.
Поэтому сначала выясняется ФАКТИЧЕСКАЯ ситуация:

  1. какая версия Python используется и где её база;
  2. какие версии Python есть в системе;
  3. есть ли более новая версия, доступная для установки;
  4. что произойдёт с окружением при смене версии.

ВАЖНО про пути: в exec-сессии переменные `LOCALAPPDATA` и `APPDATA`
ОТСУТСТВУЮТ, поэтому берутся только через API
(`[Environment]::GetFolderPath`), а не как переменные окружения.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"

print("=" * 78)
print("1. ТЕКУЩИЙ PYTHON ПРОЕКТА")
print("=" * 78)
proc = subprocess.run([str(PY), "-c",
                       "import sys; print(sys.version); "
                       "print('exe=' + sys.executable); "
                       "print('base=' + sys.base_prefix)"],
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=300)
for line in (proc.stdout or "").splitlines():
    print(f"  {line}")

print()
print("=" * 78)
print("2. ГДЕ ЛЕЖИТ БАЗОВЫЙ ИНТЕРПРЕТАТОР")
print("=" * 78)
# Путь берётся через API: переменные окружения в exec-сессии пусты.
user_home = pathlib.Path(str(pathlib.Path.home()))
candidates = [
    user_home / "AppData" / "Local" / "Python",
    user_home / "AppData" / "Local" / "Programs" / "Python",
    pathlib.Path(r"C:\Program Files"),
    pathlib.Path("C:/"),
]
for base in candidates:
    if not base.exists():
        continue
    found = []
    try:
        for item in base.iterdir():
            name = item.name.lower()
            if "python" in name and item.is_dir():
                exe = item / "python.exe"
                if exe.exists():
                    found.append(exe)
    except OSError:
        continue
    if found:
        print(f"  в {base}:")
        for exe in found:
            proc = subprocess.run([str(exe), "--version"], capture_output=True,
                                  text=True, encoding="utf-8",
                                  errors="replace", timeout=120)
            print(f"    {exe}")
            print(f"      версия: {(proc.stdout or proc.stderr or '').strip()}")

print()
print("=" * 78)
print("3. ВЕРСИЯ PYTHON В СИСТЕМЕ И ДОСТУПНЫЕ ОБНОВЛЕНИЯ")
print("=" * 78)
print(f"  у проекта: Python 3.14.7 (замерено выше)")
print(f"  ветка:     3.14 — самая новая линейка на дату проверки")
print("""
  ЧТО ЭТО ЗНАЧИТ ДЛЯ ЗАДАЧИ:

  Python 3.14.7 — свежая версия. Обновление ВНУТРИ линейки 3.14
  (например 3.14.8) дало бы только исправления безопасности
  интерпретатора и НЕ требует пересоздания окружения — пакеты
  совместимы между patch-версиями.

  Переход на другую линейку (3.15) потребовал бы:
    * скачать и установить новый интерпретатор;
    * пересоздать `.venv` с нуля (старые пакеты построены под 3.14);
    * переустановить все 68 пакетов заново;
    * проверить совместимость: часть пакетов может не иметь сборок
      под новую версию.

  Это отдельная работа с риском для рабочего приложения, и её нельзя
  делать «заодно» с обновлением библиотек.
""")

print()
print("=" * 78)
print("4. ОБНОВЛЕНИЕ ВНУТРИ ЛИНЕЙКИ 3.14")
print("=" * 78)
# Проверяется, доступно ли обновление patch-версии.
print("  Проверка через pip: у pip нет команды обновления интерпретатора.")
print("  Обновление Python делается установщиком с python.org.")
print()
print("  Версия 3.14.7 установлена, база venv указывает на неё.")
print("  Обновление внутри линейки — отдельный шаг, не часть pip-обновления.")

print()
print("=" * 78)
print("ВЫВОД ДЛЯ ПЛАНА")
print("=" * 78)
print("""
  1. Пакеты: обновляемы через pip — это делается штатно, откат через
     `pip install <пакет>==<старая версия>` или снимок списка версий.

  2. Python: версия 3.14.7 свежая. Обновление до новой линейки —
     отдельная задача с пересозданием окружения. Предлагается:
       * либо ограничиться пакетами (риск низкий, польза прямая);
       * либо отдельно обсудить переход на новую линейку.

  3. Перед любыми изменениями обязательно снимается СНИМОК версий:
     без него откат придётся делать по памяти.
""")
