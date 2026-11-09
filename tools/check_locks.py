#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Периодическая проверка брошенных замков индексации и реестра.

Зачем отдельный вход, а не только `ragraf locks`. Периодический прогон должен
иметь ОДИН вердикт и код возврата, чтобы его можно было поставить в расписание
и не разбирать текст:

    0 — чисто: замков нет либо брошенные сняты молча;
    1 — есть живой, но замолчавший замок: нужен человек (процесс мог зависнуть);
    2 — проверка не выполнена (нет каталога состояния, ошибка чтения).

Что снимается автоматически: только замок с МЁРТВЫМ держателем и нечитаемый
файл. Снять замок работающего прогона означает запустить вторую индексацию того
же корпуса, поэтому такое — отдельное решение (`ragraf.locks --clear --force
--reason …`), а не расписание.

Запуск:
    python tools/check_locks.py
    python tools/check_locks.py --no-clear --json
    python tools/check_locks.py --log .zwork/locks-check.log   # для планировщика

Почему свой лог, а не перенаправление в задании планировщика: строку задания
Windows разбирает по-своему, и `>> ...` внутри неё съедается — прогон молча не
работал (замерено 06.11.2026: задание отчитывалось кодом 1 без единой строки
в файле). Лог пишет сама проверка.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# tools/ не должен затенять пакет src/ragraf (измерено 12.10.2026).
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [item for item in sys.path if str(Path(item or ".").resolve()) != _TOOLS_DIR]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="брошенные замки: проверка и уборка")
    parser.add_argument("--no-clear", action="store_true",
                        help="только показать, ничего не снимать")
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    parser.add_argument("--log", default="",
                        help="дописать вывод в файл (для запуска по расписанию)")
    args = parser.parse_args(argv)
    lines: list[str] = []

    def out(text: str) -> None:
        lines.append(text)
        print(text)

    code = run(args, out)
    if args.log:
        try:
            path = Path(args.log)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%S')} код {code} ===\n")
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            print(f"[locks] лог не записан: {type(exc).__name__}: {exc}")
    return code


def run(args: argparse.Namespace, out) -> int:
    """Тело проверки; `out` — куда писать строки (экран и лог одновременно)."""

    try:
        from ragraf import catalog as catalog_mod

        cat = catalog_mod.Catalog()
        removed = [] if args.no_clear else cat.clear_abandoned_locks()
        report = cat.lock_report()
    except Exception as exc:  # noqa: BLE001 — проверка обязана назвать причину
        out(f"[locks] проверка не выполнена: {type(exc).__name__}: {exc}")
        return 2

    silent = [row for row in report if row["verdict"] == "silent"]
    alive = [row for row in report if row["verdict"] == "alive"]
    if args.json:
        out(json.dumps({"locks": report, "removed": removed,
                        "silent": len(silent), "alive": len(alive)},
                       ensure_ascii=False, indent=2))
    else:
        if removed:
            for row in removed:
                out(f"  снят брошенный замок: {row['file']} — {row['reason']}")
        for row in alive:
            out(f"  идёт: {row['category']} (pid {row['pid']}, "
                f"молчит {row['idle_seconds']:.0f} с)")
        for row in silent:
            out(f"  НАХОДКА: замок {row['file']} — {row['reason']}")
        if not report and not removed:
            out("[locks] замков нет: брошенных тоже")
        elif silent:
            out("  → живой, но замолчавший замок сам не снимается: проверьте процесс, "
                "затем `ragraf locks --clear --force --reason «…»`")
        else:
            out("[locks] брошенных замков нет")
    return 1 if silent else 0


if __name__ == "__main__":
    raise SystemExit(main())
