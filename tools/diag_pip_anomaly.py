"""Разведка аномалии: pip не видит установленные пакеты.

ФАКТ: в `.venv\\Lib\\site-packages` — 146 каталогов, `streamlit` и `sqlalchemy`
там ЕСТЬ, но `pip list` возвращает пустой список.

Это опасная ситуация для обновления. Если pip не видит уже установленные
пакеты, команда `pip install --upgrade streamlit`:
  * может поставить вторую копию поверх первой — конфликт версий;
  * не сможет корректно разрешить зависимости;
  * не удалит старую версию, оставив мусор.

Проверяется, ПОЧЕМУ pip не видит пакеты:
  1. есть ли метаданные (`*.dist-info`) рядом с пакетами;
  2. что отвечает `pip list` в разных форматах;
  3. читается ли версия пакета через importlib.metadata;
  4. работает ли сам streamlit.
"""

from __future__ import annotations

import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
SP = SAGA / ".venv" / "Lib" / "site-packages"

print("=" * 78)
print("1. МЕТАДАННЫЕ ПАКЕТОВ В site-packages")
print("=" * 78)
if not SP.exists():
    print(f"  каталога нет: {SP}")
    raise SystemExit(1)

entries = list(SP.iterdir())
print(f"  всего записей: {len(entries)}")
dist_info = [e for e in entries if e.name.endswith(".dist-info")]
egg_info = [e for e in entries if e.name.endswith(".egg-info")]
dirs = [e for e in entries if e.is_dir() and not e.name.endswith(
    (".dist-info", ".egg-info", ".data", ".libs"))]
print(f"    каталогов пакетов:        {len(dirs)}")
print(f"    метаданных .dist-info:    {len(dist_info)}")
print(f"    метаданных .egg-info:     {len(egg_info)}")

if dist_info:
    print(f"\n  примеры .dist-info:")
    for e in sorted(dist_info)[:12]:
        print(f"    {e.name}")
else:
    print("""
  МЕТАДАННЫХ НЕТ. Значит пакеты в этом окружении положены КОПИРОВАНИЕМ
  каталогов, а не установкой через pip. Это объясняет, почему `pip list`
  пуст: ему нечего читать — файлов METADATA нет.

  Следствие: pip считает окружение пустым. Команда `pip install streamlit`
  поставит ВТОРУЮ копию поверх существующих каталогов, и что заработает —
  зависит от порядка путей. Это надо решать до обновления.
""")

print()
print("=" * 78)
print("2. ЧТО ОТВЕЧАЕТ PIP В РАЗНЫХ ФОРМАТАХ")
print("=" * 78)
for args in (["list"], ["list", "--format=freeze"], ["freeze"]):
    proc = subprocess.run([str(PY), "-m", "pip"] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    print(f"  pip {' '.join(args):<26} код {proc.returncode}, "
          f"строк {len(lines)}")
    for ln in lines[:8]:
        print(f"      {ln}")
    if proc.stderr.strip():
        print(f"      stderr: {proc.stderr.strip()[:160]}")

print()
print("=" * 78)
print("3. ВЕРСИИ ЧЕРЕЗ importlib.metadata")
print("=" * 78)
probe = SAGA / "_probe_meta.py"
probe.write_text(
    "import importlib.metadata as md\n"
    "NAMES = ['streamlit', 'sqlalchemy', 'requests', 'cryptography',\n"
    "         'pypdf', 'python-docx', 'tiktoken', 'pytest', 'pyarrow',\n"
    "         'pandas', 'altair', 'numpy', 'pillow', 'paramiko']\n"
    "for name in NAMES:\n"
    "    try:\n"
    "        print(f'{name}|{md.version(name)}')\n"
    "    except Exception as exc:\n"
    "        print(f'{name}|нет ({type(exc).__name__})')\n"
    "print(f'МЕТАДАННЫХ ВСЕГО|{len(md.distributions())}')\n",
    encoding="utf-8", newline="\n",
)
proc = subprocess.run([str(PY), str(probe)], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=300)
print(f"  код возврата: {proc.returncode}")
for line in (proc.stdout or "").splitlines():
    if "|" in line:
        name, version = line.split("|", 1)
        print(f"    {name:<22} {version}")
if proc.stderr.strip():
    print(f"  stderr: {proc.stderr.strip()[:300]}")
probe.unlink(missing_ok=True)

print()
print("=" * 78)
print("4. РАБОТАЕТ ЛИ STREAMLIT ФАКТИЧЕСКИ")
print("=" * 78)
probe2 = SAGA / "_probe_import.py"
probe2.write_text(
    "mods = ['streamlit', 'sqlalchemy', 'requests', 'cryptography',\n"
    "        'pypdf', 'docx', 'tiktoken', 'pytest', 'paramiko']\n"
    "for m in mods:\n"
    "    try:\n"
    "        mod = __import__(m)\n"
    "        v = getattr(mod, '__version__', '?')\n"
    "        print(f'{m}|ok|{v}')\n"
    "    except Exception as exc:\n"
    "        print(f'{m}|ОШИБКА|{type(exc).__name__}: {exc}')\n",
    encoding="utf-8", newline="\n",
)
proc = subprocess.run([str(PY), str(probe2)], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=300)
print(f"  код возврата: {proc.returncode}")
for line in (proc.stdout or "").splitlines():
    parts = line.split("|")
    if len(parts) >= 3:
        mark = "OK " if parts[1] == "ok" else "ПРОВАЛ"
        print(f"    [{mark}] {parts[0]:<14} {parts[2][:60]}")
probe2.unlink(missing_ok=True)
