"""Что именно обновится: текущие версии против доступных.

Прежде чем менять окружение проекта, надо знать объём: какие пакеты
устарели и до каких версий их предлагает обновить pip.

Дополнительно проверяется, есть ли в окружении пакеты БЕЗ метаданных:
именно они могут стать источником конфликта при обновлении, потому что
pip их не видит и поставит вторую копию.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
SP = SAGA / ".venv" / "Lib" / "site-packages"


def pip(args: list[str]) -> tuple[int, str, str]:
    """Запуск pip со снятым PIP_USER (иначе он смотрит не в тот каталог)."""
    env = dict(os.environ)
    env.pop("PIP_USER", None)
    proc = subprocess.run([str(PY), "-m", "pip"] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900, env=env, cwd=str(SAGA))
    return proc.returncode, proc.stdout or "", proc.stderr or ""


print("=" * 78)
print("1. ПАКЕТЫ БЕЗ МЕТАДАННЫХ (невидимы для pip)")
print("=" * 78)
entries = list(SP.iterdir())
dist_versions = {}
for e in entries:
    if e.name.endswith(".dist-info"):
        # Имя вида "streamlit-1.62.0.dist-info".
        m = re.match(r"(.+)-([\d][\w.]*)\.dist-info$", e.name)
        if m:
            dist_versions[m.group(1).lower().replace("_", "-")] = m.group(2)

package_dirs = {
    e.name.lower().replace("_", "-"): e.name
    for e in entries
    if e.is_dir() and not e.name.endswith((".dist-info", ".egg-info", ".data",
                                           ".libs", "__pycache__"))
}
print(f"  каталогов пакетов:     {len(package_dirs)}")
print(f"  метаданных .dist-info: {len(dist_versions)}")

# Каталоги без метаданных.
no_meta = sorted(set(package_dirs) - set(dist_versions))
print(f"\n  каталогов БЕЗ метаданных: {len(no_meta)}")
for name in no_meta[:25]:
    print(f"    {package_dirs[name]}")

print()
print("=" * 78)
print("2. ТЕКУЩИЕ ВЕРСИИ ПАКЕТОВ ИЗ requirements.txt")
print("=" * 78)
REQUIRED = [
    "streamlit", "requests", "sqlalchemy", "cryptography", "pypdf",
    "python-docx", "python-pptx", "openpyxl", "pdfplumber", "tiktoken",
    "markdown", "reportlab", "paramiko", "pytest",
]
# Имена в метаданных отличаются от имён для импорта.
ALIASES = {"python-docx": "docx", "python-pptx": "pptx",
           "reportlab": "reportlab", "markdown": "markdown"}

code, out, err = pip(["list", "--format=json"])
import json  # noqa: E402

installed = {}
try:
    for item in json.loads(out or "[]"):
        installed[str(item.get("name", "")).lower().replace("_", "-")] = \
            str(item.get("version", ""))
except json.JSONDecodeError:
    print(f"  список не разобран: {(out or '')[:200]}")

print(f"  пакетов видно через pip: {len(installed)}")
print(f"\n  {'пакет':<20} {'установлено':<14} есть в требованиях")
for name in REQUIRED:
    key = name.lower().replace("_", "-")
    version = installed.get(key, "—")
    source = "да" if version != "—" else "НЕТ"
    print(f"  {name:<20} {version:<14} {source}")

print()
print("=" * 78)
print("3. ЧТО ПРЕДЛОЖИТ ОБНОВИТЬ PIP (без установки)")
print("=" * 78)
code, out, err = pip([
    "install", "--dry-run", "--upgrade",
    *[f"{n}" for n in REQUIRED],
])
text = (out or "") + (err or "")
would = [ln.strip() for ln in text.splitlines()
         if "Would install" in ln or "would install" in ln]
for line in would:
    print(f"  {line[:200]}")
if not would:
    print("  (сообщений об установке нет)")
    for line in text.splitlines()[:12]:
        print(f"  {line.strip()[:120]}")

print()
print("=" * 78)
print("4. ДОСТУПНЫЕ ВЕРСИИ КЛЮЧЕВЫХ ПАКЕТОВ")
print("=" * 78)
for name in ("streamlit", "sqlalchemy", "requests", "cryptography",
             "tiktoken"):
    code, out, err = pip(["index", "versions", name])
    text = (out or "") + (err or "")
    latest = ""
    current = ""
    for line in text.splitlines():
        if line.startswith("LATEST:"):
            latest = line.split(":", 1)[1].strip()
        elif line.startswith("INSTALLED:"):
            current = line.split(":", 1)[1].strip()
    print(f"  {name:<16} установлено {current:<12} доступно {latest}")

print()
print("=" * 78)
print("5. ЧТО МЕШАЕТ ОБНОВЛЕНИЮ")
print("=" * 78)
print(f"""
  PIP_USER=1 в окружении: pip ставит пакеты в пользовательский каталог
  вместо окружения проекта. Перед обновлением переменную надо снять.

  Пакетов без метаданных: {len(no_meta)}. Они невидимы для pip, поэтому
  при установке новой версии pip не удалит старую копию, и в окружении
  окажутся две версии одного пакета.
""")
