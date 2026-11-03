#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Активная ревизия и корпус: одно и то же множество файлов.

Зачем отдельная проверка (измерено 29.10.2026). Трижды за сутки активная ревизия называла
не то число файлов, что даёт скан корня: 912 против 908, 910 против 906, 908 против 904.
Каждый раз это обнаруживалось КОСВЕННО — через числа README в гейте публикации, — и
сообщение говорило про устаревший документ, а не про расхождение индекса с корпусом.
Искать причину глазами бесполезно: разница в четыре файла из девятисот.

Что проверяется (свойство, а не написание):

  1. скан корня непустой — ноль файлов с обеих сторон не совпадение, а поломка обхода;
  2. число файлов активной ревизии равно числу файлов скана;
  3. множество путей ревизии (`file`, не `chunk_map`: чанки есть только у документов и
     части кода) совпадает с множеством путей скана, и разница печатается ИМЕНАМИ —
     «+4» без имён не помогает найти виновника.

Запуск:
    python tools/check_corpus_revision.py
    python tools/check_corpus_revision.py -p config/ragraf.yaml

Коды: 0 — совпадает, 1 — расхождение (с именами), 2 — проверить нечем (нет конфига,
нет базы графа, нет запечатанной ревизии).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

# tools/ не должен затенять пакет src/ragraf (измерено 12.10.2026).
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [item for item in sys.path if str(Path(item or ".").resolve()) != _TOOLS_DIR]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="сверка активной ревизии с фактическим сканом корпуса")
    parser.add_argument("-p", "--project", default=str(REPO_ROOT / "config" / "ragraf.yaml"),
                        help="конфиг корпуса")
    args = parser.parse_args(argv)

    from ragraf import config as config_mod  # noqa: PLC0415
    from ragraf import corpus, store  # noqa: PLC0415

    config_path = Path(args.project).resolve()
    if not config_path.is_file():
        print(f"FAIL: нет конфига {config_path}")
        return 2
    try:
        cfg = config_mod.load(str(config_path))
    except Exception as error:  # noqa: BLE001 — причина отказа важнее типа
        print(f"FAIL: конфиг не читается: {type(error).__name__}: {error}")
        return 2

    graph_db = Path(cfg.graph_db)
    if not graph_db.is_file():
        print(f"FAIL: нет базы графа {graph_db}")
        return 2

    problems: list[str] = []
    examined = 0

    try:
        scanned = {item.path for item in corpus.scan(cfg).files}
    except Exception as error:  # noqa: BLE001
        print(f"FAIL: скан корня не выполнен: {type(error).__name__}: {error}")
        return 2
    examined += 1
    if not scanned:
        problems.append("скан корня пуст: ноль файлов — поломка обхода, а не совпадение")

    connection = store.connect(graph_db, readonly=True)
    try:
        active, skipped = store.active_revision(connection)
        if active is None:
            print("FAIL: нет ни одной запечатанной ревизии — сверять не с чем")
            return 2
        rev_id = str(active.get("rev_id"))
        stated = int(active.get("file_count") or 0)
        try:
            in_revision = {str(row[0]) for row in connection.execute(
                "SELECT DISTINCT path FROM file WHERE rev_id = ?", (rev_id,))}
        except sqlite3.Error as error:
            print(f"FAIL: пути ревизии не читаются: {error}")
            return 2
    finally:
        connection.close()

    examined += 1
    if stated != len(scanned):
        problems.append(f"активная ревизия {rev_id} называет {stated} файлов, "
                        f"скан корня — {len(scanned)}")

    examined += 1
    if not in_revision:
        problems.append("в ревизии нет путей файлов: сравнить множества нечем")
    else:
        only_revision = sorted(in_revision - scanned)
        only_scan = sorted(scanned - in_revision)
        if only_revision or only_scan:
            detail = f"путей в ревизии {len(in_revision)}, в скане {len(scanned)}"
            if only_revision:
                detail += "; только в ревизии: " + ", ".join(only_revision[:6])
            if only_scan:
                detail += "; нет в ревизии: " + ", ".join(only_scan[:6])
            problems.append(detail)

    print(f"активная ревизия: {rev_id} | файлов в записи: {stated} | "
          f"файлов в скане: {len(scanned)} | путей в ревизии: {len(in_revision)}")
    if skipped:
        print(f"  новее активной, но не запечатаны: "
              f"{', '.join(str(item.get('rev_id')) for item in skipped[:4])}")
    for problem in problems:
        print(f"FAIL: {problem}")
    print(f"осмотрено: {examined}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
