"""Проверка: работает ли RAGRAF на корпусе sagaai на практике.

Нужно ответить на главный вопрос: даёт ли инструмент пользу в этом проекте.
Проверяются операции, которые будет вызывать агент, и смотрится, приходят
ли настоящие факты со ссылками файл:строка.

Это прямая проверка применимости, а не косвенная.
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
    print("СОСТОЯНИЕ")
    print("=" * 78)
    status = fac.op_index_status()
    print(f"  ключи: {sorted(status)}")
    for key in ("rev_id", "current_rev", "capability", "freshness",
                "changed", "files", "nodes", "edges"):
        if key in status:
            print(f"    {key} = {str(status[key])[:80]}")
    cov = status.get("coverage")
    if isinstance(cov, dict):
        print(f"    coverage: {len(cov)} полей")
        for k in ("files_found", "files_indexed", "functions", "classes",
                  "calls", "capability"):
            if k in cov:
                print(f"      {k} = {cov[k]}")

    print()
    print("=" * 78)
    print("ПОИСК ПО КОДУ ПРОЕКТА")
    print("=" * 78)
    for query in ("ConversationStore", "история чатов", "DevAgent",
                  "system_prompt", "connector"):
        try:
            result = fac.op_search(query, limit=5)
            facts = result.get("facts") or []
            print(f"\n  запрос {query!r}: found={result.get('found')}, "
                  f"фактов {len(facts) if isinstance(facts, list) else '?'}")
            if isinstance(facts, list):
                for f in facts[:3]:
                    if isinstance(f, dict):
                        path = f.get("path") or f.get("file") or "?"
                        line = f.get("start_line") or f.get("line") or "?"
                        name = f.get("name") or f.get("title") or ""
                        score = f.get("score")
                        extra = f" score={score:.2f}" if isinstance(
                            score, (int, float)) else ""
                        print(f"      {path}:{line} {name}{extra}")
                    else:
                        print(f"      {str(f)[:100]}")
        except Exception as exc:
            print(f"\n  запрос {query!r}: ОШИБКА {type(exc).__name__}: {exc}")

    print()
    print("=" * 78)
    print("СИМВОЛЫ И СВЯЗИ")
    print("=" * 78)
    try:
        sym = fac.op_symbol(name="run_monologue")
        facts = sym.get("facts") or []
        print(f"  symbol(run_monologue): found={sym.get('found')}, "
              f"фактов {len(facts)}")
        for f in facts[:3]:
            print(f"    {str(f)[:130]}")
    except Exception as exc:
        print(f"  symbol: ошибка {type(exc).__name__}: {exc}")

    # Карта репозитория: показывает, видит ли RAGRAF структуру проекта.
    try:
        rmap = fac.op_repo_map()
        facts = rmap.get("facts") or []
        print(f"\n  repo_map: found={rmap.get('found')}, "
              f"записей {len(facts)}")
        for f in facts[:8]:
            if isinstance(f, dict):
                print(f"    {str(f.get('path'))[:70]} "
                      f"({f.get('symbols') or f.get('count') or ''})")
            else:
                print(f"    {str(f)[:100]}")
    except Exception as exc:
        print(f"  repo_map: ошибка {type(exc).__name__}: {exc}")

    print()
    print("=" * 78)
    print("ДОКУМЕНТЫ И ИХ РОЛИ")
    print("=" * 78)
    try:
        # Проверяется, видит ли RAGRAF документы проекта как документы.
        result = fac.op_search("AGENT.md правила агента", limit=5)
        facts = result.get("facts") or []
        print(f"  поиск по правилам: фактов {len(facts)}")
        for f in facts[:4]:
            if isinstance(f, dict):
                print(f"    {str(f.get('path'))[:60]} "
                      f"роль={f.get('doc_role_title') or f.get('doc_role')}")
    except Exception as exc:
        print(f"  ошибка: {type(exc).__name__}: {exc}")
finally:
    fac.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если поиск возвращает факты с путями и строками — RAGRAF на этом корпусе
  работоспособен, и агент может им пользоваться.
  Если фактов ноль — надо смотреть, что не так с индексацией корпуса.
""")
