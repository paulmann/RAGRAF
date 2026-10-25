"""Проверка: почему Joern для Python помечает ВСЕ вызовы владельцем <module>.

Установлено: все 172 184 записи имеют владельца `<module>`, и ни одна —
конкретную функцию. Это подозрительно: значит либо запрос забирает не то,
либо Python-граф действительно не связывает вызовы с функциями.

Надо проверить независимо от нашего запроса: посмотреть сами методы
в графе и их вызовы напрямую. Возможные объяснения:
  1. запрос QUERY_CALLS написан под PHP и для Python собирает не то;
  2. вызовы действительно лежат внутри `<module>`, а функции оформлены
     иначе (например, как отдельные узлы, но связь не проставлена).

Проверяется запросами напрямую к графу.
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
files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
rev_id = files[0].stem.replace(".cpg", "")
print(f"  ревизия: {rev_id}")

print()
print("=" * 78)
print("1. ЗАПРОС RAGRAF: ЧТО ОН ИЗВЛЕКАЕТ")
print("=" * 78)
print("  текст QUERY_CALLS:")
for line in cpg_mod.QUERY_CALLS.splitlines()[:20]:
    print(f"    {line}")

print()
print("=" * 78)
print("2. ПРЯМЫЕ ЗАПРОСЫ К ГРАФУ: ЕСТЬ ЛИ ВЫЗОВЫ ВНУТРИ ФУНКЦИЙ")
print("=" * 78)

QUERIES = [
    ("методов всего", "println(cpg.method.size)"),
    ("вызовов всего", "println(cpg.call.size)"),
    ("методов-модулей", 'println(cpg.method.name("<module>").size)'),
    ("методов-функций (не модуль)",
     'println(cpg.method.filterNot(_.name == "<module>").size)'),
    ("имён методов (примеры)",
     'println(cpg.method.filterNot(_.name == "<module>").name.take(10).l.mkString(", "))'),
    ("вызовов внутри функций (не модуль)",
     'println(cpg.call.filter(_.method.name != "<module>").size)'),
    ("файл первого вызова внутри функции",
     'println(cpg.call.filter(_.method.name != "<module>").map(c => c.method.name + " @ " + c.file.name).take(5).l.mkString(" | "))'),
    ("пример вызова из функции",
     'println(cpg.call.filter(_.method.name != "<module>").map(c => c.name + " -> " + c.method.fullName).take(5).l.mkString(" | "))'),
]

for label, query_text in QUERIES:
    try:
        ok, note, rows = runner.query_file_script(rev_id, query_text,
                                                  timeout=900) \
            if hasattr(runner, "query_file_script") else (False, "нет метода", None)
        if not ok:
            # Используется штатный путь с обёрткой @main.
            ok, note, rows = runner.query(rev_id, query_text, timeout=900)
        answer = ""
        if isinstance(rows, list) and rows:
            answer = str(rows[0])[:130]
        elif note:
            answer = str(note)[:130]
        print(f"  {label:<40} {answer}")
    except Exception as exc:
        print(f"  {label:<40} ошибка {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Запросы показывают:
    * сколько методов-функций есть в графе (кроме <module>);
    * есть ли вызовы ВНУТРИ этих функций.

  Если вызовы внутри функций есть, а QUERY_CALLS их не отдаёт — дефект
  в нашем запросе: он написан под PHP и не различает методы в Python.

  Если вызовов внутри функций нет вовсе — Python-фронтенд Joern не
  проставил связи, и тогда графические связи для Python недоступны,
  а RAGRAF работает по разбору деревом.
""")
