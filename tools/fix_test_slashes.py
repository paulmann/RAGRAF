"""Исправление тестовых данных: лишние слеши в именах.

В тесте имена Joern записаны как `core\\\\api_errors.py` (в исходнике —
четыре обратных слеша, то есть в строке их два), а Joern на самом деле
отдаёт один обратный слеш: `core\\api_errors.py`.

Из-за этого нормализация давала `core//api_errors.py` и проверки падали
на ошибочных данных, а не на ошибочном коде. Здесь данные приводятся
к фактическому виду.

Заодно сохраняется проверка на двойные разделители: нормализация должна
их схлопывать — такое встречается, если путь собран из двух частей.
"""

from __future__ import annotations

import pathlib

TARGET = pathlib.Path(r"C:\AutoClaw\RAGRAF\tests\test_joern_names.py")
text = TARGET.read_text(encoding="utf-8")

# В строке исходника четыре обратных слеша → в значении два.
# Меняется на два слеша в исходнике → один в значении.
before = text.count("\\\\\\\\")
text = text.replace("core\\\\\\\\api_errors.py", "core\\\\api_errors.py")
text = text.replace("ui\\\\\\\\pages\\\\\\\\chat.py", "ui\\\\pages\\\\chat.py")
text = text.replace("dev_agent\\\\\\\\tool_executor.py",
                    "dev_agent\\\\tool_executor.py")
text = text.replace(".\\\\\\\\core\\\\\\\\version.py", ".\\\\core\\\\version.py")
text = text.replace("App\\\\\\\\Core\\\\\\\\Config", "App\\\\Core\\\\Config")
text = text.replace("src\\\\\\\\Db.php", "src\\\\Db.php")
text = text.replace("App\\\\\\\\Shell", "App\\\\Shell")
text = text.replace("App\\\\\\\\Service", "App\\\\Service")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = text.count("\\\\\\\\")
print(f"  вхождений двойных слешей: было {before}, стало {after}")

# Проверка: имена в тесте теперь соответствуют фактическим от Joern.
for probe in ("core\\\\api_errors.py:__init__", "ui\\\\pages\\\\chat.py:render"):
    present = f'"{probe}"' in text
    print(f"  {probe!r}: {'есть' if present else 'НЕТ'}")
