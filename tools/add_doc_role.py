"""Добавление роли документа в хранилище чанков.

Зачем
    Документация в индексе была безликой: чанк из технического задания выглядел
    так же, как чанк из журнала правок. При этом роль почти всегда видна из имени
    файла (AGENTS.md, README, ТЗ-*) — разведка по двум каталогам это подтвердила.

    Пометка бесполезна, если её видит только индекс. Поэтому роль кладётся в
    chunk_map — оттуда её читает поиск и показывает агенту вместе с результатом.

Где именно
    Существующая колонка labels не подходит: она уже занята меткой символа.
    Добавляется колонка `doc_role`. Старые базы догоняются через PRAGMA
    table_info — тот же приём, что уже применён для таблицы edge (U4),
    иначе на существующих корпусах колонки бы не было.
"""

from __future__ import annotations

import pathlib
import re

TARGET = pathlib.Path(__file__).resolve().parents[1] / "src" / "ragraf" / "lexvec.py"
text = TARGET.read_text(encoding="utf-8")

# 1. Обратная совместимость схемы: догоняющая миграция колонки doc_role.
old_init = """        self.extension_loaded = store.try_load_extension(self.conn, "vec0")
        store.init_lexvec(self.conn)
        self.vec_ok = store.ensure_vec_table(self.conn)
        self.conn.commit()"""
new_init = """        self.extension_loaded = store.try_load_extension(self.conn, "vec0")
        store.init_lexvec(self.conn)
        self.vec_ok = store.ensure_vec_table(self.conn)
        # Догоняющая миграция: роль документа появилась позже самой таблицы.
        # Без неё на существующих корпусах (z, revoap, md.viewer) колонки нет,
        # и запись чанков упала бы с OperationalError. Тот же приём уже
        # применён для props в таблице edge.
        self._migrate_doc_role()
        self.conn.commit()

    def _migrate_doc_role(self) -> None:
        \"\"\"Добавить колонку роли документа, если её ещё нет.\"\"\"
        try:
            cols = {
                str(r["name"])
                for r in self.conn.execute("PRAGMA table_info(chunk_map)").fetchall()
            }
        except sqlite3.Error:
            return
        if not cols:
            return
        if "doc_role" not in cols:
            try:
                self.conn.execute("ALTER TABLE chunk_map ADD COLUMN doc_role TEXT")
            except sqlite3.Error:
                # Колонка могла появиться параллельно: это не ошибка.
                pass"""
if old_init not in text:
    raise SystemExit("НЕ НАЙДЕН блок инициализации LexVecStore")
text = text.replace(old_init, new_init, 1)

# 2. Запись роли вместе с чанком.
old_map = """        map_rows = []"""
new_map = """        map_rows = []
        # Роль документа считается один раз на файл: чанков из одного файла
        # бывает много, а роль у них одна. Разбор заголовка — тоже на файл.
        doc_roles: dict[str, object] = {}"""
if old_map not in text:
    raise SystemExit("НЕ НАЙДЕН блок map_rows")
text = text.replace(old_map, new_map, 1)

print("Правка 1-2 применены. Ищу место формирования строк chunk_map...")

# Показать, как формируется map_rows, чтобы вставить роль точно.
idx = text.find("map_rows.append")
print(text[idx - 900 : idx + 600])
