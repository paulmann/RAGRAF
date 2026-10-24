"""Включение схлопывания дублей в конфиге корпуса revoap.

Почему именно revoap: в нём единственном среди подключённых корпусов есть
дубли (107 байт-идентичных копий, замерено). В z, MD.Viewer и RdpMON их нет,
и включать там нечего — проверять нечего тоже.

Что делает скрипт: дописывает в секцию `index` конфига настройки
`dedupe_enabled: true` и остальные значения. Резервная копия делается
перед правкой: конфиг — рабочий файл, и откат должен быть возможен.

Скрипт идемпотентен: повторный запуск не добавляет ключи второй раз.
"""

from __future__ import annotations

import pathlib
import shutil

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "config" / "revoap.yaml"
BACKUP = REPO / ".staging" / "revoap.yaml.before-dedupe"

# Настройки схлопывания с пояснениями для человека, который откроет конфиг.
BLOCK = """  # --- схлопывание байт-идентичных копий ---
  # Замер этого корпуса: 699 файлов, из них 107 — байт-идентичные копии
  # (все в deploy/host/state: пары .files.txt и .manifest.txt совпадают
  # по содержимому). После схлопывания остаётся 592 файла.
  #
  # Область docs означает: схлопываются только документы (md, markdown,
  # rst, txt). Проверено на этом корпусе: отброшено 107 файлов, из них
  # документов 107, файлов кода 0 — то есть код не затронут.
  #
  # Файлы на диске НЕ удаляются: они просто не идут в индекс.
  dedupe_enabled: true
  dedupe_scope: docs      # docs | all
  dedupe_mode: keep_one   # keep_one | skip_all
  dedupe_keep: cleanest   # cleanest | shortest | longest | newest
  dedupe_report: true     # показывать разбор групп в ответе операции
"""

text = TARGET.read_text(encoding="utf-8")

if "dedupe_enabled" in text:
    print("настройки схлопывания уже есть в конфиге — правка не нужна")
else:
    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"резервная копия: {BACKUP.name}")

    # Ключи дописываются в конец секции index. Ищется последняя строка
    # секции: она — самая длинная цепочка строк с отступом в два пробела.
    lines = text.split("\n")
    idx_start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("index:")), None
    )
    if idx_start is None:
        raise SystemExit("НЕ НАЙДЕНА секция index в конфиге")

    # Конец секции: первая строка после index, которая не начинается
    # с пробелов (то есть новая секция верхнего уровня), либо конец файла.
    idx_end = len(lines)
    for i in range(idx_start + 1, len(lines)):
        if lines[i] and not lines[i].startswith((" ", "\t")):
            idx_end = i
            break

    print(f"  секция index: строки {idx_start + 1}..{idx_end}")
    new_lines = lines[:idx_end] + BLOCK.rstrip("\n").split("\n") + [""] + lines[idx_end:]
    TARGET.write_text("\n".join(new_lines), encoding="utf-8", newline="\n")
    print("  настройки добавлены в секцию index")

after = TARGET.read_text(encoding="utf-8")
print()
print(f"  размер конфига: {len(after.encode('utf-8'))} байт")
for key in ("dedupe_enabled", "dedupe_scope", "dedupe_mode", "dedupe_keep",
            "dedupe_report"):
    print(f"  {key}: {'есть' if key in after else 'НЕТ'}")

# Проверка, что конфиг читается и настройки дошли до объекта.
import sys  # noqa: E402

sys.path.insert(0, str(REPO / "src"))
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(TARGET)
print()
print(f"  прочитано: enabled={cfg.index.dedupe_enabled}, "
      f"scope={cfg.index.dedupe_scope!r}, mode={cfg.index.dedupe_mode!r}, "
      f"keep={cfg.index.dedupe_keep!r}, report={cfg.index.dedupe_report}")
assert cfg.index.dedupe_enabled is True, "настройка не применилась"
assert cfg.index.dedupe_scope == "docs", "область не применилась"
print("  настройки применились верно")
