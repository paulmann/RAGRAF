"""Снимок панели управления: разметка страницы и содержимое API (для отчёта).

Запуск: python tools/snapshot_admin.py [--base=http://127.0.0.1:8777]

Что делает: сохраняет разметку страницы и полный ответ /api/state в файл.
Нужно для отчёта: видно, что именно показывает панель, без скриншота.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8777"
for arg in sys.argv[1:]:
    if arg.startswith("--base="):
        BASE = arg.split("=", 1)[1].rstrip("/")

out_dir = REPO / ".staging"
out_dir.mkdir(parents=True, exist_ok=True)


def get(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


html = get("/")
(out_dir / "admin_page.html").write_text(html, encoding="utf-8", newline="\n")
print(f"страница: {len(html)} символов")

state_raw = get("/api/state")
state = json.loads(state_raw)
(out_dir / "admin_state.json").write_text(
    json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
)
print(f"состояние: {len(state_raw)} символов")

print("\n=== что показывает панель ===")
st = state.get("state") or {}
catalog = st.get("catalog") or {}
print(f"разработок: {catalog.get('count')}")
for e in catalog.get("entries") or []:
    size = e.get("size") or {}
    print(f"  {e['category']}: ревизия {e['rev_id']}, сумма совпадает: {e.get('hash_matches')}")
    print(f"    баз: {len(size.get('files') or [])}, таблиц: {len(size.get('tables') or [])}, "
          f"итого: {round((size.get('total_bytes') or 0) / 1024 / 1024, 1)} МБ")

detail = st.get("detail") or {}
for name, d in detail.items():
    usage = d.get("usage") or {}
    quality = d.get("quality") or {}
    q = quality.get("quality") or {}
    print(f"  {name}: обращений {usage.get('calls_total')}, "
          f"проверяемость {usage.get('verifiable_ratio')}, "
          f"чанков {((q.get('chunks') or {}).get('total'))}, "
          f"здоровье индекса: {(q.get('healthy'))}")

settings = st.get("settings") or {}
print(f"\nнастроек в разделе global: {len(settings.get('global_settings') or {})}")
print(f"разработок с настройками: {len(settings.get('per_project') or {})}")
print(f"пояснений к настройкам: {len(settings.get('descriptions') or {})}")
