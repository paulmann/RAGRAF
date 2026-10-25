"""Точный замер спорных каталогов: отделить настоящий код от данных.

Прошлый замер отнёс к «коду» файлы `.md` и `.json`, и из-за этого каталоги
вроде `.dev_agent` выглядели опасными для исключения. Но `.md` — это
документация, а `.json` — данные: их исключение не выбрасывает из индекса
исходники проекта.

Задача: по каждому каталогу, который знает только init, понять, есть ли
в нём НАСТОЯЩИЙ исходный код проекта. Исключать глобально можно лишь те,
где его нет.

Классификация:
    КОД   — php, js, ts, py, ps1, sh, cs, css, html, c, cpp, java, go, lua
    ДАННЫЕ — json, yaml, sql, xml, ini, конфигурации
    ТЕКСТ  — md, rst, txt (документация и заметки)

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

CODE_EXT = {".php", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".py",
            ".ps1", ".psm1", ".sh", ".bash", ".cs", ".css", ".html", ".htm",
            ".c", ".h", ".cpp", ".hpp", ".java", ".go", ".lua", ".rb", ".pl"}
DATA_EXT = {".json", ".yaml", ".yml", ".sql", ".xml", ".ini", ".cfg",
            ".conf", ".toml", ".props", ".csproj", ".sln"}
TEXT_EXT = {".md", ".markdown", ".rst", ".txt", ".log"}

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
]

# Каталоги, которых нет в базовом списке (из точного сравнения).
ONLY_INIT = sorted(set(EXCLUDE_DIRS) - {
    ".agents", ".git", ".ragraf", ".zwork", "build", "dist", "node_modules",
    "var", "vendor",
})

print("=" * 78)
print("СОДЕРЖИМОЕ КАТАЛОГОВ, КОТОРЫЕ ЗНАЕТ ТОЛЬКО init")
print("=" * 78)

stats: dict[str, dict] = {}
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    root = pathlib.Path(str(cfg.root))
    if not root.exists():
        continue
    for name in ONLY_INIT:
        for d in root.rglob(name):
            if not d.is_dir():
                continue
            entry = stats.setdefault(name, {
                "code": Counter(), "data": 0, "text": 0, "other": 0,
                "code_examples": [], "projects": set(),
            })
            entry["projects"].add(title)
            try:
                files = [f for f in d.rglob("*") if f.is_file()]
            except OSError:
                continue
            for f in files:
                ext = f.suffix.lower()
                if ext in CODE_EXT:
                    entry["code"][ext] += 1
                    if len(entry["code_examples"]) < 3:
                        entry["code_examples"].append(
                            f"{title}: {f.relative_to(root)}"
                        )
                elif ext in DATA_EXT:
                    entry["data"] += 1
                elif ext in TEXT_EXT:
                    entry["text"] += 1
                else:
                    entry["other"] += 1

print(f"\n  {'каталог':<18} {'КОД':>6} {'данные':>7} {'текст':>7} "
      f"{'прочее':>7}  вердикт")
print("  " + "-" * 76)

safe: list[str] = []
risky: list[tuple[str, dict]] = []

for name in ONLY_INIT:
    info = stats.get(name)
    if not info:
        print(f"  {name:<18} {'—':>6} {'—':>7} {'—':>7} {'—':>7}  "
              f"не найден в корпусах")
        continue
    code_total = sum(info["code"].values())
    if code_total == 0:
        verdict = "исключать безопасно"
        safe.append(name)
    else:
        verdict = f"ЕСТЬ КОД: {dict(info['code'])}"
        risky.append((name, info))
    print(f"  {name:<18} {code_total:>6} {info['data']:>7} {info['text']:>7} "
          f"{info['other']:>7}  {verdict}")

print()
print("=" * 78)
print("СПОРНЫЕ КАТАЛОГИ: ЧТО ЗА КОД В НИХ")
print("=" * 78)
for name, info in risky:
    print(f"\n  {name}: кода {sum(info['code'].values())} "
          f"({dict(info['code'])})")
    print(f"    проекты: {sorted(info['projects'])}")
    for ex in info["code_examples"]:
        print(f"    пример: {ex}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Безопасно добавить в базовый список ({len(safe)}):
    {', '.join(safe) if safe else '—'}

  Нельзя добавлять глобально ({len(risky)}):
""")
for name, info in risky:
    print(f"    {name}: содержит {sum(info['code'].values())} файлов кода")

print("""
  ВАЖНО: каталог `bin` — отдельный случай. В revoap.com там рабочие
  PHP-скрипты (cache-purge.php, revoap-worker.php и другие), а в RdpMON —
  сборка .NET. По имени это неразличимо, поэтому `bin` остаётся задачей
  конкретного корпуса, а не базовым исключением.
""")
