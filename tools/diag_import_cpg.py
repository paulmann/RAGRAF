"""Обход: получить вызовы из готового CPG без importCode.

Причина установлена точно:

    ConsoleException: Error creating project for input path
    Caused by: AssertionError: CPG generator does not exist at:
        .tools\\joern-cli...\\pysrc2cpg

Серверный путь RAGRAF разбирает исходники заново (`importCode`), а для этого
ищет исполняемый генератор `pysrc2cpg`. У установленного Joern он есть только
в виде `.bat`-обёртки, которую сервер не признаёт, поэтому построение падает.

НО: файл `.cpg.bin` для Python уже СОЗДАН обычным путём (`joern-parse`), и
запросы к нему через `query()` работают — 72 566 вызовов. Значит, серверу
надо не строить граф заново, а ЗАГРУЗИТЬ готовый файл: команда `importCpg`.

Проверяется: работает ли `importCpg` на готовом Python-файле и отдаёт ли
он те же вызовы. Если да — это и есть решение для Python-корпусов.

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

# Берётся готовый файл CPG: он создаётся обычным путём при индексации.
files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
if not files:
    print("  готового CPG нет — сначала нужна индексация обычным путём")
    raise SystemExit(1)
cpg_path = files[0]
print(f"  готовый CPG: {cpg_path.name} "
      f"({cpg_path.stat().st_size / 1024 / 1024:.1f} МБ)")

print()
print("=" * 78)
print("1. ЗАГРУЗКА ГОТОВОГО ГРАФА В СЕРВЕР (importCpg)")
print("=" * 78)
try:
    server = runner._ensure_server()
except Exception as exc:
    print(f"  сервер не создан: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

# Путь передаётся в прямых слешах: обратные в CPGQL экранируются.
posix = cpg_path.as_posix()
query = f'importCpg("{posix}")'
print(f"  запрос: {query}")
try:
    out = server.query(query, timeout=1800)
    print(f"  ответ ({len(out)} символов):")
    for line in out.splitlines()[:10]:
        print(f"    {line[:120]}")
    loaded = "Error" not in out and "Exception" not in out
    print(f"  граф загружен: {'ДА' if loaded else 'НЕТ'}")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    loaded = False

print()
print("=" * 78)
print("2. ЗАПРОСЫ К ЗАГРУЖЕННОМУ ГРАФУ")
print("=" * 78)
if loaded:
    for label, probe in (
        ("вызовов всего", "cpg.call.size"),
        ("методов всего", "cpg.method.size"),
        ("вызовов с именем", 'println(cpg.call.map(c => c.name).take(5).l.mkString(", "))'),
    ):
        try:
            out = server.query(probe, timeout=600)
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            answer = lines[-1][:80] if lines else "(пусто)"
            print(f"  {label:<20} → {answer}")
        except Exception as exc:
            print(f"  {label:<20} ошибка {type(exc).__name__}")

    # Главная проверка: запрос вызовов тем же текстом, что использует RAGRAF.
    print()
    print("  запрос вызовов (QUERY_CALLS, как в RAGRAF):")
    try:
        out = server.query(cpg_mod.QUERY_CALLS, timeout=900)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        print(f"    строк в ответе: {len(lines)}")
        for line in lines[:5]:
            print(f"    {line[:110]}")
    except Exception as exc:
        print(f"    ошибка: {type(exc).__name__}: {exc}")
else:
    print("  граф не загружен — запросы невозможны")

runner.stop_server()
print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если importCpg загружает готовый Python-граф и запросы отвечают числами,
  решение для Python-корпусов такое:
    1. строить CPG обычным путём (joern-parse + pysrc2cpg) — это работает;
    2. серверу НЕ поручать построение, а загружать готовый файл;
    3. вызовы брать запросами к загруженному графу.

  Тогда экспорт (который падает на Python из-за неразрешённых MethodRef)
  вообще не нужен, и граф вызовов по Python становится доступен.
""")
