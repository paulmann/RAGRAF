"""Проверка спорных файлов в каталогах, которые я исключаю.

Прошлая проверка показала: в publish есть 1 .html, 1 .css, 1 .js; в .dev_agent
есть 1 .html, 1 .py; в obj — сгенерированные .cs. Прежде чем исключать,
нужно понять, ЧТО это за файлы.

Если это копии исходников — исключать правильно (иначе индекс дублируется).
Если это единственный экземпляр — исключение выбросит рабочий файл.

Для каждого файла проверяется: есть ли файл с таким же именем вне
исключаемого каталога. Совпадение имени и размера = копия.
"""

from __future__ import annotations

import hashlib
import pathlib

ROOT = pathlib.Path(r"C:\1st_RdpMON\Service")
CODE_EXTS = {".cs", ".php", ".js", ".ts", ".py", ".ps1", ".sh", ".pl", ".c",
             ".cpp", ".h", ".java", ".go", ".rb", ".lua", ".sql", ".css",
             ".html", ".htm"}

# Каталоги, которые предполагается исключить.
EXCLUDED = ["publish", ".dev_agent", ".staging", "obj", "bin", "packages"]


def sha(path: pathlib.Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "?"


print("=" * 78)
print("РАЗБОР СПОРНЫХ ФАЙЛОВ")
print("=" * 78)

# Сначала собираются все файлы кода ВНЕ исключаемых каталогов: по их хешам
# проверяется, является ли файл из исключаемого каталога копией.
outside: dict[str, list[pathlib.Path]] = {}
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in CODE_EXTS:
        continue
    parts = path.relative_to(ROOT).parts
    if any(p in EXCLUDED for p in parts):
        continue
    outside.setdefault(sha(path), []).append(path)

print(f"\n  файлов кода вне исключаемых каталогов: "
      f"{sum(len(v) for v in outside.values())}")

for name in EXCLUDED:
    # Каталоги с этим именем на любом уровне.
    dirs = [p for p in ROOT.rglob(name) if p.is_dir()]
    if not dirs:
        continue
    code_files: list[pathlib.Path] = []
    for d in dirs:
        try:
            code_files.extend(f for f in d.rglob("*")
                              if f.is_file() and f.suffix.lower() in CODE_EXTS)
        except OSError:
            continue
    if not code_files:
        print(f"\n  {name}: файлов кода нет — исключение безопасно")
        continue

    print(f"\n  {name}: {len(code_files)} файлов кода в {len(dirs)} каталогах")
    copies = 0
    unique = 0
    for f in sorted(code_files)[:14]:
        h = sha(f)
        same = outside.get(h, [])
        rel = str(f.relative_to(ROOT))
        if same:
            copies += 1
            print(f"    КОПИЯ  {rel}")
            print(f"           = {same[0].relative_to(ROOT)}")
        else:
            unique += 1
            print(f"    УНИКАЛ {rel} ({f.stat().st_size} б)")
    if len(code_files) > 14:
        rest = sorted(code_files)[14:]
        for f in rest:
            if outside.get(sha(f)):
                copies += 1
            else:
                unique += 1
    print(f"    ИТОГО: копий {copies}, уникальных {unique}")
    if unique:
        print(f"    ВЫВОД: есть уникальные файлы — исключать нельзя, "
              f"не разобравшись")
    else:
        print("    ВЫВОД: все файлы — копии, исключение безопасно")

print()
print("=" * 78)
print("ЧТО ИМЕННО В publish И .dev_agent (полные списки)")
print("=" * 78)
for name in ("publish", ".dev_agent", ".staging"):
    d = ROOT / name
    if not d.exists():
        continue
    files = [f for f in d.rglob("*") if f.is_file()
             and f.suffix.lower() in CODE_EXTS]
    print(f"\n  {name}:")
    for f in sorted(files):
        print(f"    {f.relative_to(ROOT)} ({f.stat().st_size} б)")
