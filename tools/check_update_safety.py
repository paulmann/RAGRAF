"""Проверка перед обновлением: что реально отсутствует и что обновится.

Собраны факты:
  * pip предлагает обновить 8 пакетов (включая streamlit 1.62 → 1.65);
  * `paramiko` НЕ установлен, хотя указан в requirements.txt;
  * 18 каталогов в site-packages — надо понять, это проблема или
    вспомогательные каталоги пакетов.

Последнее надо проверить: каталог `dateutil` — это часть пакета
`python-dateutil`, а `PIL` — часть `pillow`. Если родительский пакет
имеет метаданные, то «отсутствие метаданных» у каталога — норма,
а не дефект. Пугать владельца этим не нужно.

Отдельно проверяется: не сломает ли обновление streamlit интерфейс.
Для этого ищутся вызовы API, которые менялись между версиями.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
SP = SAGA / ".venv" / "Lib" / "site-packages"

print("=" * 78)
print("1. КАТАЛОГИ БЕЗ МЕТАДАННЫХ: ПРОБЛЕМА ИЛИ НОРМА?")
print("=" * 78)
# Проверяется, какому пакету принадлежит каждый каталог.
ALIASES = {
    "dateutil": "python-dateutil",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "PIL": "pillow",
    "attr": "attrs",
    "jwt": "pyjwt",
    "nacl": "pynacl",
    "multipart": "python-multipart",
    "google": "protobuf или google-api",
    "github": "pygithub",
    "pdfminer": "pdfminer.six",
    "tiktoken_ext": "tiktoken",
    "_pytest": "pytest",
    "_distutils_hack": "setuptools",
    "rpds": "rpds-py",
    "pypdfium2_cfg": "pypdfium2",
    "pypdfium2_cli": "pypdfium2",
    "pypdfium2_raw": "pypdfium2",
}


def pip(args: list[str]) -> tuple[int, str, str]:
    env = dict(os.environ)
    env.pop("PIP_USER", None)
    proc = subprocess.run([str(PY), "-m", "pip"] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600, env=env, cwd=str(SAGA))
    return proc.returncode, proc.stdout or "", proc.stderr or ""


code, out, _ = pip(["list", "--format=freeze"])
installed = {}
for line in (out or "").splitlines():
    if "==" in line:
        name, version = line.split("==", 1)
        installed[name.lower().replace("_", "-")] = version
print(f"  установленных пакетов: {len(installed)}")

print(f"\n  {'каталог':<18} {'принадлежит':<22} состояние")
real_problems = []
for d in sorted(ALIASES):
    owner = ALIASES[d]
    key = owner.lower().replace("_", "-").split(' ')[0]
    ver = installed.get(key)
    if ver:
        state = f"норма (пакет {owner} {ver})"
    else:
        state = "ПАКЕТ НЕ НАЙДЕН"
        real_problems.append(d)
    print(f"  {d:<18} {owner:<22} {state}")

print(f"\n  каталогов с реальной проблемой: {len(real_problems)}")
if not real_problems:
    print("""
  ВЫВОД: это НЕ дефект. Каталоги `dateutil`, `PIL`, `docx` и прочие —
  подмодули установленных пакетов. Метаданные есть у родительского
  пакета, этого достаточно. pip их видит корректно.
""")

print()
print("=" * 78)
print("2. ОТСУТСТВУЮЩИЕ ПАКЕТЫ ИЗ requirements.txt")
print("=" * 78)
REQUIRED = ["streamlit", "requests", "sqlalchemy", "cryptography", "pypdf",
            "python-docx", "python-pptx", "openpyxl", "pdfplumber",
            "tiktoken", "markdown", "reportlab", "paramiko", "pytest"]
missing = [n for n in REQUIRED
           if n.lower().replace("_", "-") not in installed]
print(f"  не установлено: {missing or 'нет'}")

# Проверяется, что даёт отсутствие paramiko: SSH-коннектор.
if "paramiko" in missing:
    print(f"\n  СЛЕДСТВИЕ: `paramiko` не установлен, хотя указан в требованиях.")
    probe = SAGA / "_probe_paramiko.py"
    probe.write_text(
        "try:\n"
        "    import paramiko\n"
        "    print('paramiko: OK', paramiko.__version__)\n"
        "except ImportError as exc:\n"
        "    print('paramiko: НЕДОСТУПЕН -', exc)\n"
        "try:\n"
        "    from core import ssh_connector\n"
        "    print('ssh_connector: импортируется')\n"
        "except ImportError as exc:\n"
        "    print('ssh_connector: импорт не удался -', "
        "type(exc).__name__, exc)\n",
        encoding="utf-8", newline="\n",
    )
    proc = subprocess.run([str(PY), str(probe)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          cwd=str(SAGA), timeout=300)
    for line in (proc.stdout or "").splitlines():
        print(f"    {line}")
    if proc.stderr.strip():
        print(f"    stderr: {proc.stderr.strip()[:200]}")
    probe.unlink(missing_ok=True)

print()
print("=" * 78)
print("3. API STREAMLIT В КОДЕ: РИСК ОБНОВЛЕНИЯ")
print("=" * 78)
# Ищутся вызовы, которые менялись между 1.62 и 1.65.
RISKY = {
    "st.experimental_": "УСТАРЕЛО, удалено в новых версиях",
    "st.cache(": "устарело, заменено на st.cache_data/cache_resource",
    "st.cache_data": "актуально",
    "st.cache_resource": "актуально",
    "st.rerun": "актуально",
    "st.experimental_rerun": "УСТАРЕЛО",
    "st.chat_input": "актуально",
    "st.chat_message": "актуально",
    "st.container": "актуально",
    "st.columns": "актуально",
    "st.dialog": "актуально (1.34+)",
    "st.fragment": "актуально (1.37+)",
    "use_container_width": "принимается, но есть width= в 1.4x+",
    "st.experimental_radio": "УСТАРЕЛО",
    "st.experimental_get_query_params": "УСТАРЕЛО (удалено)",
    "st.experimental_set_query_params": "УСТАРЕЛО (удалено)",
}
counts: dict[str, int] = {}
files_with_risky: dict[str, set[str]] = {}
for p in SAGA.rglob("*.py"):
    if {".venv", ".git", ".dev_agent", "__pycache__"} & set(p.parts):
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    rel = str(p.relative_to(SAGA)).replace("\\", "/")
    for needle in RISKY:
        if needle in text:
            counts[needle] = counts.get(needle, 0) + text.count(needle)
            files_with_risky.setdefault(needle, set()).add(rel)

print(f"  {'вызов':<36} {'вхождений':>9} оценка")
for needle, verdict in RISKY.items():
    n = counts.get(needle, 0)
    if n:
        print(f"  {needle:<36} {n:>9} {verdict}")

deprecated = [n for n in counts if RISKY[n].startswith("УСТАРЕЛО")]
print(f"\n  устаревших вызовов найдено: {len(deprecated)}")
for needle in deprecated:
    print(f"\n    {needle} — {counts[needle]} вхождений:")
    for rel in sorted(files_with_risky[needle])[:6]:
        print(f"      {rel}")
