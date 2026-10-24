"""Диагностика: где в экспорте Joern находятся узлы и как их читает ijson."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXP = REPO / ".tools" / "exp_graphson" / "export.json"


def main() -> int:
    print(f"файл: {EXP.name} ({EXP.stat().st_size / 1_048_576:.1f} МБ)")

    # 1. Где вообще встречается ключ nodes.
    with open(EXP, "r", encoding="utf-8") as fh:
        pos = 0
        found = -1
        context = ""
        while True:
            chunk = fh.read(5_000_000)
            if not chunk:
                break
            i = chunk.find('"nodes"')
            if i >= 0:
                found = pos + i
                context = chunk[max(0, i - 120) : i + 200]
                break
            pos += len(chunk)
    print(f"ключ 'nodes' найден на позиции: {found}")
    if found >= 0:
        print("контекст:")
        print(context.replace("\n", " ")[:400])

    # 2. Что видит ijson.
    try:
        import ijson
    except ImportError:
        print("ijson не установлен")
        return 1

    for prefix in (
        "@value.nodes.item",
        "value.nodes.item",
        "nodes.item",
        "@value.edges.item",
    ):
        try:
            with open(EXP, "rb") as fh:
                count = 0
                for _ in ijson.items(fh, prefix, use_float=True):
                    count += 1
                    if count >= 100000:
                        break
            print(f"  ijson.items({prefix!r}): {count}")
        except Exception as exc:
            print(f"  ijson.items({prefix!r}): ошибка {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
