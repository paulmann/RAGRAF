#!/usr/bin/env python3
"""Снимок ревизии: сколько файлов, узлов и рёбер записано в активной ревизии.

Зачем отдельный инструмент. README публикует размеры собственного индекса как
измерение («N файлов, M узлов, K рёбер»), но измерение без проверки стареет
молча: на 25.10.2026 документ называл 804 / 4 771 / 61 080 при фактических
810 / 4 814 / 62 162, и заметить это было нечем. Числа берутся из ЗАПИСИ
РЕВИЗИИ (таблица `rev`), а не из документа и не из литерала: сверка идёт с
фактом, который записал индексатор.

Печатает JSON:
    {"rev_id": "rev-0284", "files": 810, "nodes": 4814, "edges": 62162,
     "capability": "full", "state": "ready"}

Ревизия, которая ещё запечатывается, снимком не является. Вместо нулей
печатается явное состояние, и код возврата ненулевой:

    {"status": "in_progress", "rev_id": "rev-0293", "state": "building",
     "created_at": "2026-10-25T12:51:54+03:00", "reason": "..."}

Почему нули запрещены: «0 файлов» читается как измерение, хотя это половина
записи. Измерено 25.10.2026 (ПРОМТ-И14): прогон в 12:51 получил 0/0/0 при
фактических 815/4828/62812, и проверка чисел в README объявила ложный провал.

Режим `--match F,N,E` отвечает на ДРУГОЙ вопрос: было ли когда-нибудь у этого
корпуса состояние «F файлов, N узлов, E рёбер». Он нужен проверке чисел в
README, чтобы различать две разные вещи, которые до 25.10.2026 считались одной:

    документ ОТСТАЛ  — названные числа были верны на rev-0308, индекс с тех пор
                       вырос; это не ложь, а устаревший замер;
    документ ВРЁТ    — таких чисел не было ни в одной ревизии.

Различие берётся из `rev_history`, а не из `rev`: таблица `rev` живёт в
графе и ротация оставляет в ней только последние ревизии (замер 25.10.2026:
три записи), а `rev_history` не удаляется никогда — ротация лишь меняет
`state` на `rotated` (`src/ragraf/revisions.py:1-5`). Поэтому по ней видно
состояние, которое документ мог называть раньше.

Печатает JSON:
    {"queried": {...}, "active": {"rev_id": "rev-0311", "files": 823, ...},
     "match": {"rev_id": "rev-0308", ...} | null, "match_is_active": false,
     "rows": 311, "oldest": "...", "newest": "...", "project": "ragraf"}

Коды возврата: 0 — снимок запечатанной ревизии получен (или история ревизий
прочитана в режиме `--match`); 2 — снимка нет и быть не может (нет конфига,
нет базы, нет ревизии, ревизия ещё запечатывается) либо история не прочитана.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

# Каталог скрипта выкидывается из sys.path: файл `tools/ragraf.py` иначе
# затеняет пакет `src/ragraf` при установленном пакете (измерено 12.10.2026).
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

#: Состояния, в которых запись ревизии считается запечатанной. `building` —
#: запись создана, числа ещё не записаны; `failed` — прогон не состоялся.
SEALED_STATES = ("ready", "sealed")

#: Состояния истории, в которых числа означают СОСТОЯНИЕ ПРОДУКТА: `rotated`
#: это ревизия, чьи данные удалены ротацией, но запись и числа остались.
#: `building` и `failed` в счёт не идут: у первой числа ещё не записаны,
#: вторая не описывает ничего отданного.
HISTORY_STATES = ("ready", "rotated")


def _snapshot(cfg, rev: str) -> int:
    """Печатает числа активной (или указанной) ревизии из графа."""
    db = Path(cfg.graph_db)
    if not db.exists():
        print(json.dumps({"error": f"нет базы графа: {db}"}, ensure_ascii=False))
        return 2

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # state и created_at читаются вместе с числами: снимок НЕЗАПЕЧАТАННОЙ
        # ревизии — не измерение, а половина записи.
        columns = ("rev_id, file_count, node_count, edge_count, capability,"
                   " state, created_at")
        if rev:
            row = conn.execute(
                f"SELECT {columns} FROM rev WHERE rev_id = ?", (rev,)).fetchone()
        else:
            row = conn.execute(
                f"SELECT {columns} FROM rev ORDER BY rev_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    if row is None:
        print(json.dumps({"error": f"ревизии {rev or '(активная)'} нет в базе"}, ensure_ascii=False))
        return 2

    state = str(row[5] or "")
    created_at = str(row[6] or "")
    files = int(row[1] or 0)
    if state not in SEALED_STATES or files == 0:
        reason = (
            "ревизия ещё запечатывается: числа появятся после seal"
            if state not in SEALED_STATES
            else "ревизия запечатана, но в ней ноль файлов: измерять нечего"
        )
        print(json.dumps({"status": "in_progress", "rev_id": str(row[0]),
                          "state": state, "created_at": created_at,
                          "reason": reason}, ensure_ascii=False))
        return 2

    payload = {"rev_id": str(row[0]), "files": files,
               "nodes": int(row[2] or 0), "edges": int(row[3] or 0),
               "capability": str(row[4] or ""), "state": state}
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _history(cfg, project: str) -> list[dict]:
    """История ревизий корпуса из control.sqlite — только чтение.

    Порядок задаётся `rev_id DESC`, а не временем: активная ревизия
    определяется нумерацией, часы машины в этом не участвуют (AGENTS.md,
    раздел 4). Соединение открывается штатным путём `store.connect(...,
    readonly=True)`: чужой индекс эта функция не меняет и заблокировать не может.
    """
    from ragraf import store  # noqa: PLC0415 — импорт после правки sys.path

    db = Path(cfg.control_db)
    if not db.exists():
        raise RuntimeError(f"нет хранилища управления: {db}")
    conn = store.connect(db, readonly=True)
    try:
        if not store.table_exists(conn, "rev_history"):
            raise RuntimeError(f"в {db.name} нет таблицы rev_history")
        placeholders = ",".join("?" * len(HISTORY_STATES))
        rows = conn.execute(
            f"""SELECT rev_id, created_at, state, files, nodes, edges
                FROM rev_history
                WHERE project = ? AND files IS NOT NULL
                  AND state IN ({placeholders})
                ORDER BY rev_id DESC""",
            (project, *HISTORY_STATES),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _rev_view(row: dict) -> dict:
    return {"rev_id": str(row["rev_id"]), "created_at": str(row["created_at"]),
            "state": str(row["state"]), "files": int(row["files"]),
            "nodes": int(row["nodes"]), "edges": int(row["edges"])}


def _match(cfg, spec: str) -> int:
    """Было ли у корпуса состояние «F файлов, N узлов, E рёбер»."""
    try:
        files, nodes, edges = (int(part) for part in spec.split(","))
    except ValueError:
        print(json.dumps({"error": f"не разобраны числа: {spec!r} (ожидается F,N,E)"},
                         ensure_ascii=False))
        return 2

    try:
        rows = _history(cfg, cfg.project)
    except Exception as exc:  # noqa: BLE001 — причина отказа важнее типа
        print(json.dumps({"error": f"история ревизий не прочитана: "
                                   f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2

    if not rows:
        print(json.dumps({"error": "в rev_history нет ни одной ревизии с числами: "
                                   "сверять не с чем"}, ensure_ascii=False))
        return 2

    active = rows[0]
    wanted = (files, nodes, edges)
    match = next((row for row in rows
                  if (int(row["files"]), int(row["nodes"]), int(row["edges"])) == wanted),
                 None)
    payload = {
        "project": cfg.project,
        "queried": {"files": files, "nodes": nodes, "edges": edges},
        "active": _rev_view(active),
        "match": _rev_view(match) if match is not None else None,
        "match_is_active": bool(match is not None
                               and str(match["rev_id"]) == str(active["rev_id"])),
        "rows": len(rows),
        "oldest": str(rows[-1]["created_at"]),
        "newest": str(active["created_at"]),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="снимок активной ревизии корпуса")
    parser.add_argument("--project", default=str(REPO_ROOT / "config" / "ragraf.yaml"),
                        help="конфиг корпуса (по умолчанию config/ragraf.yaml проекта)")
    parser.add_argument("--rev", default="", help="идентификатор ревизии; пусто — активная")
    parser.add_argument("--match", default="",
                        help="F,N,E — было ли у корпуса такое состояние (история ревизий)")
    args = parser.parse_args()

    try:
        from ragraf.config import load as load_config  # noqa: PLC0415

        cfg = load_config(args.project)
    except Exception as exc:  # noqa: BLE001 — причина отказа важнее типа
        print(json.dumps({"error": f"конфиг не прочитан: {type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        return 2

    if args.match:
        return _match(cfg, args.match)
    return _snapshot(cfg, args.rev)


if __name__ == "__main__":
    sys.exit(main())
