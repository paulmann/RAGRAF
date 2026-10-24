"""Проверка результата обновления: что реально изменилось.

ЗАГАДКА, которую надо разъяснить. Команда установки вернула код 0,
и мой скрипт сообщил «изменилось 0 пакетов». Но проверка по метаданным
и интерпретатору показывает:

    метаданные:    streamlit-1.65.0.dist-info
    интерпретатор: 1.65.0

То есть ОБНОВЛЕНИЕ ПРОИЗОШЛО, а мой счётчик изменений его не заметил.

ПРИЧИНА (предположение, которое проверяется). Снимок «до» был снят
не в тот момент: первый запуск обновления УЖЕ установил новые версии,
а снимок «перед-обновлением-версии.json» сохранён ПОСЛЕ этого. Тогда
сравнение «до/после» видит одинаковые значения.

Проверяется сравнением с исходным снимком и по метаданным пакетов —
это независимый источник, он не зависит от порядка вызовов.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
SP = SAGA / ".venv" / "Lib" / "site-packages"
DOCS = pathlib.Path(r"C:\AutoClaw\docs\SagaAI\Fixes")

TARGETS = {
    "streamlit": "1.65.0",
    "sqlalchemy": "2.1.3",
    "cryptography": "50.0.2",
    "pypdf": "6.19.0",
    "Markdown": "3.11",
    "paramiko": "5.0.0",
}

print("=" * 78)
print("1. ФАКТИЧЕСКИЕ ВЕРСИИ ПО МЕТАДАННЫМ ПАКЕТОВ")
print("=" * 78)
print("  метаданные в site-packages (независимый источник):")
found = {}
for pattern, name in (("streamlit-*.dist-info", "streamlit"),
                      ("SQLAlchemy-*.dist-info", "sqlalchemy"),
                      ("cryptography-*.dist-info", "cryptography"),
                      ("pypdf-*.dist-info", "pypdf"),
                      ("Markdown-*.dist-info", "Markdown"),
                      ("paramiko-*.dist-info", "paramiko"),
                      ("bcrypt-*.dist-info", "bcrypt"),
                      ("invoke-*.dist-info", "invoke")):
    hits = sorted(p.name for p in SP.glob(pattern))
    version = "—"
    if hits:
        # Из имени вида "streamlit-1.65.0.dist-info" берётся версия.
        import re

        m = re.search(r"-([\d][\w.]*)\.dist-info$", hits[0])
        version = m.group(1) if m else hits[0]
    found[name] = version
    mark = ""
    if name in TARGETS:
        mark = "  ЦЕЛЬ " + ("ДОСТИГНУТА" if version == TARGETS[name]
                            else f"НЕ ДОСТИГНУТА (нужно {TARGETS[name]})")
    print(f"    {name:<16} {version}{mark}")

print()
print("=" * 78)
print("2. ВЕРСИИ ЧЕРЕЗ ИНТЕРПРЕТАТОР (второй независимый источник)")
print("=" * 78)
probe = SAGA / "_probe_versions.py"
probe.write_text(
    "import importlib.metadata as md\n"
    "NAMES = ['streamlit', 'sqlalchemy', 'cryptography', 'pypdf',\n"
    "         'Markdown', 'paramiko', 'bcrypt', 'invoke']\n"
    "for name in NAMES:\n"
    "    try:\n"
    "        print(f'{name}|{md.version(name)}')\n"
    "    except Exception:\n"
    "        print(f'{name}|отсутствует')\n",
    encoding="utf-8", newline="\n",
)
env = dict(os.environ)
env.pop("PIP_USER", None)
proc = subprocess.run([str(PY), str(probe)], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", cwd=str(SAGA),
                      timeout=300, env=env)
for line in (proc.stdout or "").splitlines():
    if "|" in line:
        name, version = line.split("|", 1)
        mark = ""
        if name in TARGETS:
            mark = ("  OK" if version == TARGETS[name]
                    else f"  нужно {TARGETS[name]}")
        print(f"    {name:<16} {version}{mark}")
probe.unlink(missing_ok=True)

print()
print("=" * 78)
print("3. СРАВНЕНИЕ СНИМКОВ")
print("=" * 78)
before_file = DOCS / "перед-обновлением-версии.json"
after_file = DOCS / "после-обновления-версии.json"
for label, path in (("до", before_file), ("после", after_file)):
    if not path.exists():
        print(f"  снимок {label}: нет файла {path.name}")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"\n  снимок {label} ({len(data)} пакетов):")
    for name in ("streamlit", "SQLAlchemy", "cryptography", "pypdf",
                 "Markdown", "paramiko"):
        key = next((k for k in data if k.lower() == name.lower()), None)
        value = data[key] if key else "—"
        print(f"    {name:<16} {value}")

print()
print("=" * 78)
print("4. ИТОГ: ДОСТИГНУТЫ ЛИ ЦЕЛЕВЫЕ ВЕРСИИ")
print("=" * 78)
achieved = 0
for name, target in TARGETS.items():
    actual = found.get(name, "—")
    ok = actual == target
    if ok:
        achieved += 1
    mark = "достигнута" if ok else f"НЕ достигнута (есть {actual})"
    print(f"  {name:<16} цель {target:<12} {mark}")
print(f"\n  достигнуто: {achieved} из {len(TARGETS)}")
