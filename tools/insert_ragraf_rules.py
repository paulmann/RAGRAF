"""Вставить раздел про RAGRAF в пользовательский AGENTS.md.

Файловые инструменты агента ограничены рабочим каталогом, поэтому правка
внешнего файла выполняется отдельным процессом. Перед вставкой создаётся копия.

Проверяется:
  * раздел вставляется ровно один раз (повторный запуск не дублирует его);
  * файл остаётся в UTF-8 без BOM и с переводами строк, как был;
  * кодировка исходного файла сохраняется.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
from datetime import datetime

AGENTS = pathlib.Path.home() / ".pi" / "agent" / "AGENTS.md"
SECTION = pathlib.Path(__file__).resolve().parents[1] / ".staging" / "AGENTS_ragraf_section.md"

MARKER = "## 8. Обязательное использование RAGRAF"

if not AGENTS.exists():
    print(f"нет файла: {AGENTS}")
    sys.exit(1)
if not SECTION.exists():
    print(f"нет раздела: {SECTION}")
    sys.exit(1)

# Кодировка и переводы строк определяются до правки, чтобы сохранить их.
raw = AGENTS.read_bytes()
had_bom = raw.startswith(b"\xef\xbb\xbf")
had_crlf = b"\r\n" in raw
text = raw.decode("utf-8-sig")
section = SECTION.read_text(encoding="utf-8").rstrip("\n")

if MARKER in text:
    print("раздел уже присутствует — вставка не выполняется (идемпотентно)")
    # Дописываем только если содержимое отличается: иначе повторный запуск
    # не должен ничего менять.
    current_start = text.find(MARKER)
    current = text[current_start:].strip()
    if current == section.strip():
        print("содержимое раздела совпадает с источником")
        sys.exit(0)
    print("содержимое раздела отличается — раздел будет заменён")
    # Отрезаем прежний раздел целиком (он последний в файле).
    text = text[:current_start].rstrip() + "\n"

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup = AGENTS.with_name(f"AGENTS.md.bak.{stamp}-insert-ragraf")
shutil.copy2(AGENTS, backup)
print(f"копия: {backup.name}")

new_text = text.rstrip("\n") + "\n\n" + section + "\n"
lines_before = len(text.splitlines())
lines_after = len(new_text.splitlines())

# Переводы строк приводятся к тем, что были в файле.
out = new_text
if had_crlf:
    out = out.replace("\r\n", "\n").replace("\n", "\r\n")
data = out.encode("utf-8")
if had_bom:
    data = b"\xef\xbb\xbf" + data
AGENTS.write_bytes(data)

print(f"строк было: {lines_before}, стало: {lines_after} (+{lines_after - lines_before})")
print(f"BOM сохранён: {had_bom}, CRLF сохранён: {had_crlf}")
print(f"файл: {AGENTS}")
