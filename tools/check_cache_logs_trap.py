"""Проверка ловушки: каталоги cache и logs содержат код ПРОЕКТА.

Замер показал: в revoap.com есть
    src/cache/CacheStatistics.php
    src/cache/ModelResponseCache.php
    src/logs/LogsService.php

Это исходный код проекта, лежащий в подкаталоге с «служебным» именем.
Если добавить `**/cache/**` и `**/logs/**` в базовый список исключений,
эти файлы исчезнут из индекса — причём молча, без ошибки.

Задача проверки: убедиться, что каталоги с такими именами бывают РАБОЧИМИ,
и зафиксировать это как основание НЕ вносить их в базовый список.

Проверяется по каждому корпусу: где лежит каталог с таким именем, под `src/`
(код) или в корне (служебное). Разница принципиальная.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
]

# Каталоги, которые по имени кажутся служебными, но могут содержать код.
SUSPECT = ("cache", "logs", "log", "bin", "obj", "publish", "dist", "build",
           "var", "storage", "tmp", "temp")

CODE_EXT = {".php", ".js", ".mjs", ".ts", ".py", ".ps1", ".sh", ".cs",
            ".css", ".html", ".htm", ".json", ".sql", ".yaml", ".yml"}

print("=" * 78)
print("ГДЕ ЛЕЖИТ КАТАЛОГ: ПОД src/ (КОД) ИЛИ В КОРНЕ (СЛУЖЕБНОЕ)")
print("=" * 78)

code_dirs: list[tuple[str, str, str, int]] = []
service_dirs: list[tuple[str, str, int]] = []

for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    root = pathlib.Path(str(cfg.root))
    if not root.exists():
        continue

    for name in SUSPECT:
        for d in root.rglob(name):
            if not d.is_dir():
                continue
            rel = d.relative_to(root)
            parts = [p.lower() for p in rel.parts]
            # «Под кодом» — если каталог лежит внутри каталогов исходников.
            under_code = any(p in ("src", "app", "lib", "includes", "modules",
                                   "classes", "services", "controllers",
                                   "models", "components", "views", "domain")
                             for p in parts[:-1])
            try:
                files = [f for f in d.rglob("*") if f.is_file()]
            except OSError:
                continue
            code = [f for f in files if f.suffix.lower() in CODE_EXT]
            if under_code and code:
                code_dirs.append((title, str(rel), str(code[0].name),
                                  len(code)))
            elif not under_code:
                service_dirs.append((title, str(rel), len(files)))

print("\n  КАТАЛОГИ С КОДОМ ПРОЕКТА (под src/ и подобными):")
if not code_dirs:
    print("    не найдено")
for title, rel, example, n in code_dirs:
    print(f"    {title:<22} {rel:<28} кода {n}, пример: {example}")

print("\n  КАТАЛОГИ В КОРНЕ (похожи на служебные):")
for title, rel, n in sorted(service_dirs, key=lambda r: -r[2])[:14]:
    print(f"    {title:<22} {rel:<28} файлов {n}")

print()
print("=" * 78)
print("ПРОВЕРКА: ЧТО ПРОИЗОЙДЁТ, ЕСЛИ ИСКЛЮЧИТЬ cache И logs ГЛОБАЛЬНО")
print("=" * 78)
# Симулируется применение шаблонов к реальным путям корпуса revoap.
from ragraf import corpus, util  # noqa: E402

TEST_PATTERNS = ["**/cache/**", "**/logs/**"]
cfg = load_config(REPO / "config" / "revoap.yaml")
result = corpus.scan(cfg)

would_drop = [
    f.path for f in result.files
    if util.matches_any(f.path, TEST_PATTERNS)
]
print(f"\n  корпус revoap: файлов в скане {len(result.files)}")
print(f"  выпало бы при глобальном исключении: {len(would_drop)}")
for p in would_drop:
    print(f"    {p}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Каталогов с кодом проекта под src/: {len(code_dirs)}

  Это значит, что по имени каталог от служебного НЕ отличается:
    src/logs/LogsService.php     — исходник проекта;
    logs/access.log              — журнал работы.

  Глобальное исключение по имени выбросило бы исходник. Поэтому `cache`,
  `logs`, `bin`, `obj`, `publish` НЕ вносятся в базовый список: их
  исключают в конфиге конкретного корпуса, где видно назначение.

  В базовый список можно вносить только те каталоги, назначение которых
  однозначно: `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.venv`,
  `venv`, `.idea`, `.vscode`, `coverage`, `TestResults`, `node_modules`,
  `vendor`, `bower_components`, `.nuget`, `packages`, `target`.
""")
