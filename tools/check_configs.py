"""Проверка целостности конфигураций корпусов после правки секции index.

Правка настроек выполнялась регуляркой, поэтому нужно убедиться, что все секции
и значения на месте: конфиг корпуса — входные данные индексации, и его порча
не проявится сразу, а испортит индекс.

Проверяется: файл разбирается как YAML, обязательные секции присутствуют,
значения читаются штатным загрузчиком, число секций верхнего уровня совпадает
с ожидаемым.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402

EXPECTED_SECTIONS = {
    "z-batteryservice": [
        "project", "root", "languages", "include", "exclude",
        "db", "index", "embedding",
    ],
    "revoap": [
        "project", "root", "languages", "include", "exclude",
        "db", "index", "embedding",
    ],
}

FAIL = 0

for name, sections in EXPECTED_SECTIONS.items():
    path = REPO / "config" / f"{name}.yaml"
    print(f"=== {name}.yaml ===")
    text = path.read_text(encoding="utf-8")

    # Секции верхнего уровня: строка без отступа с двоеточием.
    found = re.findall(r"^([a-z_][a-z0-9_]*):", text, re.MULTILINE)
    print(f"  секции: {found}")
    for section in sections:
        if section in found:
            print(f"    [OK ] секция {section}")
        else:
            print(f"    [FAIL] секция {section} отсутствует")
            FAIL += 1

    # Дубликаты секций: правка регуляркой могла добавить вторую секцию index.
    dupes = [s for s in set(found) if found.count(s) > 1]
    if dupes:
        print(f"    [FAIL] дубликаты секций: {dupes}")
        FAIL += 1
    else:
        print("    [OK ] дубликатов секций нет")
    if "index" in found and found.count("index") > 1:
        print("    [FAIL] секция index задублирована")
        FAIL += 1

    # Штатный загрузчик должен прочитать конфиг.
    try:
        cfg = load_config(path)
        print(f"    [OK ] конфиг загружен: проект {cfg.project}")
        print(f"         пороги: быстрая {cfg.index.reindex_after_changes}, "
              f"полная {cfg.index.full_reindex_after_changes}, "
              f"интервал {cfg.index.reindex_interval_minutes} мин")
        print(f"         включений: {len(cfg.include)}, исключений: {len(cfg.exclude)}, "
              f"языков: {cfg.languages}")
        print(f"         режим CPG: {cfg.index.joern_enabled}, "
              f"хранить ревизий: {cfg.index.retain_revs}")
        if not cfg.include or not cfg.languages:
            print("    [FAIL] список включений или языков пуст")
            FAIL += 1
    except Exception as exc:
        print(f"    [FAIL] конфиг не загружается: {type(exc).__name__}: {exc}")
        FAIL += 1

    # Комментарии в секции index сохранены?
    idx = text.find("\nindex:")
    if idx > 0:
        block = text[idx:idx + 900]
        has_comments = "#" in block
        print(f"    {'[OK ]' if has_comments else '[FAIL]'} комментарии в секции index "
              f"{'сохранены' if has_comments else 'потеряны'}")
        if not has_comments:
            FAIL += 1
    print()

print(f"провалов: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
