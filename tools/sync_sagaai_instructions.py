"""Синхронизация инструкций проекта SagaAI после добавления RAGRAF.

Проблема: проект читает инструкции из КЭША в базе данных, а не с диска
напрямую. Кэш заполняется только при первом обращении (когда он пуст)
либо по кнопке «синхронизация» в интерфейсе.

Поэтому файл `ragraf_index.md` лежит на диске, но проект его не видит:
в кэше 9 инструкций, а файлов 10.

Решение: вызвать штатную функцию синхронизации проекта. Это ровно то,
что делает сам проект при старте и по кнопке в настройках — то есть
вмешательства в чужие данные нет, просто обновляется кэш.
"""

from __future__ import annotations

import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

from core.orchestrator_folders import (  # noqa: E402
    list_orchestrator_instructions,
    sync_orchestrator_instructions,
)

print("=" * 78)
print("ДО СИНХРОНИЗАЦИИ")
print("=" * 78)
before = list_orchestrator_instructions("dev_agent")
before_ids = [i.get("id") for i in before]
print(f"  инструкций в кэше: {len(before_ids)}")
print(f"    {before_ids}")
print(f"  ragraf_index виден: {'ragraf_index' in before_ids}")

print()
print("=" * 78)
print("СИНХРОНИЗАЦИЯ (штатная функция проекта)")
print("=" * 78)
count = sync_orchestrator_instructions("dev_agent")
print(f"  синхронизировано записей: {count}")

print()
print("=" * 78)
print("ПОСЛЕ СИНХРОНИЗАЦИИ")
print("=" * 78)
after = list_orchestrator_instructions("dev_agent")
after_ids = [i.get("id") for i in after]
print(f"  инструкций в кэше: {len(after_ids)}")
for i in after:
    print(f"    {str(i.get('id')):<24} {str(i.get('name'))[:44]}")

visible = "ragraf_index" in after_ids
print()
print(f"  ragraf_index виден: {visible}")

# Проверка: текст инструкции доступен целиком (не только метаданные).
if visible:
    from core.orchestrator_folders import get_orchestrator_instruction

    full = get_orchestrator_instruction("dev_agent", "ragraf_index")
    if full:
        text = str(full.get("text") or "")
        print(f"  размер текста: {len(text)} символов")
        print(f"  описание: {str(full.get('description'))[:100]}")
        checks = {
            "примеры вызова": "ragraf(operation=" in text,
            "перечень операций": "search" in text and "symbol" in text,
            "раздел о границах": "НЕ умеет" in text,
            "имя агента": "SagaAI" in text,
        }
        for label, ok in checks.items():
            print(f"    [{'OK ' if ok else '--'}] {label}")
    else:
        print("  текст инструкции получить не удалось")

print()
print("=" * 78)
print("ИТОГ")
print("=" * 78)
print(f"  было инструкций: {len(before_ids)}")
print(f"  стало:           {len(after_ids)}")
print(f"  RAGRAF виден:    {visible}")
raise SystemExit(0 if visible else 1)
