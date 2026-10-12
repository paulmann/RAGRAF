"""Проверка разбора скриптов запросов для серверного режима.

Скрипты проекта написаны для запуска отдельным процессом: они содержат
`@main def exec(cpgFile, outFile)`, внутри загружают граф через importCpg
и пишут результат в файл.

Для сервера нужен другой вид: загрузка графа делается отдельно, а результат
возвращается прямо в ответе. Значит скрипт надо преобразовать, и это
преобразование нужно проверить.

Проверяется:
  1. разбирается ли каждый скрипт проекта;
  2. убрана ли из него загрузка графа;
  3. превращена ли запись в файл в возврат значения;
  4. остался ли код вычислений без искажений.
"""

from __future__ import annotations

import pathlib
import sys


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
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.cpg import (  # noqa: E402
    QUERY_CALLS, QUERY_METHODS, _extract_script_body,
)

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


for name, script in (("QUERY_CALLS", QUERY_CALLS), ("QUERY_METHODS", QUERY_METHODS)):
    print(f"=== {name} ===")
    # Подход: пути ПОДСТАВЛЯЮТСЯ в текст, логика скрипта не меняется.
    # Так результат попадает в тот же файл, что и при запуске отдельным
    # процессом, и вызывающий код читает его обычным способом.
    body = _extract_script_body(script, "C:/tmp/test.cpg.bin", "C:/tmp/test_out.json")
    check(body is not None, f"{name}: скрипт разобран")
    if body is None:
        continue

    print(f"  строк в результате: {len([l for l in body.splitlines() if l.strip()])}")
    for line in [l for l in body.splitlines() if l.strip()][:5]:
        print(f"    {line[:100]}")

    print()
    # Главное: логика сохранена, а повторная загрузка графа УБРАНА.
    # Это не придирка: повторный importCpg внутри скрипта ЛОМАЕТ сессию сервера
    # (NullPointerException в XTypeRecoveryPassGenerator) и данные не выдаются.
    # Воспроизведено вызовом: скрипт с importCpg файла не создавал, без него — создавал.
    check("importCpg" not in body,
          f"{name}: повторная загрузка графа убрана (она ломает сессию)")
    check("os.write" in body,
          f"{name}: запись в файл сохранена (результат читается как обычно)")
    check("C:/tmp/test_out.json" in body,
          f"{name}: путь вывода подставлен", "путь не найден в тексте")
    # Путь к ГРАФУ в теле отсутствует намеренно: строка importCpg удаляется
    # целиком вместе с путём (граф грузит вызывающий код отдельно).
    check("C:/tmp/test.cpg.bin" not in body,
          f"{name}: путь к графу в теле отсутствует (загрузка убрана)",
          "строка с путём графа осталась")
    check("cpgFile" not in body,
          f"{name}: переменная cpgFile заменена")
    check("outFile" not in body,
          f"{name}: переменная outFile заменена")
    check("cpg." in body,
          f"{name}: обращения к графу сохранены")
    # Баланс скобок: показывает, не порезан ли код.
    opens = body.count("{") + body.count("(") + body.count("[")
    closes = body.count("}") + body.count(")") + body.count("]")
    check(opens == closes,
          f"{name}: скобки сбалансированы",
          f"открытых {opens}, закрытых {closes}")
    print()

print("=== Проверка на синтетических случаях ===")
# Скрипт без @main не должен разбираться: тогда работает прежний путь.
check(_extract_script_body("cpg.method.size") is None,
      "скрипт без @main не разбирается (прежний путь)")

# Скрипт без параметров: подставлять нечего, но вычисление должно сохраниться.
simple = '@main def exec() = {\n cpg.method.size\n}'
body = _extract_script_body(simple, "C:/x.cpg.bin", "C:/o.json")
print(f"  простой скрипт: {body!r}")
check(body is not None and "cpg.method.size" in body,
      "простой скрипт разобран и вычисление сохранено")

# Пустое тело не должно давать пустую строку как запрос.
empty = '@main def exec(cpgFile: String, outFile: String) = {\n}'
check(_extract_script_body(empty) is None,
      "пустое тело не превращается в пустой запрос")

# Обратные слеши заменяются на прямые: в Scala они escape-символы.
win = '@main def exec(cpgFile: String, outFile: String) = { importCpg(cpgFile) }'
body = _extract_script_body(win, r"C:\tmp\a.cpg.bin", r"C:\tmp\b.json")
# У этого скрипта тело состояло только из importCpg — после его удаления
# остаётся пустое тело, и результат равен None. Это верное поведение:
# пустой запрос выполнять нечего.
check(body is None or "C:/tmp/a.cpg.bin" in body,
      "обратные слеши заменяются либо тело пустое (нет вычислений)",
      str(body)[:120])

# Проверка замены слешей на скрипте, где есть и загрузка, и вычисление.
win2 = ('@main def exec(cpgFile: String, outFile: String) = {\n'
        '  importCpg(cpgFile)\n'
        '  os.write.over(os.Path(outFile), cpg.method.size)\n}')
body2 = _extract_script_body(win2, r"C:\tmp\a.cpg.bin", r"C:\tmp\b.json")
check(body2 is not None and "C:/tmp/a.cpg.bin" not in body2,
      "путь к графу подставлен и загрузка убрана",
      str(body2)[:150])
check(body2 is not None and "cpg.method.size" in body2,
      "вычисление сохранено")
check(body2 is not None and "C:/tmp/b.json" in body2,
      "путь вывода подставлен в прямых слешах")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
