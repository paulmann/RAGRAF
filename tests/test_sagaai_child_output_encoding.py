"""Проверка: вывод дочернего процесса читается независимо от локали.

Какие дефекты здесь закрыты

1. `run_code` / `run_test` вызывали `subprocess.run(..., text=True)` без
   указания кодировки. Дочерний вывод декодировался предпочтительной
   кодировкой РОДИТЕЛЯ. На Windows для piped-процесса это ANSI-кодовая
   страница (cp1252 на этой машине), тогда как сам ребёнок мог писать
   UTF-8 или cp1251. При несовпадении падал поток чтения, `proc.stdout`
   оставался None, и следующая строка `proc.stdout[-4000:]` давала ровно:

       TypeError: 'NoneType' object is not subscriptable

   Это сообщение уходило пользователю через результат инструмента и
   читалось как поломка инструмента, хотя причина — кодировка.

2. Функция RAGRAF для SagaAI бросала исключение при нечисловом значении
   числового параметра, нарушая объявленный контракт «при любой ошибке —
   {"ok": false, ...}».

Запуск: интерпретатор окружения SagaAI
(`<проект>\\.venv\\Scripts\\python.exe`) — нужны зависимости проекта.
Тест не пишет в рабочие журналы и не трогает индекс RAGRAF.
"""

from __future__ import annotations

import os
import pathlib
import sys

PROJECT = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)
os.environ.setdefault("SAGAAI_DATA_DIR", str(PROJECT))

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


from dev_agent.tool_executor import ToolExecutor  # noqa: E402

executor = ToolExecutor()

# Код ребёнка, печатающий не-ASCII текст. Именно такой вывод давал
# 'NoneType' object is not subscriptable до правки.
UTF8_TEXT = "print('\u041f\u0440\u0438\u0432\u0435\u0442 \u2014 \u0442\u0435\u043a\u0441\u0442')"
CP1251_TEXT = ("import sys\n"
               "sys.stdout.reconfigure(encoding='cp1251')\n"
               "print('\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440')")
CP1252_TEXT = ("import sys\n"
               "sys.stdout.reconfigure(encoding='cp1252')\n"
               "print('\u041f\u0440\u0438\u0432\u0435\u0442 \u2014 \u0442\u0435\u043a\u0441\u0442')")

print("=" * 78)
print("1. НЕ-ASCII ВЫВОД РЕБЁНКА НЕ РОЖДАЕТ 'NoneType' object is not subscriptable")
print("=" * 78)
print("  Проверяется тот самый путь: результат инструмента разбирается")
print("  целиком, поэтому исключение внутри видно здесь же.")
print()

for label, code in (("utf-8 текст", UTF8_TEXT),
                    ("cp1251 текст", CP1251_TEXT),
                    ("cp1252 текст", CP1252_TEXT)):
    try:
        res = executor.run_code(code=code, confirmed_by_user=True)
        raised = None
    except Exception as exc:                      # noqa: BLE001 - это и проверяется
        res, raised = None, f"{type(exc).__name__}: {exc}"
    check(raised is None, f"{label}: исключения наружу нет", raised or "")
    check(res is not None and "NoneType" not in str(res),
          f"{label}: результат не содержит 'NoneType'",
          str(res)[:80] if res else "")
    check(isinstance(res, dict) and "stdout" in res,
          f"{label}: результат — словарь с полем stdout",
          str(type(res)))

print()
print("=" * 78)
print("2. РУССКИЙ ТЕКСТ ЧИТАЕТСЯ, А НЕ ПРЕВРАЩАЕТСЯ В МУСОР")
print("=" * 78)

res = executor.run_code(code=UTF8_TEXT, confirmed_by_user=True)
check(res.get("ok") is True, "utf-8 текст: запуск успешен")
check("Привет" in (res.get("stdout") or ""),
      "utf-8 текст читается без потерь", repr(res.get("stdout")))

# cp1251 — родная однобайтовая кодировка для русских файлов на этой машине.
# Декодирование utf-8 её портит, поэтому вывод читается байтами и
# разбирается цепочкой проекта (core.fs.decode_bytes).
res = executor.run_code(code=CP1251_TEXT, confirmed_by_user=True)
check(res.get("ok") is True, "cp1251 текст: запуск успешен")
check("Привет мир" in (res.get("stdout") or ""),
      "cp1251 текст читается как текст, а не как мусор",
      repr(res.get("stdout")))

print()
print("=" * 78)
print("3. ОБЫЧНЫЕ ИСХОДЫ СОХРАНЕНЫ: ОШИБКА, ПУСТОЙ ВЫВОД, ДЛИННЫЙ ВЫВОД")
print("=" * 78)

res = executor.run_code(code="raise ValueError('boom')", confirmed_by_user=True)
check(res.get("ok") is False, "ошибка ребёнка отдаётся ok=false")
check("ValueError" in (res.get("stderr") or "") or "boom" in (res.get("stderr") or ""),
      "причина ошибки видна в stderr", repr((res.get("stderr") or "")[-60:]))

res = executor.run_code(code="pass", confirmed_by_user=True)
check(res.get("ok") is True, "пустой вывод — нормальный успех")
check(res.get("stdout") == "", "пустой вывод не превращается в ошибку")

res = executor.run_code(code="print('x' * 9000)", confirmed_by_user=True)
length = len(res.get("stdout") or "")
check(res.get("ok") is True, "длинный вывод: запуск успешен")
check(length == 4000, "длинный вывод обрезается до хвоста в 4000 символов",
      f"длина {length}")

print()
print("=" * 78)
print("4. ПУТЬ-РЕЖИМ run_test / run_code ТОЖЕ ЗАЩИЩЁН")
print("=" * 78)

# Обе функции ходят через один помощник, поэтому проверяется и второй путь.
target = PROJECT / ".zwork_run_child_encoding_probe.py"
target.write_text("print('\u041f\u0440\u0438\u0432\u0435\u0442 \u0438\u0437 \u0444\u0430\u0439\u043b\u0430')\n",
                  encoding="utf-8", newline="\n")
try:
    res = executor.run_code(path=".zwork_run_child_encoding_probe.py",
                            confirmed_by_user=True)
    check(res.get("ok") is True, "run_code в режиме path: запуск успешен",
          str(res)[:100])
    check("Привет из файла" in (res.get("stdout") or ""),
          "run_code в режиме path читает русский текст",
          repr(res.get("stdout")))
finally:
    target.unlink(missing_ok=True)

print()
print("=" * 78)
print("5. ФУНКЦИЯ RAGRAF ВОЗВРАЩАЕТ ok:FALSE ВМЕСТО ИСКЛЮЧЕНИЯ")
print("=" * 78)

import importlib.util  # noqa: E402

fn_path = PROJECT / "orchestrators" / "dev_agent" / "functions" / "ragraf.py"
spec = importlib.util.spec_from_file_location("sagaai_ragraf_fn", fn_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

try:
    res = module.invoke(operation="search", text="tool", limit="abc")
    raised = None
except Exception as exc:                          # noqa: BLE001 - это и проверяется
    res, raised = None, f"{type(exc).__name__}: {exc}"

check(raised is None, "нечисловой limit не бросает исключение наружу", raised or "")
check(isinstance(res, dict) and res.get("ok") is False,
      "нечисловой limit отдаётся как ok:false", str(res)[:80])
check("limit" in str(res.get("error") or ""),
      "в ответе назван виновный параметр", repr(res.get("error")))

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
