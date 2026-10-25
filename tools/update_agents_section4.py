"""Дополнить раздел 4 AGENTS.md ссылкой на обязательное использование RAGRAF.

Смысл: раздел 4 объясняет, КАК правила попадают в контекст агента. Теперь
правила включают обязательное использование RAGRAF (раздел 8), и это должно
быть видно уже в разделе 4 — иначе агент прочитает «где включаются правила»
и не поймёт, что индекс обязателен.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
from datetime import datetime

AGENTS = pathlib.Path.home() / ".pi" / "agent" / "AGENTS.md"

# Строка раздела 4 ищется по началу, без опоры на конец строки: файл хранит
# переводы строк CRLF, и сравнение с "\n" не срабатывает.
OLD_START = "выбор `pwsh`/`powershell` жёстко зашит в рантайм, а `AGENTS.md` задаёт требования к коду."

ADDITION = (
    "\n\n"
    "Тот же механизм делает обязательным **раздел 8** (использование RAGRAF): он\n"
    "подхватывается для любого проекта, включая те, где своего `AGENTS.md` нет.\n"
    "Если проектный `AGENTS.md` задаёт более строгие требования к работе с кодом,\n"
    "действует он; если он молчит про индекс — действует раздел 8."
)

if not AGENTS.exists():
    print(f"нет файла: {AGENTS}")
    sys.exit(1)

raw = AGENTS.read_bytes()
had_bom = raw.startswith(b"\xef\xbb\xbf")
had_crlf = b"\r\n" in raw
text = raw.decode("utf-8-sig")

if "раздел 8** (использование RAGRAF)" in text:
    print("ссылка на раздел 8 уже присутствует — изменений не требуется")
    sys.exit(0)

if OLD_START not in text:
    print("не найден фрагмент раздела 4 для замены")
    sys.exit(1)

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup = AGENTS.with_name(f"AGENTS.md.bak.{stamp}-section4-ragraf")
shutil.copy2(AGENTS, backup)
print(f"копия: {backup.name}")

# Вставка идёт после строки целиком: сама строка не заменяется, поэтому
# её перевод строки сохраняется как был.
idx = text.index(OLD_START) + len(OLD_START)
new_text = text[:idx] + ADDITION + text[idx:]
out = new_text
if had_crlf:
    out = out.replace("\r\n", "\n").replace("\n", "\r\n")
data = out.encode("utf-8")
if had_bom:
    data = b"\xef\xbb\xbf" + data
AGENTS.write_bytes(data)
print(f"готово; строк было {len(text.splitlines())}, стало {len(out.splitlines())}")
