"""Диагностика попаданий: сравнить операции search и symbol по задачам полигона.

Зачем: замер показал 46% попаданий в файл-цель, и нужно понять, это дефект
индекса или неудачный подбор операций в наборе задач. Разница принципиальна:
если symbol попадает, а search нет — это не ошибка индекса, а ошибка замерщика.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

# Функции и символы, определения которых ищут задачи набора.
SYMBOLS = [
    "z_agr_journals", "z_mail_rows", "z_boot", "z_e", "z_agr_scope_sql",
    "z_agr_next_no", "z_mail_file_deny", "z_agr_setting", "z_agr_ac_audit",
    "z_agr_setting_set", "z_agr_local_cfg", "z_agr_now", "z_agr_rows",
]

# Запросы, которые в наборе заданы поиском: проверяем, куда они ведут.
SEARCH_QUERIES = [
    ("Z-02", "отдача файла проверка realpath каталог", ["mail/file.php"]),
    ("Z-03", "сборка строк реестра корреспонденции сортировка пагинация", ["mail/rows.php"]),
    ("Z-04", "bootstrap загрузка конфигурации подключение модулей", ["src/Bootstrap.php"]),
    ("Z-07", "маппинг журналов реестр код таблица префикс", ["src/Agreements/Engine.php"]),
    ("Z-09", "шаг согласования слепок решения SignHash раунд", ["DocApproval", "20261004_0013"]),
    ("Z-10", "реестр настроек шифрование секретов sodium", ["src/Agreements/Setting.php"]),
    ("Z-11", "выдача номера документа транзакция блокировка счётчик", ["src/Agreements/Numbering.php"]),
    ("Z-13", "миграция ядро подразделения сотрудники роли таблицы", ["20261004_0013", "DocDepartment"]),
    ("Z-14", "правила проекта конвенции инварианты агент запреты", ["AGENTS"]),
]


def main() -> int:
    cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
    facade = Facade(cfg)
    try:
        print("=== symbol: где ОПРЕДЕЛЕНЫ функции ===")
        for name in SYMBOLS:
            data = facade.op_symbol(name, limit=3)
            rows = data.get("result") or []
            paths = [str(r.get("path")) for r in rows if r.get("path")]
            mark = "OK " if paths else "нет"
            print(f"  [{mark}] {name:22} {paths if paths else '(не найдено)'}")

        print("\n=== search: куда ведут запросы из набора ===")
        for task_id, query, expect in SEARCH_QUERIES:
            data = facade.op_search(query, limit=5)
            rows = data.get("result") or []
            paths = [str(r.get("path")) for r in rows]
            hit = any(any(e in p for e in expect) for p in paths)
            mark = "ЦЕЛЬ" if hit else "мимо"
            print(f"  [{mark}] {task_id}: ожидалось {expect}")
            for p in paths[:3]:
                print(f"           {p}")

        print("\n=== rg по тем же темам (что видит агент без индекса) ===")
        import subprocess

        for name in ("z_agr_journals", "z_mail_rows", "z_boot", "z_agr_next_no"):
            proc = subprocess.run(
                ["rg", "--files-with-matches", "-e", f"function {name}", "."],
                cwd=str(cfg.root), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            files = [f for f in (proc.stdout or "").splitlines() if f.strip()]
            print(f"  rg 'function {name}': {files if files else '(не найдено)'}")
    finally:
        facade.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
