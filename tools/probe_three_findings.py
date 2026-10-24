"""Разбор трёх находок из проверки C#-корпуса.

Проверка выявила:
  1. op_code вернул 0 строк для найденного символа AlertRule;
  2. op_repo_map вернул пустой состав языков (0);
  3. op_class_usage показал классы CSS (tailwind: flex, px-4) вместо C#-классов.

Нужно понять, что из этого дефект RAGRAF, а что — неправильное ожидание теста.
Разбор идёт по фактам: сырой ответ операции печатается целиком.

Ничего не меняет.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

cfg = load_config(REPO / "config" / "rdpmon.yaml")
fac = Facade(cfg, tool="probe", telemetry=False, watch=False)


def show(title: str, value, limit: int = 2000) -> None:
    print("=" * 78)
    print(title)
    print("=" * 78)
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(value)
    print(text[:limit])
    if len(text) > limit:
        print(f"... (всего {len(text)} символов)")
    print()


# --- 1. op_code ---
fqn = ("src/RdpAudit.Configurator/Services/NotificationCatalogInfo.cs"
       ":AlertRule")
show("1. СЫРОЙ ОТВЕТ op_code", fac.op_code(fqn=fqn, limit=5), 2500)

# Проверяется, лежит ли файл в корпусе и на месте ли строка 21.
path = pathlib.Path(cfg.root) / "src/RdpAudit.Configurator/Services/NotificationCatalogInfo.cs"
print(f"  файл существует: {path.exists()}")
if path.exists():
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"  строк в файле: {len(lines)}")
    print(f"  строки 19-24:")
    for i in range(18, min(24, len(lines))):
        print(f"    {i + 1:>4}: {lines[i][:70]}")
print()

# --- 2. op_repo_map ---
show("2. СЫРОЙ ОТВЕТ op_repo_map", fac.op_repo_map(), 2500)

# --- 3. op_class_usage ---
show("3. СЫРОЙ ОТВЕТ op_class_usage (первые 1500)",
     fac.op_class_usage(name=None, limit=3, mode="all"), 1500)

print("=" * 78)
print("ГИПОТЕЗЫ ДЛЯ ПРОВЕРКИ")
print("=" * 78)
print("""
  1. op_code: возможно, нужен параметр style или другое имя символа
     (в индексе символ записан как struct AlertRule внутри класса
     NotificationCatalogInfo, а fqn может требовать другого вида).
  2. op_repo_map: соответствует действительности, если состав языков
     берётся из корпуса, а не из индекса; тогда это не дефект, а вопрос
     к тесту — надо смотреть на index_status.counts.
  3. op_class_usage: показал CSS-классы. Это ожидаемо для корпуса,
     где есть CSS и разметка, но означает, что классы C# в этот отчёт
     не попадают — проверить, должен ли class_usage покрывать C#.
""")
fac.close()
