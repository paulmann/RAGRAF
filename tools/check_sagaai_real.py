"""Фактическая проверка: что возвращает RAGRAF на корпусе sagaai.

Поле с фактами называется `result` (не `facts`) — предыдущая проверка
смотрела не туда и делала ложный вывод «фактов 0».

Здесь проверяются операции, которыми будет пользоваться агент, и печатаются
настоящие пути и строки. Это и есть ответ на вопрос «пригоден ли инструмент».
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
fac = Facade(cfg, tool="selftest", telemetry=False, watch=False)

try:
    print("=" * 78)
    print("ПОИСК ПО КОДУ ПРОЕКТА SAGA AI")
    print("=" * 78)
    for query in ("DevAgent", "ConversationStore", "system_prompt",
                  "orchestrator", "connector"):
        try:
            result = fac.op_search(query, limit=5)
            rows = result.get("result") or []
            print(f"\n  запрос {query!r}: найдено {len(rows)}")
            for f in rows[:3]:
                if not isinstance(f, dict):
                    print(f"    {str(f)[:100]}")
                    continue
                path = f.get("path") or f.get("file") or "?"
                line = f.get("start_line") or f.get("line") or "?"
                kind = f.get("kind") or ""
                name = f.get("name") or ""
                print(f"    {path}:{line} [{kind}] {name}")
        except Exception as exc:
            print(f"\n  запрос {query!r}: ОШИБКА {type(exc).__name__}: {exc}")

    print()
    print("=" * 78)
    print("СИМВОЛЫ: ГДЕ ОПРЕДЕЛЕНО")
    print("=" * 78)
    for name in ("run_monologue", "ConversationStore", "call_llm"):
        try:
            result = fac.op_symbol(name=name)
            rows = result.get("result") or []
            print(f"\n  символ {name!r}: найдено {len(rows)}")
            for f in rows[:3]:
                if isinstance(f, dict):
                    print(f"    {f.get('path')}:{f.get('start_line')} "
                          f"[{f.get('kind')}] fqn={str(f.get('fqn'))[:60]}")
        except Exception as exc:
            print(f"  символ {name!r}: ошибка {type(exc).__name__}: {exc}")

    print()
    print("=" * 78)
    print("КАРТА РЕПОЗИТОРИЯ")
    print("=" * 78)
    try:
        result = fac.op_repo_map()
        rows = result.get("result") or []
        print(f"  записей: {len(rows)}")
        for f in rows[:10]:
            if isinstance(f, dict):
                print(f"    {str(f.get('path'))[:60]:<62} "
                      f"символов {f.get('symbols') or f.get('count') or '?'}")
            else:
                print(f"    {str(f)[:100]}")
    except Exception as exc:
        print(f"  ошибка {type(exc).__name__}: {exc}")

    print()
    print("=" * 78)
    print("СВЯЗИ: КТО ЧТО ВЫЗЫВАЕТ")
    print("=" * 78)
    try:
        result = fac.op_callers("core/orchestrator.py:run")
        rows = result.get("result") or []
        print(f"  callers: найдено {len(rows)}, error={result.get('error')!r}")
        for f in rows[:4]:
            print(f"    {str(f)[:110]}")
    except Exception as exc:
        print(f"  callers: ошибка {type(exc).__name__}: {exc}")

    print()
    print("=" * 78)
    print("ДОКУМЕНТЫ И РОЛИ")
    print("=" * 78)
    try:
        result = fac.op_search("правила агента", limit=5)
        rows = result.get("result") or []
        print(f"  найдено документов: {len(rows)}")
        for f in rows[:5]:
            if isinstance(f, dict):
                role = f.get("doc_role_title") or f.get("doc_role") or "-"
                print(f"    {str(f.get('path'))[:60]:<62} роль={role}")
    except Exception as exc:
        print(f"  ошибка {type(exc).__name__}: {exc}")
finally:
    fac.close()

print()
print("=" * 78)
print("ИТОГ")
print("=" * 78)
print("""
  Если выше есть пути и строки — RAGRAF на корпусе sagaai работает,
  и агент может им пользоваться для поиска по коду проекта.
""")
