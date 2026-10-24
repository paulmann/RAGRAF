"""Дополнение таблицы «вопрос → операция» в разделе 8.3 системного промпта.

Причина отдельного скрипта: предыдущая попытка не нашла таблицу, потому что
файл хранится с переводами строк CRLF, а шаблон поиска был записан с LF.
Здесь поиск идёт по нормализованному тексту в памяти, а запись выполняется
в исходной кодировке и с исходными переводами строк.

Идемпотентность: если строки про policy_trace уже есть, скрипт ничего не меняет.
"""

from __future__ import annotations

import pathlib
import shutil
from datetime import datetime

path = pathlib.Path.home() / ".pi" / "agent" / "AGENTS.md"
raw = path.read_bytes()
had_bom = raw.startswith(b"\xef\xbb\xbf")
crlf = b"\r\n" in raw

# Нормализация в LF для поиска: правится только то, что найдено в LF-виде.
text = raw.decode("utf-8-sig").replace("\r\n", "\n")

if "policy_trace" in text and "Где идёт запись" in text:
    print("таблица уже дополнена — изменений не требуется")
    raise SystemExit(0)

old = """   | «Что в таблице и кто её использует» | `schema`, `table_usage` |
   | «Можно ли так записать» | **`write_check`** |"""
new = """   | «Что в таблице и кто её использует» | `schema`, `table_usage` |
   | «Где идёт запись в таблицу» | `table_usage` с `--mode write` |
   | «Кто и по какому условию получает право» | **`policy_trace`** |
   | «Можно ли так записать» | **`write_check`** |"""

if old not in text:
    # Печатается фактический фрагмент таблицы: по нему видно, что искать.
    marker = "Можно ли так записать"
    idx = text.find(marker)
    print("шаблон не найден. Фактический фрагмент вокруг искомой строки:")
    print(repr(text[max(0, idx - 260):idx + 60]) if idx > 0 else "строка не найдена вовсе")
    raise SystemExit(2)

text = text.replace(old, new, 1)

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup = path.with_name(f"{path.name}.bak.{stamp}-table")
shutil.copy2(path, backup)
print(f"резервная копия: {backup.name}")

payload = text.encode("utf-8")
if had_bom:
    payload = b"\xef\xbb\xbf" + payload
if crlf:
    payload = payload.replace(b"\n", b"\r\n")
path.write_bytes(payload)

check = path.read_bytes()
print(f"записано: {len(check)} байт, BOM={check.startswith(b'\xef\xbb\xbf')}, "
      f"CRLF={b'\r\n' in check}")
print(f"строк: {len(path.read_text(encoding='utf-8-sig').splitlines())}")
print("\nстраница таблицы после правки:")
body = path.read_text(encoding="utf-8-sig")
i = body.find("Что в таблице")
print(body[i - 20:i + 400])
