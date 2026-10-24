"""Проверка: что именно лежит в каталогах, которые я собираюсь исключать.

Исключение каталога — необратимое решение: если внутри окажется рабочий код,
он исчезнет из индекса молча. Поэтому перед исключением проверяется содержимое,
а не угадывается по имени.

Проверяются: publish, .dev_agent, .staging, obj, bin, packages в обоих корнях.
"""

from __future__ import annotations

import pathlib
from collections import Counter

ROOTS = [pathlib.Path(__file__).resolve().parents[1].parent.parent / "1st_RdpMON" / "Service", pathlib.Path(__file__).resolve().parents[1].parent]

# Каталоги, которые я добавил в исключения: проверяются на обоих корнях.
SUSPECTS = ["publish", ".dev_agent", ".staging", "obj", "bin", "packages",
            "TestResults", ".nuget"]

# Расширения, которые считаются исходным кодом: их наличие — признак,
# что каталог исключать нельзя без разбирательства.
CODE_EXTS = {".cs", ".php", ".js", ".ts", ".py", ".ps1", ".sh", ".pl", ".c",
             ".cpp", ".h", ".java", ".go", ".rb", ".lua", ".sql", ".css",
             ".html", ".htm"}

print("=" * 78)
print("ПРОВЕРКА КАТАЛОГОВ ПЕРЕД ИСКЛЮЧЕНИЕМ")
print("=" * 78)

for root in ROOTS:
    print(f"\n{root}")
    print("-" * 78)
    # Ищутся каталоги с таким именем на любом уровне.
    for name in SUSPECTS:
        found = []
        for path in root.rglob(name):
            if path.is_dir():
                # Каталоги внутри других подозрительных не считаются.
                if any(p.name in SUSPECTS and p != path for p in path.parents):
                    continue
                found.append(path)
        if not found:
            continue
        for path in found[:4]:
            try:
                files = [f for f in path.rglob("*") if f.is_file()]
            except OSError:
                print(f"  {path.relative_to(root)}: чтение недоступно")
                continue
            exts = Counter(f.suffix.lower() for f in files)
            code = {e: n for e, n in exts.items() if e in CODE_EXTS}
            total = len(files)
            size_mb = sum(f.stat().st_size for f in files if f.exists()) / 1024 / 1024
            rel = str(path.relative_to(root))
            print(f"\n  {rel}")
            print(f"    файлов: {total}, объём: {size_mb:.1f} МБ")
            if code:
                top = sorted(code.items(), key=lambda kv: -kv[1])[:8]
                print(f"    ИСХОДНЫЙ КОД ЕСТЬ: {top}")
                print(f"    ВЫВОД: исключать {'нельзя без проверки' if total else ''}"
                      f" — содержит {sum(code.values())} файлов кода")
            else:
                top = sorted(exts.items(), key=lambda kv: -kv[1])[:5]
                print(f"    расширения: {top}")
                print("    ВЫВОД: исходного кода нет, исключение безопасно")
        if len(found) > 4:
            print(f"\n    … и ещё {len(found) - 4} каталогов с именем {name}")
