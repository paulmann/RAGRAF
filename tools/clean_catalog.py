"""Уборка реестра разработок от тестовых записей и фиксация ревизий.

Что делает:
  1. удаляет из реестра записи тестовых корпусов (projA, projB и подобные),
     оставшиеся от прогонов проверки;
  2. для оставшихся разработок проставляет фактическую ревизию из их индекса —
     регистрация не индексирует, поэтому поле ревизии оставалось пустым.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import util  # noqa: E402

TECH = {"proja", "projb", "p", "dbg", "tmp"}

cat = catalog_mod.Catalog()
data = cat.load()
entries = dict(data.get("entries") or {})

removed: list[str] = []
for name in list(entries):
    low = name.lower()
    root = str((entries[name] or {}).get("root", "")).lower()
    # Тестовые записи опознаются по имени и по пути во временном каталоге.
    is_test = (
        low in TECH
        or low.startswith("proj")
        or "temp" in root
        or "tmp" in root
    )
    if is_test:
        removed.append(name)
        del entries[name]

data["entries"] = entries
util.write_json(cat.path, data, indent=2)
print(f"удалено тестовых записей: {len(removed)}")
if removed:
    print("  ", ", ".join(removed))
print(f"осталось разработок: {len(entries)}")
for name in entries:
    print(f"  {name}: {entries[name].get('root')}")
