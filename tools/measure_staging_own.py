"""Замер: что будет, если исключить `.staging` на разных уровнях.

Вопрос владельца: исключать ли `.staging` в конфиге RAGRAF, если сам RAGRAF
подключат корпусом.

Тут есть ловушка, найденная ранее: в корпусе RdpMON `.staging` содержит
НАСТОЯЩИЕ скрипты (`port-diff/reference.sh`, `ps-port/clean-bom-senior.ps1`),
поэтому глобальное исключение `.staging` их потеряет.

Замеряется три варианта:
  A. добавить `.staging` в DEFAULT_EXCLUDES (общий список) — что потеряем;
  B. добавить только в конфиг корпуса RAGRAF — что потеряем;
  C. ничего не менять.

Проверяется по факту: сколько файлов и какие именно исчезнут из каждого
корпуса. Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
    ("sagaai.yaml", "sagaai"),
]

print("=" * 78)
print("ЧТО ЛЕЖИТ В `.staging` КАЖДОГО КОРПУСА")
print("=" * 78)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    root = pathlib.Path(str(cfg.root))
    # Ищутся все файлы проекта, включая .staging — напрямую по диску.
    staged = []
    for p in root.rglob("*"):
        if ".staging" not in p.parts:
            continue
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        # Исключаются служебные каталоги, как при сканировании.
        parts = set(rel.parts)
        if parts & {".git", ".venv", "node_modules", "__pycache__"}:
            continue
        staged.append(rel)
    if staged:
        print(f"\n  {title}: файлов в .staging — {len(staged)}")
        for rel in sorted(staged)[:10]:
            size = (root / rel).stat().st_size
            print(f"    {str(rel)[:66]:<68} {size} б")
        if len(staged) > 10:
            print(f"    ... и ещё {len(staged) - 10}")
    else:
        print(f"\n  {title}: .staging нет либо пуст")

print()
print("=" * 78)
print("ВАРИАНТ A: `.staging` в ОБЩЕМ списке исключений")
print("=" * 78)
# Проверяется, какие файлы ТЕКУЩИХ корпусов исчезнут.
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    before = corpus.scan(cfg)
    before_files = {str(f.path) for f in before.files}

    # Исключение добавляется только в памяти: файл конфига не меняется.
    import copy

    original = list(cfg.exclude)
    cfg.exclude = original + ["**/.staging/**"]
    after = corpus.scan(cfg)
    after_files = {str(f.path) for f in after.files}
    cfg.exclude = original

    lost = before_files - after_files
    print(f"\n  {title}: файлов было {len(before_files)}, "
          f"станет {len(after_files)}, потеряно {len(lost)}")
    for rel in sorted(lost)[:8]:
        print(f"    - {rel[:70]}")

print()
print("=" * 78)
print("СОСТОЯНИЕ КОНФИГА САМОГО RAGRAF")
print("=" * 78)
print("""
  Корпус самого RAGRAF пока не подключён (в реестре его нет).
  Поэтому вопрос стоит так: что делать ЗАРАНЕЕ, чтобы при подключении
  RAGRAF корпусом его `.staging` не попал в индекс.
""")

# Проверяется, есть ли конфиг RAGRAF и что в нём.
own_config = REPO / "config" / "ragraf.yaml"
print(f"  конфиг config/ragraf.yaml: "
      f"{'есть' if own_config.exists() else 'НЕТ'}")
print(f"  файлов в .staging RAGRAF: "
      f"{len(list((REPO / '.staging').rglob('*')))}")
print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Смотрите таблицу выше: если исключение .staging теряет НАСТОЯЩИЕ файлы
  кода в каком-либо корпусе — в общий список его добавлять нельзя.
  Тогда остаётся вариант B: исключать .staging только там, где он
  действительно служебный, то есть в конфиге самого RAGRAF.
""")
