#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Число рёбер воспроизводится: два прогона на неизменённом корпусе дают одно значение.

Зачем. Гейт публикации предупреждает, что «число рёбер не воспроизводится между
пересборками», опираясь на замер 25.10.2026 (64 971 / 64 972 / 64 968 при неизменном
дереве). Проверить это утверждение было нечем: числа сравнивались только с записью
активной ревизии, а не между двумя прогонами.

Что делает. Запускает `index` N раз (по умолчанию 2) и сравнивает число файлов, узлов и
рёбер каждой ревизии. Если числа разошлись — печатает ревизии и значения и возвращает 1:
расхождение при неизменном дереве означает недетерминизм, а не «устаревший документ».

Запуск:
    python tools/check_edges_repeatable.py
    python tools/check_edges_repeatable.py --runs 3
    python tools/check_edges_repeatable.py --project config/ragraf.yaml

Коды: 0 — все прогоны совпали, 1 — расхождение, 2 — проверить не удалось (нет конфига,
прогон не завершился).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [item for item in sys.path if str(Path(item or ".").resolve()) != _TOOLS_DIR]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


def snapshot(config: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "rev_snapshot.py"),
         "--project", str(config)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    for line in reversed((completed.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return {}
    return {}


def edge_kinds(project_config: Path, rev_id: str) -> dict[str, int]:
    """Разбивка рёбер ревизии по видам: итог без неё не говорит, ЧТО разошлось."""
    from ragraf import config as config_mod  # noqa: PLC0415
    from ragraf import store  # noqa: PLC0415

    try:
        cfg = config_mod.load(str(project_config))
    except Exception:  # noqa: BLE001 — причина отказа известна вызывающему
        return {}
    graph_db = Path(cfg.graph_db)
    if not graph_db.is_file():
        return {}
    try:
        connection = store.connect(graph_db, readonly=True)
    except Exception:  # noqa: BLE001
        return {}
    try:
        rows = connection.execute(
            "SELECT kind, COUNT(*) AS n FROM edge WHERE rev_id = ? GROUP BY kind",
            (rev_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    finally:
        connection.close()
    result: dict[str, int] = {}
    for row in rows:
        try:
            result[str(row["kind"])] = int(row["n"])
        except (KeyError, TypeError, ValueError):
            key, value = row[0], row[1]
            result[str(key)] = int(value)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="воспроизводимость чисел ревизии между пересборками")
    parser.add_argument("--runs", type=int, default=2, help="сколько прогонов сделать")
    parser.add_argument("--project", default=str(REPO_ROOT / "config" / "ragraf.yaml"),
                        help="конфиг корпуса")
    args = parser.parse_args(argv)

    config = Path(args.project).resolve()
    if not config.is_file():
        print(f"FAIL: нет конфига {config}")
        return 2

    seen: list[dict] = []
    for number in range(1, max(2, args.runs) + 1):
        print(f"прогон {number} ...", end=" ", flush=True)
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "ragraf.py"),
             "-p", str(config), "index"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-3:]
            print("не выполнен")
            print("FAIL: прогон завершился кодом", completed.returncode, "|", " / ".join(tail))
            return 2
        state = snapshot(config)
        if not state.get("rev_id"):
            print("снимок не прочитан")
            return 2
        kinds = edge_kinds(config, str(state["rev_id"]))
        state["kinds"] = kinds
        seen.append(state)
        print(f"{state['rev_id']}: {state['files']} / {state['nodes']} / {state['edges']}"
              f" (видов рёбер: {len(kinds)})")

    unique = {(item["files"], item["nodes"], item["edges"]) for item in seen}
    print()
    if len(unique) == 1:
        files, nodes, edges = next(iter(unique))
        print(f"совпало: {len(seen)} прогона дали одно число — {files} / {nodes} / {edges}")
        print(f"осмотрено: {len(seen)}")
        return 0

    print("FAIL: числа разошлись при неизменённом дереве — это недетерминизм, а не документ:")
    for item in seen:
        print(f"  {item['rev_id']}: {item['files']} / {item['nodes']} / {item['edges']}")
    # Разбивка по видам: без неё непонятно, искать причину в коде или в документе.
    first = seen[0].get("kinds") or {}
    for item in seen[1:]:
        other = item.get("kinds") or {}
        for kind in sorted(set(first) | set(other)):
            left, right = first.get(kind, 0), other.get(kind, 0)
            if left != right:
                print(f"  расходится вид «{kind}»: {left} против {right}"
                      f" ({right - left:+d})")
    print(f"осмотрено: {len(seen)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
