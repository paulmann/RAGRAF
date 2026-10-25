"""Разведка: фактическая структура ответов фасада.

Тест не сходится, потому что я угадывал имена полей. Правильный путь —
посмотреть, что операция возвращает на самом деле, и уже по этому писать
проверки. Ничего не меняет: только вызывает операции и печатает структуру.
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


def describe(name: str, value, depth: int = 0) -> None:
    """Показать структуру значения без вывода всего содержимого."""
    pad = "  " * depth
    if isinstance(value, dict):
        print(f"{pad}{name}: объект, ключи: {sorted(value)[:14]}")
        for key in sorted(value)[:10]:
            describe(key, value[key], depth + 1)
    elif isinstance(value, list):
        print(f"{pad}{name}: список из {len(value)}")
        if value:
            describe("[0]", value[0], depth + 1)
    elif isinstance(value, str):
        one = value.replace("\n", " ")[:70]
        print(f"{pad}{name}: строка ({len(value)} симв.): {one}")
    else:
        print(f"{pad}{name}: {type(value).__name__} = {value}")


print("=" * 78)
print("СТРУКТУРА ОТВЕТА op_index_status")
print("=" * 78)
st = fac.op_index_status()
describe("index_status", st)

print()
print("=" * 78)
print("СТРУКТУРА ОТВЕТА op_search")
print("=" * 78)
sr = fac.op_search(query="AlertRule", limit=2)
describe("search", sr)

print()
print("=" * 78)
print("СТРУКТУРА ОТВЕТА op_symbol")
print("=" * 78)
sy = fac.op_symbol(name="AlertRule", limit=2)
describe("symbol", sy)

print()
print("=" * 78)
print("СТРУКТУРА ОТВЕТА op_repo_map")
print("=" * 78)
rm = fac.op_repo_map()
describe("repo_map", rm)

print()
print("=" * 78)
print("ПОЛНЫЙ ОТВЕТ index_status (первые 2500 символов JSON)")
print("=" * 78)
try:
    text = json.dumps(st, ensure_ascii=False, indent=2, default=str)
    print(text[:2500])
except Exception as exc:
    print(f"  сериализация не удалась: {type(exc).__name__}: {exc}")
    print(f"  тип: {type(st)}")

fac.close()
