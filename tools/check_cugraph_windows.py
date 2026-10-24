"""Проверка: есть ли сборка cugraph под Windows.

Отчёт по cuGraph утверждал, что пакет есть только для Linux, но сравнение
делалось на torch.sparse — то есть вывод про cuGraph остался НЕПРОВЕРЕННЫМ.
Здесь он проверяется прямо: запрашивается список файлов пакета с PyPI и
считается, какие платформы в них заявлены.

Это проверка факта, а не установка: ничего не скачивается и не ставится.
"""

from __future__ import annotations

import json
import urllib.request
from collections import Counter

PACKAGES = ["cugraph-cu12", "cugraph-cu13", "pylibcugraph-cu12", "cudf-cu12"]
API = "https://pypi.org/pypi/{name}/json"

print("=" * 78)
print("ЕСТЬ ЛИ СБОРКА ПОД WINDOWS")
print("=" * 78)

for name in PACKAGES:
    print(f"\n{name}")
    print("-" * 60)
    try:
        with urllib.request.urlopen(API.format(name=name), timeout=25) as response:
            data = json.load(response)
    except Exception as exc:
        print(f"  запрос не удался: {type(exc).__name__}: {exc}")
        continue

    info = data.get("info", {})
    version = info.get("version")
    print(f"  последняя версия: {version}")

    # Ищутся файлы последней версии: по их именам видно платформу.
    files = data.get("releases", {}).get(version, [])
    if not files:
        print("  файлов для последней версии нет")
        continue

    platforms = Counter()
    win_files = []
    for f in files:
        fname = f.get("filename", "")
        packagetype = f.get("packagetype", "")
        if packagetype == "sdist":
            platforms["sdist (исходники)"] += 1
            continue
        # Имя колеса: имя-версия-python-abi-ПЛАТФОРМА.whl
        parts = fname[:-4].split("-") if fname.endswith(".whl") else []
        plat = parts[-1] if len(parts) >= 5 else "?"
        platforms[plat] += 1
        if "win" in plat.lower():
            win_files.append(fname)

    print(f"  всего файлов: {len(files)}")
    print("  платформы:")
    for plat, count in platforms.most_common(12):
        print(f"    {plat}: {count}")

    # Ключевой вывод по этому пакету.
    if win_files:
        print(f"  СБОРКА ПОД WINDOWS ЕСТЬ: {win_files[:3]}")
    else:
        print("  СБОРКИ ПОД WINDOWS НЕТ")

print()
print("=" * 78)
print("ЧТО ЭТО ЗНАЧИТ ПРАКТИЧЕСКИ")
print("=" * 78)
print("""
  Проверка отвечает на вопрос: можно ли поставить cuGraph на этой машине
  (Windows, без WSL). Если сборок под Windows нет — поставить его напрямую
  нельзя, и тогда решение «не включать графовый анализ на видеокарте»
  остаётся обоснованным не мнением, а фактом.

  Важная оговорка: отсутствие сборки НЕ означает, что cuGraph плох.
  В WSL (Linux-подсистема) он поставится и там может оказаться быстрее
  процессора. Наш вывод был про отсутствие альтернативы: на наших графах
  (5520 вершин) даже готовый GPU-обход проигрывал процессору в 136 раз.
""")
