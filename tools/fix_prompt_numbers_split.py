"""Уточнение строки с числами: не смешивать каталоги.

В разделе 8.14 была строка:

    Замер по двум каталогам (`<полигон>` и `1st_RdpMON\\Service`) дал такие
    числа файлов: Perl 1235, Python 1337, C# 1217, shell 212, PowerShell 96,
    TypeScript 146, C/C++ 384.

Числа взяты из двух разных сканирований, и в одной строке они читаются как
принадлежащие одному каталогу. Проверка по факту:
    <полигон>:  .pl 1235, .py 1337, .sh 212, .ps1 86+, .ts 146
    1st_RdpMON\\Service:  .cs 1217 (при том наборе исключений)

Это разные места, и агент, который начнёт искать 1235 файлов Perl в каталоге
Service, их не найдёт. Строка переписывается так, чтобы источник каждого
числа был назван.
"""

from __future__ import annotations

import pathlib

PROMPT = pathlib.Path.home() / ".pi/agent/AGENTS.md"

OLD = """Замер по двум каталогам (`<полигон>` и `1st_RdpMON\\Service`) дал такие
числа файлов: Perl 1235, Python 1337, C# 1217, shell 212, PowerShell 96,
TypeScript 146, C/C++ 384. Все они не попадали в индекс, потому что их
расширений не было в карте языков (`src/ragraf/config.py`, `LANG_BY_EXT`),
а разбор деревом шёл только для трёх языков (`indexer.py`)."""

NEW = """Замер по двум каталогам дал такие числа файлов (каждое число — из своего
каталога, смешивать их нельзя):

| каталог | язык | файлов |
|---|---|---|
| `<полигон>` | Python | 1337 |
| `<полигон>` | Perl | 1235 |
| `<полигон>` | C/C++ (заголовки и исходники) | 384 |
| `<полигон>` | shell | 212 |
| `<полигон>` | TypeScript | 146 |
| `<полигон>` | PowerShell | 86 |
| `1st_RdpMON\\Service` | C# | 1217 |
| `1st_RdpMON\\Service` | PowerShell | 8 |

Все они не попадали в индекс, потому что их расширений не было в карте
языков (`src/ragraf/config.py`, `LANG_BY_EXT`), а разбор деревом шёл только
для трёх языков (`indexer.py`)."""

text = PROMPT.read_text(encoding="utf-8")
if OLD not in text:
    if "| каталог | язык | файлов |" in text:
        print("уже уточнено ранее — правка не нужна")
    else:
        raise SystemExit("НЕ НАЙДЕН фрагмент для замены")
else:
    text = text.replace(OLD, NEW, 1)
    PROMPT.write_text(text, encoding="utf-8", newline="\n")
    print("строка с числами разделена по каталогам")

after = PROMPT.read_text(encoding="utf-8")
print(f"  размер: {len(after.encode('utf-8'))} байт, строк {after.count(chr(10)) + 1}")
print(f"  таблица чисел: {'есть' if '| каталог | язык | файлов |' in after else 'НЕТ'}")
print(f"  оба каталога названы: "
      f"{'да' if '<полигон>' in after and '1st_RdpMON' in after else 'нет'}")
EOF_MARKER = None
