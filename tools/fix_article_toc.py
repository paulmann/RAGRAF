"""Обновление оглавления статьи: раздел 11 переименован.

Было «Шестнадцать дефектов», стало «Двадцать один дефект» — в статье
добавлены пять дефектов, найденных при доработке механизма.

Правка идемпотентна, кодировка и переводы строк сохраняются.
"""

from __future__ import annotations

import pathlib

path = pathlib.Path(__file__).resolve().parents[1] / "docs" / "05_article" / "RAGRAF_ARTICLE.md"
raw = path.read_bytes()
had_bom = raw.startswith(b"\xef\xbb\xbf")
crlf = b"\r\n" in raw
text = raw.decode("utf-8-sig").replace("\r\n", "\n")

old = "11. [Шестнадцать дефектов](#11-шестнадцать-дефектов)"
new = "11. [Двадцать один дефект](#11-двадцать-один-дефект)"

if old in text:
    text = text.replace(old, new, 1)
    payload = text.encode("utf-8")
    if had_bom:
        payload = b"\xef\xbb\xbf" + payload
    if crlf:
        payload = payload.replace(b"\n", b"\r\n")
    path.write_bytes(payload)
    print("оглавление обновлено: раздел 11")
elif new in text:
    print("оглавление уже обновлено")
else:
    print("строка оглавления не найдена")
    raise SystemExit(2)

# Проверка: ссылка ведёт к существующему заголовку.
body = path.read_text(encoding="utf-8-sig")
import re

heads = re.findall(r"^## 11\. (.+)$", body, re.MULTILINE)
print(f"фактический заголовок раздела 11: {heads}")
