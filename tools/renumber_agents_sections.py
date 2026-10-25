"""Приведение разделов 8.x системного промпта в правильный порядок.

Проблема: разделы добавлялись вставками в разные моменты, и порядок сбился:
  8.7 стоит перед 8.6, а 8.10 — перед 8.9.

Что делает скрипт: перенумеровывает заголовки `### 8.N` по ФАКТИЧЕСКОМУ порядку
их следования в файле, а не переставляет блоки. Это безопаснее: текст разделов
не перемещается, меняются только номера в заголовках, и нумерация становится
непрерывной.

Правка идемпотентна: повторный запуск ничего не меняет.
"""

from __future__ import annotations

import pathlib
import re
import shutil
from datetime import datetime

path = pathlib.Path.home() / ".pi" / "agent" / "AGENTS.md"
raw = path.read_bytes()
had_bom = raw.startswith(b"\xef\xbb\xbf")
crlf = b"\r\n" in raw
text = raw.decode("utf-8-sig").replace("\r\n", "\n")

# Собираются все заголовки разделов 8.x в порядке следования.
pattern = re.compile(r"^### 8\.(\d+) (.+)$", re.MULTILINE)
matches = list(pattern.finditer(text))
print(f"разделов 8.x найдено: {len(matches)}")
for m in matches:
    print(f"  строка {text[:m.start()].count(chr(10)) + 1}: 8.{m.group(1)} {m.group(2)}")

# Проверка: нужна ли правка вообще.
numbers = [int(m.group(1)) for m in matches]
if numbers == list(range(1, len(numbers) + 1)):
    print("\nпорядок уже верный — изменений не требуется")
    raise SystemExit(0)

print(f"\nфактическая нумерация: {numbers}")
print(f"требуемая:             {list(range(1, len(numbers) + 1))}")

# Замена идёт с конца, чтобы позиции предыдущих совпадений не сдвигались.
for index, m in enumerate(reversed(matches), start=1):
    target = len(matches) - index + 1
    old = m.group(0)
    new = f"### 8.{target} {m.group(2)}"
    if old != new:
        text = text[: m.start()] + new + text[m.end():]

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup = path.with_name(f"{path.name}.bak.{stamp}-renumber")
shutil.copy2(path, backup)
print(f"\nрезервная копия: {backup.name}")

payload = text.encode("utf-8")
if had_bom:
    payload = b"\xef\xbb\xbf" + payload
if crlf:
    payload = payload.replace(b"\n", b"\r\n")
path.write_bytes(payload)

body = path.read_text(encoding="utf-8-sig")
print("\n=== после правки ===")
for m in re.finditer(r"^### 8\.(\d+) (.+)$", body, re.MULTILINE):
    print(f"  8.{m.group(1)} {m.group(2)}")
print(f"\nстрок: {len(body.splitlines())}")
