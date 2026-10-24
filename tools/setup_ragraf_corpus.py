"""Настройка конфига корпуса RAGRAF: исключения служебных каталогов.

`init` дал 2638 файлов и 894 МБ — это явно неверно: в индекс попали
скопированные зависимости и внешние инструменты. Замер показывает, что
надо исключить:

  * `.tools`          — 2761 файл, 5,2 ГБ: JDK, Joern, GPU-библиотеки;
  * `.ort_gpu_probe`  — 2391 файл, 1,9 ГБ: СКОПИРОВАННЫЕ зависимости
                        (numpy, nvidia, flatbuffers) для замера GPU;
  * `.staging`        — 182 файла, 16 МБ: временные файлы задач сессий;
  * `.zwork`          — 367 файлов, 9,3 МБ: журналы фоновых задач;
  * `workspace`       — рабочие файлы Joern (CPG-бинарники);
  * `catalog`         — реестр и настройки, не код.

ПОЧЕМУ ЭТО В КОНФИГЕ RAGRAF, А НЕ В ОБЩЕМ СПИСКЕ.
Замер: в корпусе RdpMON каталог `.staging` содержит РАБОЧИЕ фикстуры
(15 файлов: `bom_crlf.php`, `crlf_only.css` и другие) — это тестовые
данные инструмента, а не мусор. Общее исключение потеряло бы их:
было 1266 файлов, стало 1251.

Значит `.staging` нельзя исключать глобально: в одних проектах это
временные файлы, в других — рабочие данные. Решение принимает владелец
проекта, а не общий список.

Ожидаемый результат: около 1155 файлов кода вместо 2638.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

CONFIG = REPO / "config" / "ragraf.yaml"
text = CONFIG.read_text(encoding="utf-8")
print(f"  конфиг: {CONFIG}")
print(f"  размер: {len(text.encode('utf-8'))} байт")

# Дополнительные исключения для ЭТОГО корпуса.
OWN_EXCLUDES = [
    "**/.staging/**",
    "**/.tools/**",
    "**/.ort_gpu_probe/**",
    "**/.zwork/**",
    "**/workspace/**",
    "**/catalog/**",
]

# Проверяется, что уже есть.
already = [p for p in OWN_EXCLUDES if p in text]
missing = [p for p in OWN_EXCLUDES if p not in text]
print(f"\n  уже в конфиге: {len(already)}")
for p in already:
    print(f"    {p}")
print(f"  надо добавить: {len(missing)}")
for p in missing:
    print(f"    {p}")

if not missing:
    print("\n  конфиг уже настроен")
else:
    # Ищется блок exclude в YAML.
    import re

    match = re.search(r"^exclude:\n((?:  - .*\n)+)", text, re.MULTILINE)
    if not match:
        raise SystemExit("НЕ НАЙДЕН блок exclude в конфиге")
    block = match.group(0)
    additions = "".join(f"  - {p}\n" for p in missing)
    new_block = block.rstrip("\n") + "\n" + additions
    text = text.replace(block, new_block, 1)
    CONFIG.write_text(text, encoding="utf-8", newline="\n")
    print(f"\n  добавлено правил: {len(missing)}")

print()
print("=" * 78)
print("ПРОВЕРКА РЕЗУЛЬТАТА")
print("=" * 78)
cfg = load_config(CONFIG)
result = corpus.scan(cfg)
print(f"  файлов к индексации: {len(result.files)}")
print(f"  всего найдено:        {result.files_found}")
total_kb = sum(result.file_sizes.values()) / 1024 if hasattr(
    result, "file_sizes") else 0
print(f"  объём:                {total_kb:.0f} КБ")

from collections import Counter  # noqa: E402

by_lang = Counter(f.lang for f in result.files)
print(f"\n  по языкам:")
for lang, n in by_lang.most_common(14):
    print(f"    {lang:<14} {n}")

# Проверка: ничего служебного не осталось.
print()
print("  проверка исключений:")
checks = [
    ("staging", ".staging"), (".tools", ".tools"),
    ("ort_gpu_probe", ".ort_gpu_probe"), ("zwork", ".zwork"),
    ("workspace", "workspace"), ("catalog", "catalog"),
]
for label, marker in checks:
    leaked = [f for f in result.files if marker in str(f.path)]
    mark = "ok" if not leaked else f"ПРОТЕКЛО: {len(leaked)}"
    print(f"    {label:<16} {mark}")
    for f in leaked[:3]:
        print(f"      - {f.path}")

# Проверка: код самого RAGRAF на месте.
print()
print("  код проекта присутствует:")
for label, marker in (("src", "src/ragraf/telemetry.py"),
                      ("tests", "tests/"),
                      ("tools", "tools/ragraf.py"),
                      ("config", "config/")):
    found = [f for f in result.files if marker in str(f.path)]
    print(f"    {label:<10} файлов {len(found)}")
