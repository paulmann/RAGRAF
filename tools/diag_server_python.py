"""Диагностика серверного пути: почему он не отдаёт вызовы для Python.

Установлено:
  * язык теперь передаётся верно (`language = "PYTHON"`);
  * но серверный путь сообщает «сервер не создал файл результата»;
  * при этом обычный файловый запрос через `query()` к готовому CPG
    отдаёт 72 566 вызовов — то есть данные ЕСТЬ.

Значит, дело в том, как серверный путь забирает результат. Надо понять,
что именно он ждёт и чего не получает.

Проверяется по факту: запускается сервер, строится граф через importCode
и выполняются те же запросы, что делает RAGRAF, с печатью результата.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)

print("=" * 78)
print("1. СОЗДАНИЕ СЕРВЕРА")
print("=" * 78)
try:
    server = runner._ensure_server()
    print(f"  сервер создан: {server is not None}")
    print(f"  порт: {getattr(server, 'port', '?')}")
except Exception as exc:
    print(f"  сервер не создан: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

print()
print("=" * 78)
print("2. ПОСТРОЕНИЕ ГРАФА ЧЕРЕЗ importCode (language=PYTHON)")
print("=" * 78)
root = pathlib.Path(str(cfg.root))
query = (f'val project = importCode(inputPath = "{root.as_posix()}", '
         f'language = "PYTHON")')
print(f"  запрос: {query[:110]}...")
try:
    out = server.query(query, timeout=1800)
    print(f"  ответ ({len(out)} символов):")
    for line in out.splitlines()[:12]:
        print(f"    {line[:120]}")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("3. ЗАПРОС ВЫЗОВОВ ЧЕРЕЗ СЕРВЕР")
print("=" * 78)
# Тот же запрос, что использует RAGRAF для вызовов.
try:
    out = server.query(cpg_mod.QUERY_CALLS, timeout=900)
    lines = out.strip().splitlines()
    print(f"  всего строк в ответе: {len(lines)}")
    for line in lines[:8]:
        print(f"    {line[:120]}")
    if len(lines) > 8:
        print(f"    ... и ещё {len(lines) - 8} строк")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("4. ПРОСТОЙ ЗАПРОС: ЧИСЛО ВЫЗОВОВ")
print("=" * 78)
for probe in ("cpg.call.size", "cpg.method.size",
              "cpg.call.filter(_.name == \"print\").size"):
    try:
        out = server.query(probe, timeout=300)
        answer = [ln.strip() for ln in out.splitlines() if ln.strip()]
        print(f"  {probe:<46} → {answer[-1][:60] if answer else '(пусто)'}")
    except Exception as exc:
        print(f"  {probe:<46} ошибка {type(exc).__name__}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает, отвечает ли сервер на запросы по Python-графу.

  Если простые запросы (cpg.call.size) отвечают числом — сервер работает,
  и неполадка в способе получения вызовов RAGRAF (например, он ждёт файл,
  который сервер не создаёт по своей природе).

  Если сервер отвечает пусто или ошибается — серверный путь для Python
  неприменим, и остаётся файловый: построить CPG, а вызовы забрать
  запросами к файлу (этот путь проверен и дал 72 566 вызовов).
""")
