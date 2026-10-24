"""Замер: какие служебные каталоги физически есть в корпусах и обходятся ли.

Установлено:
  * `.ragraf` есть и в DEFAULT_EXCLUDES, и в конфигах всех шести конфигов;
  * базовые исключения применяются АВТОМАТИЧЕСКИ (config.py:654:
    `exclude = list(DEFAULT_EXCLUDES) + из файла`);
  * в индексах четырёх рабочих корпусов служебных путей нет.

Значит, по `.ragraf` задача уже выполнена. Но проверка выявила другое:
в правилах корпусов НЕТ целого ряда служебных каталогов, которые физически
существуют. Этот замер показывает, какие именно и сколько в них файлов —
чтобы решить, надо ли их исключать.

Осторожность: `bin` в revoap содержит 6 рабочих PHP-скриптов и НЕ является
служебным. Поэтому замер только измеряет, а не предлагает исключать слепо.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import util  # noqa: E402
from ragraf.config import DEFAULT_EXCLUDES, load as load_config  # noqa: E402

# Каталоги, которые считаются служебными по общепринятому смыслу.
SERVICE = (
    ".ragraf", ".zwork", ".git", ".agents", ".pi", ".staging", ".tools",
    ".dev_agent", ".openclaw", ".autoclaw", ".cluster", ".opencode",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".nuget",
    "__pycache__", "node_modules", "vendor", "bower_components",
    "obj", "bin", "publish", "dist", "build", "target", "coverage",
    "logs", "cache", "uploads", "TestResults", ".venv", "venv",
    ".ort_gpu_probe", "var",
)

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
]

print("=" * 78)
print("КАКИЕ СЛУЖЕБНЫЕ КАТАЛОГИ ЕСТЬ ФИЗИЧЕСКИ И ОБХОДЯТСЯ ЛИ")
print("=" * 78)

for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    root = pathlib.Path(str(cfg.root))
    if not root.exists():
        print(f"\n  {title}: корня нет")
        continue

    patterns = list(cfg.exclude)
    print(f"\n{'=' * 78}")
    print(f"{title}   ({root})")
    print("=" * 78)

    rows = []
    for name in SERVICE:
        dirs = [d for d in root.rglob(name) if d.is_dir()]
        if not dirs:
            continue
        files = 0
        size = 0
        for d in dirs:
            try:
                for f in d.rglob("*"):
                    if f.is_file():
                        files += 1
                        size += f.stat().st_size
            except OSError:
                pass
        # Проверяется, исключён ли каталог правилами корпуса.
        sample = f"{name}/x.txt"
        excluded_root = util.matches_any(sample, patterns)
        sample_nested = f"sub/{name}/x.txt"
        excluded_nested = util.matches_any(sample_nested, patterns)
        rows.append((name, len(dirs), files, size, excluded_root,
                     excluded_nested))

    if not rows:
        print("  служебных каталогов не найдено")
        continue

    print(f"\n  {'каталог':<20} {'мест':>5} {'файлов':>8} {'МБ':>8} "
          f"{'в корне':>9} {'вложенно':>9}")
    print("  " + "-" * 68)
    for name, places, files, size, ex_root, ex_nested in sorted(
        rows, key=lambda r: -r[3]
    ):
        print(f"  {name:<20} {places:>5} {files:>8} {size / 1024 / 1024:>8.2f} "
              f"{'искл.' if ex_root else 'ОБХОД':>9} "
              f"{'искл.' if ex_nested else 'ОБХОД':>9}")

    # Отдельно: каталоги, которые обходятся и при этом содержат много файлов.
    open_dirs = [r for r in rows if not r[5] and r[2] > 0]
    if open_dirs:
        print(f"\n  ОБХОДЯТСЯ и содержат файлы ({len(open_dirs)}):")
        for name, places, files, size, _, _ in open_dirs:
            print(f"    {name}: {files} файлов, {size / 1024 / 1024:.2f} МБ")

print()
print("=" * 78)
print("ПРОВЕРКА: ОБХОДИТ ЛИ СКАНЕР КАТАЛОГ .ragraf ФИЗИЧЕСКИ")
print("=" * 78)
# Если каталог обрезается, он не должен попасть в счётчик пропущенных
# каталогов как ошибка, но сам обход должен его пропустить.
from ragraf import corpus  # noqa: E402

for config_name, title in CORPORA[:4]:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    result = corpus.scan(cfg)
    hits = [p for p in result.excluded if ".ragraf" in p]
    print(f"\n  {title}:")
    print(f"    сработавших правил для .ragraf: {len(hits)}")
    for h in hits[:3]:
        print(f"      {h}: {result.excluded[h]} раз")
    ragraf_files = [f.path for f in result.files if ".ragraf" in f.path]
    print(f"    файлов .ragraf в скане: {len(ragraf_files)}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  По `.ragraf` задача выглядит уже выполненной: он в базовом списке,
  в каждом конфиге, и в сканы не попадает. Проверяется это дважды:
  по правилам и по фактическому результату сканирования.

  Заодно замер показывает служебные каталоги, которые ОБХОДЯТСЯ:
  `__pycache__`, `.pytest_cache`, `obj`, `publish` и подобные. Из них
  безопасно исключать не все: `bin` в revoap содержит рабочие скрипты.
""")
