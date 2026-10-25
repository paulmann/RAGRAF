"""Дописать стили страницы входа в assets/css/z.css.

Почему отдельным шагом: файл стилей большой (2164 строки), и правка делается
дописыванием в конец — так исключается риск задеть существующие правила.
Перед правкой создаётся копия.

Проверки после записи:
  * все классы шаблона auth.htm теперь имеют правила;
  * в добавленном фрагменте нет литеральных цветов (только токены --z-*);
  * файл остаётся в UTF-8 без BOM и без CRLF.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys
from datetime import datetime

PROJECT = pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru"
CSS = PROJECT / "assets" / "css" / "z.css"
TEMPLATE = PROJECT / "templates" / "auth.htm"
SECTION = pathlib.Path(__file__).resolve().parents[1] / ".staging/auth_styles.css"

MARKER = "Страница входа (templates/auth.htm)"

if not CSS.exists():
    print(f"нет файла: {CSS}")
    sys.exit(1)
if not SECTION.exists():
    print(f"нет фрагмента: {SECTION}")
    sys.exit(1)

css_text = CSS.read_text(encoding="utf-8")
if MARKER in css_text:
    print("стили страницы входа уже добавлены — повторная вставка не выполняется")
else:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = CSS.with_name(f"z.css.bak.{stamp}-auth-styles")
    shutil.copy2(CSS, backup)
    print(f"копия: {backup.name}")

    addition = SECTION.read_text(encoding="utf-8").rstrip("\n")
    new_text = css_text.rstrip("\n") + "\n" + addition + "\n"
    CSS.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"добавлено строк: {len(addition.splitlines())}")
    css_text = new_text

# ---------------- проверки ----------------

# 1. Литеральные цвета в добавленном фрагменте.
section_text = SECTION.read_text(encoding="utf-8")
literal = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", section_text)
print(f"\nлитеральных цветов в добавленном фрагменте: {len(literal)}")
if literal:
    for item in literal[:10]:
        print(f"  {item}")

# 2. Все классы шаблона входа должны иметь правила.
html = TEMPLATE.read_text(encoding="utf-8")
classes = set()
for m in re.finditer(r'class="([^"]+)"', html):
    for cls in m.group(1).split():
        classes.add(cls)

missing = []
for cls in sorted(classes):
    # Правило ищется как ".класс" в начале селектора или после пробела/запятой.
    pattern = re.compile(r"[.,\s]" + re.escape("." + cls) + r"[\s,{:]")
    if not pattern.search(css_text):
        missing.append(cls)

print(f"\nклассов в шаблоне входа: {len(classes)}")
print(f"классов без правил в CSS: {len(missing)}")
if missing:
    print("  ", ", ".join(missing))

# 3. Кодировка и переводы строк.
raw = CSS.read_bytes()
print(f"\nBOM: {raw.startswith(b'\\xef\\xbb\\xbf')}, CRLF: {b'\\r\\n' in raw}")
print(f"строк в z.css: {len(css_text.splitlines())}")
