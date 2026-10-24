"""Диагностика: что именно отдаёт /api/state и где теряются новые настройки.

Проверка показала противоречие:
  * валидация ЗНАЕТ новые ключи (опечатка `dedupe_scope='doc'` отклонена
    с правильным сообщением) — значит, модуль настроек загружен свежий;
  * состояние их НЕ ОТДАЁТ (все значения None).

Это значит, что теряется не определение ключа, а его прохождение через
ответ сервера. Проверяются все слои по очереди:
  1. `SettingsStore().load()` — читает ли ключи из файла;
  2. `settings.state(...)` — попадают ли они в состояние;
  3. `state.to_dict()` — есть ли они в сериализованном виде;
  4. сырой ответ `/api/state` — что реально уходит клиенту.

Ничего не меняет: только читает.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import settings as settings_mod  # noqa: E402

KEYS = ("dedupe_enabled", "dedupe_scope", "dedupe_mode", "dedupe_keep",
        "dedupe_report")

print("=" * 78)
print("1. SettingsStore().load()")
print("=" * 78)
store = settings_mod.SettingsStore()
loaded = store.load()
print(f"  прочитано ключей: {len(loaded)}")
for key in KEYS:
    print(f"    {key:<16} = {loaded.get(key)!r}")
print(f"  EDITABLE содержит dedupe: "
      f"{[k for k in settings_mod.EDITABLE if k.startswith('dedupe')]}")
print(f"  DEFAULTS содержит dedupe: "
      f"{[k for k in settings_mod.DEFAULTS if k.startswith('dedupe')]}")

print()
print("=" * 78)
print("2. settings.state(entries)")
print("=" * 78)
# Разработки читаются из реестра: нужен тот же вход, что у сервера.
from ragraf.catalog import Catalog  # noqa: E402

try:
    cat = Catalog()
    entries = cat.entries() if hasattr(cat, "entries") else {}
    if not entries and hasattr(cat, "load"):
        data = cat.load()
        entries = data.get("projects") if isinstance(data, dict) else {}
except Exception as exc:
    print(f"  реестр не прочитан: {type(exc).__name__}: {exc}")
    entries = {}
print(f"  разработок в реестре: {len(entries or {})}")

st = settings_mod.state(entries or {})
print(f"  тип состояния: {type(st).__name__}")
print(f"  поля состояния: {sorted(st.to_dict())}")
glob = st.to_dict().get("global_settings") or {}
print(f"  global_settings: {len(glob)} ключей")
for key in KEYS:
    print(f"    {key:<16} = {glob.get(key)!r}")
print(f"  enums: {st.to_dict().get('enums')}")

print()
print("=" * 78)
print("3. СЫРОЙ ОТВЕТ /api/state")
print("=" * 78)
try:
    with urllib.request.urlopen("http://127.0.0.1:8777/api/state", timeout=20) as r:
        raw = json.loads(r.read().decode("utf-8"))
    print(f"  ключи верхнего уровня: {sorted(raw)}")
    inner = raw.get("settings")
    print(f"  тип settings: {type(inner).__name__}")
    if isinstance(inner, dict):
        print(f"  ключи settings: {sorted(inner)[:12]}")
        g = inner.get("global_settings") or {}
        print(f"  global_settings: {len(g)} ключей")
        for key in KEYS:
            print(f"    {key:<16} = {g.get(key)!r}")
        print(f"  enums: {inner.get('enums')}")
        print(f"  editable содержит dedupe: "
              f"{[k for k in (inner.get('editable') or []) if k.startswith('dedupe')]}")
    else:
        # Возможно, settings — строка с JSON внутри.
        print(f"  первые 300 символов: {str(inner)[:300]}")
except Exception as exc:
    print(f"  запрос не удался: {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если пункт 1 даёт ключи, а пункт 3 — нет, значит сервер отдаёт устаревший
  модуль настроек: он запущен ДО правки, и Python держит прежний код в памяти.
  Признак: валидация тоже была бы старой. Но валидация новая — значит,
  расхождение в другом месте, и его покажет сравнение пунктов 2 и 3.
""")
