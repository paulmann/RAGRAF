"""Проверка: есть ли источники для уникальных файлов из publish.

Прошлая проверка сравнивала файлы по содержимому (хешу). Три файла в
publish\\Web\\wwwroot\\assets оказались «уникальными» — то есть файла с таким
содержимым вне publish нет. Но это НЕ значит, что источник отсутствует:
сборка могла минифицировать файл, и хеш изменился.

Проверяется по ИМЕНИ файла: есть ли файл с таким же именем вне publish,
и если да — чем он отличается по размеру и содержимому.
"""

from __future__ import annotations

import hashlib
import pathlib

ROOT = pathlib.Path(r"C:\1st_RdpMON\Service")
TARGETS = [
    "rdpaudit-web.css",
    "rdpaudit-web.js",
    "index.html",
]


def info(path: pathlib.Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()[:16]


print("=" * 78)
print("ПОИСК ИСТОЧНИКОВ ДЛЯ ФАЙЛОВ ИЗ PUBLISH")
print("=" * 78)

for name in TARGETS:
    print(f"\n{name}")
    print("-" * 60)
    # Все файлы с этим именем в проекте, включая publish.
    hits = sorted(p for p in ROOT.rglob(name) if p.is_file())
    if not hits:
        print("  файлов с таким именем не найдено")
        continue
    for path in hits:
        size, digest = info(path)
        rel = path.relative_to(ROOT)
        mark = " <-- publish" if "publish" in path.parts else ""
        print(f"  {rel}{mark}")
        print(f"    размер: {size} б, хеш: {digest}")

    # Вывод: есть ли источник вне publish.
    outside = [p for p in hits if "publish" not in p.parts]
    inside = [p for p in hits if "publish" in p.parts]
    print()
    if outside and inside:
        o_size, o_hash = info(outside[0])
        i_size, i_hash = info(inside[0])
        if o_hash == i_hash:
            print("  ВЫВОД: в publish точная копия источника — "
                  "исключать publish безопасно")
        else:
            print(f"  ВЫВОД: содержимое отличается (источник {o_size} б "
                  f"против {i_size} б).")
            print("         Источник существует — он и попадёт в индекс. "
                  "Копию в publish индексировать не нужно.")
    elif inside and not outside:
        print("  ВНИМАНИЕ: файл есть ТОЛЬКО в publish.")
        print("  Это значит, что сборка содержит артефакт, источника которого")
        print("  в проекте нет (возможно, он собирается на другой машине).")
        print("  Решение: НЕ исключать этот файл, чтобы он остался в индексе.")

print()
print("=" * 78)
print("СТРУКТУРА ПРОЕКТА: где исходники веб-интерфейса")
print("=" * 78)
for pattern in ("src/*/wwwroot", "src/*/Web", "Web", "wwwroot"):
    for path in sorted(ROOT.glob(pattern)):
        if path.is_dir():
            n = len([f for f in path.rglob("*") if f.is_file()])
            print(f"  {path.relative_to(ROOT)}: {n} файлов")
