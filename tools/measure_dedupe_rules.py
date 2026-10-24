"""Замер: какое правило выбора «главной» копии даёт осмысленный результат.

Перед реализацией нужно выбрать правило, по которому из группы байт-идентичных
файлов остаётся один. Кандидаты:

  A. Самый короткий путь.
     Логика: оригинал обычно лежит ближе к корню, а копии — глубже
     (зеркала вида `revoap/www/www/docs/`).

  B. Самый длинный путь.
     Логика: если копии образуются вложением каталогов, «последняя» глубже.

  C. Путь без «мусорных» сегментов.
     Логика: сегменты `backup`, `old`, `publish`, `release`, `out`, `copy`,
     `www`, `dist` указывают на копию, а не на источник.

  D. Наибольшее время изменения.
     Логика: свежая копия — рабочая.

Правило проверяется на реальных группах: считается, сколько раз каждое
правило выбрало бы путь из мусорного каталога (это ошибка) и сколько раз
путь с мусорным сегментом ОСТАЛСЯ бы в индексе (тоже ошибка).

Ничего не меняет.
"""

from __future__ import annotations

import hashlib
import pathlib
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1].parent
DOC_EXTS = {".md", ".markdown", ".rst", ".txt"}
SKIP_TOP = {
    ".git", "node_modules", "vendor", ".zwork", ".ragraf", ".tools",
    ".ort_gpu_probe", "__pycache__", "site-packages",
}

# Сегменты пути, указывающие на копию, а не на источник.
DIRTY_SEGMENTS = (
    "_backup", "backup", "old", "publish", "release", "dist", "build",
    "out", "copy", "_to.", "archive", "tmp", ".tmp", "verify",
)

print("=" * 78)
print("СБОР ГРУПП ОДИНАКОВЫХ ФАЙЛОВ")
print("=" * 78)

by_sha: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    try:
        parts = path.relative_to(ROOT).parts
    except ValueError:
        continue
    if any(p in SKIP_TOP or p in {"obj", "bin"} for p in parts[:-1]):
        continue
    if path.suffix.lower() not in DOC_EXTS:
        continue
    try:
        stat = path.stat()
        if stat.st_size == 0 or stat.st_size > 2_000_000:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        continue
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    by_sha[digest].append((rel, stat.st_size, stat.st_mtime))

groups = [files for files in by_sha.values() if len(files) > 1]
print(f"  групп: {len(groups)}")
print(f"  файлов в группах: {sum(len(g) for g in groups)}")


def has_dirty(rel: str) -> bool:
    low = rel.lower()
    return any(seg in low for seg in DIRTY_SEGMENTS)


def depth(rel: str) -> int:
    return rel.count("/")


# Правила возвращают индекс выбранного элемента в группе.
def rule_shortest(files):
    return min(range(len(files)), key=lambda i: (depth(files[i][0]), files[i][0]))


def rule_longest(files):
    return max(range(len(files)), key=lambda i: (depth(files[i][0]), files[i][0]))


def rule_clean(files):
    """Предпочесть путь без мусорных сегментов, затем короткий."""
    return min(range(len(files)),
               key=lambda i: (has_dirty(files[i][0]), depth(files[i][0]),
                              files[i][0]))


def rule_newest(files):
    return max(range(len(files)), key=lambda i: (files[i][2], files[i][0]))


RULES = {
    "A самый короткий": rule_shortest,
    "B самый длинный": rule_longest,
    "C без мусорных сегментов": rule_clean,
    "D самый свежий": rule_newest,
}

print()
print("=" * 78)
print("ОЦЕНКА ПРАВИЛ")
print("=" * 78)
print("""
  Ошибка первого рода: правило оставило в индексе путь из мусорного
  каталога (backup/old/publish), а рабочий отбросило.
  Ошибка второго рода: правило отбросило ВСЕ пути с мусорным сегментом,
  хотя других вариантов не было (значит файл в корпусе всё равно мусорный,
  но это не вина правила).
""")

print(f"\n  {'правило':<28} {'оставило мусор':>15} {'найдено':>9} "
      f"{'отброшено файлов':>17}")
print("  " + "-" * 74)

results: dict[str, dict] = {}
for name, rule in RULES.items():
    kept_dirty = 0       # главная копия из мусорного каталога
    all_dirty = 0        # в группе вообще есть мусорные пути
    dropped = 0
    for files in groups:
        idx = rule(files)
        if has_dirty(files[idx][0]):
            kept_dirty += 1
        if any(has_dirty(f[0]) for f in files):
            all_dirty += 1
        dropped += len(files) - 1
    results[name] = {"kept_dirty": kept_dirty, "all_dirty": all_dirty,
                     "dropped": dropped}
    print(f"  {name:<28} {kept_dirty:>15} {all_dirty:>9} {dropped:>17}")

print()
print("=" * 78)
print("РАЗБОР ЛУЧШЕГО КАНДИДАТА НА ПРИМЕРАХ")
print("=" * 78)
# Показывается, что именно выберет каждое правило в крупных группах.
biggest = sorted(groups, key=lambda g: -len(g))[:4]
for files in biggest:
    print(f"\n  группа из {len(files)} копий ({files[0][1]} б):")
    for name, rule in RULES.items():
        chosen = files[rule(files)][0]
        mark = " <-- мусор" if has_dirty(chosen) else ""
        print(f"    {name:<28} {chosen[:60]}{mark}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
best = min(results.items(), key=lambda kv: kv[1]["kept_dirty"])
print(f"""
  Меньше всего «оставленного мусора»: {best[0]}
  (оставило мусорных путей: {best[1]['kept_dirty']} из {len(groups)} групп)

  Это правило и берётся за основу:
    1. сначала предпочитается путь БЕЗ сегментов-признаков копии
       (backup, old, publish, release, out, copy, www, _to.upload, verify);
    2. при равенстве — более короткий путь (оригинал ближе к корню);
    3. при полном равенстве — лексикографически первый (детерминизм).

  ВАЖНО: правило должно быть ДЕТЕРМИНИРОВАННЫМ. Если выбор зависит от
  порядка обхода каталога, то при каждом сканировании «главным» может стать
  разный файл, сумма корпуса изменится, и сторож свежести начнёт бесконечно
  запускать переиндексацию. Поэтому третий пункт обязателен, а не «на всякий
  случай»: он делает результат воспроизводимым.
""")
