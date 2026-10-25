"""Расхождение двух списков исключений — настоящий пробел.

Установлено:
  * `.ragraf` есть и в `DEFAULT_EXCLUDES` (config.py), и в `EXCLUDE_DIRS`
    (init_corpus.py), и в конфигах всех шести корпусов. В сканы он не
    попадает — проверено фактически (0 файлов, каталог обрезается 1 раз).

  * НО два списка РАСХОДЯТСЯ:
      EXCLUDE_DIRS (по нему работает init)  — 28 каталогов;
      DEFAULT_EXCLUDES (реальное сканирование) — 13 шаблонов.

Значит корпус, созданный НЕ через `init` (вручную или ранней версией),
не получит исключений для `__pycache__`, `obj`, `dist`, `.pytest_cache`
и подобных. Они попадут в индекс.

Этот замер проверяет, какие каталоги расходятся и БЕЗОПАСНО ли их
исключать глобально: содержимое проверяется, а не угадывается по имени.
Особое внимание `bin`: по имени он служебный, но в revoap там рабочие
PHP-скрипты.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import DEFAULT_EXCLUDES, load as load_config  # noqa: E402
from ragraf.init_corpus import EXCLUDE_DIRS  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
]

# Каталоги, которые знает init, но о которых молчит базовый список.
in_default = {p.strip("*/. ") for p in DEFAULT_EXCLUDES}
only_init = sorted(set(EXCLUDE_DIRS) - in_default)

print("=" * 78)
print("1. РАСХОЖДЕНИЕ СПИСКОВ")
print("=" * 78)
print(f"\n  DEFAULT_EXCLUDES (применяется всегда): {len(DEFAULT_EXCLUDES)}")
print(f"  EXCLUDE_DIRS (только при init):        {len(EXCLUDE_DIRS)}")
print(f"\n  есть ТОЛЬКО в init ({len(only_init)}) — при ручном конфиге НЕ исключаются:")
for name in only_init:
    print(f"    {name:<20} {EXCLUDE_DIRS[name]}")

print()
print("=" * 78)
print("2. ЧТО ВНУТРИ ЭТИХ КАТАЛОГОВ: БЕЗОПАСНО ЛИ ИСКЛЮЧАТЬ")
print("=" * 78)
# Ключевая проверка: каталог исключается глобально только если в нём
# НЕТ исходного кода. Иначе исключение выбросит рабочие файлы.
CODE_EXTS = {".php", ".js", ".mjs", ".ts", ".py", ".ps1", ".sh", ".cs",
             ".css", ".html", ".htm", ".json", ".sql", ".md", ".yaml", ".yml"}

verdicts: dict[str, dict] = {}
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    root = pathlib.Path(str(cfg.root))
    if not root.exists():
        continue
    for name in only_init:
        for d in root.rglob(name):
            if not d.is_dir():
                continue
            try:
                files = [f for f in d.rglob("*") if f.is_file()]
            except OSError:
                continue
            exts = Counter(f.suffix.lower() for f in files)
            code = {e: n for e, n in exts.items() if e in CODE_EXTS}
            entry = verdicts.setdefault(
                name, {"files": 0, "code": Counter(), "examples": [],
                       "projects": set()}
            )
            entry["files"] += len(files)
            entry["code"].update(code)
            entry["projects"].add(title)
            if code and len(entry["examples"]) < 3:
                for f in files:
                    if f.suffix.lower() in CODE_EXTS:
                        entry["examples"].append(
                            f"{title}: {f.relative_to(root)}"
                        )
                        break

print(f"\n  {'каталог':<20} {'файлов':>8} {'проектов':>9}  вердикт")
print("  " + "-" * 78)
for name in only_init:
    info = verdicts.get(name)
    if not info:
        print(f"  {name:<20} {'—':>8} {'—':>9}  не найден ни в одном корпусе")
        continue
    code_total = sum(info["code"].values())
    # Вердикт: безопасно, если кода нет; осторожно, если код есть.
    if code_total == 0:
        verdict = "исключать безопасно (только служебное)"
    else:
        verdict = f"ОСТОРОЖНО: кода {code_total} ({list(info['code'])[:3]})"
    print(f"  {name:<20} {info['files']:>8} {len(info['projects']):>9}  {verdict}")
    for ex in info["examples"][:2]:
        print(f"      пример: {ex}")

print()
print("=" * 78)
print("3. ОСОБЫЙ СЛУЧАЙ: bin")
print("=" * 78)
print("""
  По имени `bin` — служебный каталог сборки. Но замер прошлого шага показал,
  что в revoap.com там ЛЕЖАТ РАБОЧИЕ PHP-СКРИПТЫ:
      bin/cache-purge.php
      bin/export-worker-config.php
      bin/memory-retention.php
      bin/models-actualize-heal.php
      bin/revoap-worker.php
  А в RdpMON `bin` — это 7454 файла сборки .NET на 2,4 ГБ.

  Вывод: `bin` НЕЛЬЗЯ исключать глобально. Он служебный в одном проекте
  и рабочий в другом, и по имени это неразличимо. Правильное решение —
  исключать его в конфиге КОНКРЕТНОГО корпуса (как сделано в RdpMON),
  а не в общем списке.
""")

print("=" * 78)
print("4. СВОДКА: ЧТО МОЖНО ВНЕСТИ В БАЗОВЫЙ СПИСОК")
print("=" * 78)
safe = []
risky = []
for name in only_init:
    info = verdicts.get(name)
    if not info:
        continue
    code_total = sum(info["code"].values())
    (safe if code_total == 0 else risky).append((name, code_total,
                                                 info["files"]))

print(f"\n  БЕЗОПАСНО добавить в DEFAULT_EXCLUDES ({len(safe)}):")
for name, _, files in sorted(safe):
    print(f"    {name:<20} (встречен в корпусах, файлов {files})")

print(f"\n  НЕ добавлять глобально ({len(risky)}):")
for name, code, files in sorted(risky):
    print(f"    {name:<20} содержит кода {code} из {files} файлов")
