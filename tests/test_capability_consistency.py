"""Согласованность режима (`capability`) с содержимым графа.

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_capability_consistency.py`).

Какой дефект закрывается. Режим `capability` — утверждение о возможностях
индекса, а не украшение ответа. `full` означает «операции среза доступны», то
есть в графе есть рёбра, пришедшие от графа свойств кода (CPG). Измеренный
дефект: обходной путь экспорта возвращал `full`, даже когда не добавил НИ ОДНОГО
ребра (ревизия rev-0012: запросов 131 639, подтверждено в графе 0). Клиент видел
`full`, вызывал срез и получал ошибку вместо честного «недоступно».

Проверка формулирует инвариант прямо: если режим заявлен как `full`, в графе
обязаны быть рёбра с источником `joern`. Если таких рёбер нет, режим обязан быть
`partial` или `degraded`.

Ничего не меняет: только читает хранилище.
"""

from __future__ import annotations

import sys
from pathlib import Path


# Отчёт печатается по-русски, а консоль Windows по умолчанию не UTF-8: без этой
# настройки запуск падал UnicodeEncodeError ('charmap' codec can't encode),
# причём только на Windows и только вне UTF-8-консоли — в CI это валило job.
# Проверено: PYTHONIOENCODING=cp1252 -> exit 1. Настройка делает тест
# независимым от локали/консоли (Python 3.7+: TextIOWrapper.reconfigure).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def main() -> int:
    cfg_path = REPO / "config" / "ragraf.yaml"
    print("=== Согласованность режима с содержимым графа ===")
    if not cfg_path.exists():
        print("  конфигурация корпуса не найдена — проверять нечего")
        print("\n=== ИТОГ ===\nпройдено: 0, провалено: 0 (неприменимо)")
        return 0

    cfg = load_config(cfg_path)
    if not cfg.graph_db.exists():
        print("  индекс не создан — проверять нечего")
        print("\n=== ИТОГ ===\nпройдено: 0, провалено: 0 (неприменимо)")
        return 0

    conn = store.connect(cfg.graph_db, readonly=True)
    try:
        row = conn.execute(
            "SELECT rev_id, capability, node_count, edge_count, joern_used"
            " FROM rev ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            check(False, "последняя ревизия найдена", "таблица rev пуста")
            print("\n=== ИТОГ ===")
            print(f"пройдено: {PASS}, провалено: {FAIL}")
            return 1

        rev = str(row["rev_id"])
        capability = str(row["capability"] or "")
        print(f"  ревизия {rev}: capability={capability!r}, узлов={row['node_count']},"
              f" рёбер={row['edge_count']}, joern_used={row['joern_used']}")

        # Рёбра, пришедшие от графа свойств кода: источник помечен `joern`.
        from_joern = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ? AND sources LIKE ?",
                (rev, "%joern%"),
            ).fetchone()["c"]
        )
        # Подтверждённые вызовы любого происхождения — то, на чём стоит срез
        # частично, но НЕ доказательство полной сборки свойств кода.
        calls_any = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ?"
                " AND kind IN ('calls','calls_heuristic')",
                (rev,),
            ).fetchone()["c"]
        )
        print(f"  рёбер от CPG: {from_joern}; подтверждённых вызовов всего: {calls_any}")

        check(capability in ("full", "partial", "degraded"),
              "режим из известного набора", repr(capability))

        if capability == "full":
            check(from_joern > 0,
                  "режим full подтверждён рёбрами от графа свойств кода",
                  f"capability='full', но рёбер с источником joern {from_joern}:"
                  " срез будет недоступен, а клиенту обещано обратное")
            check(bool(row["joern_used"]),
                  "при режиме full признак использования CPG выставлен",
                  f"joern_used={row['joern_used']!r}")
        else:
            # Обратная сторона инварианта: неполный режим обязан быть объясним.
            check(True, f"режим {capability} не требует рёбер от CPG")

        # Границы среза совпадают с содержанием графа: если рёбер от CPG нет,
        # режим не может быть full — это и есть проверяемое утверждение.
        if from_joern == 0:
            check(capability != "full",
                  "без рёбер от CPG режим не заявлен как full",
                  f"capability={capability!r} при нуле рёбер от CPG")

        # Числа в журнале ревизии не должны противоречить таблицам.
        actual_edges = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ?", (rev,)
            ).fetchone()["c"]
        )
        actual_nodes = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM node WHERE rev_id = ?", (rev,)
            ).fetchone()["c"]
        )
        check(int(row["edge_count"]) == actual_edges,
              "число рёбер в журнале совпадает с таблицей",
              f"журнал={row['edge_count']}, таблица={actual_edges}")
        check(int(row["node_count"]) == actual_nodes,
              "число узлов в журнале совпадает с таблицей",
              f"журнал={row['node_count']}, таблица={actual_nodes}")
    finally:
        conn.close()

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
