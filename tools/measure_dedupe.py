"""Замер: сколько байт-идентичных дублей в каждом корпусе и какие они.

Нужно перед реализацией. От результата зависит:
  1. правило выбора «главной» копии (какой путь оставлять);
  2. надо ли ограничивать дедупликацию документацией или применять ко всему;
  3. сколько именно экономится (файлов, чанков, объёма).

Замер идёт по тому же пути, что и индексатор: вызывается `corpus.scan`,
то есть учитываются все исключения, секреты и бинарные файлы корпуса.
Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter, defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.corpus import scan  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
]

# Каталоги-зеркала и резервные копии: по ним видно, кто создаёт дубли.
MIRROR_MARKERS = ("_backup", "backup", "old", ".old", "archive", "copy",
                  "mirror", "release", "publish", "out/", "fixtures")

for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    result = scan(cfg)

    print()
    print("=" * 78)
    print(f"{title}   (файлов в корпусе: {len(result.files)})")
    print("=" * 78)

    # Группировка по содержимому.
    by_sha: dict[str, list] = defaultdict(list)
    for f in result.files:
        by_sha[f.sha256].append(f)

    dup_groups = {sha: files for sha, files in by_sha.items() if len(files) > 1}
    dup_files = sum(len(v) for v in dup_groups.values())
    extra = dup_files - len(dup_groups)   # сколько файлов станет лишними
    extra_bytes = sum(
        f.bytes for files in dup_groups.values() for f in files[1:]
    )
    extra_lines = sum(
        f.lines for files in dup_groups.values() for f in files[1:]
    )
    extra_chunks_est = extra_lines // 45   # оценка по CHARS_PER_LINE

    print(f"  групп одинаковых файлов:      {len(dup_groups)}")
    print(f"  файлов в этих группах:        {dup_files}")
    print(f"  лишних копий (можно убрать):  {extra}")
    print(f"    объём лишних:               {extra_bytes / 1024 / 1024:.2f} МБ")
    print(f"    строк лишних:               {extra_lines}")

    if not dup_groups:
        continue

    # По языкам: где именно дубли.
    by_lang: Counter = Counter()
    by_lang_extra: Counter = Counter()
    for files in dup_groups.values():
        langs = {f.lang for f in files}
        for lang in langs:
            by_lang[lang] += 1
        by_lang_extra[files[0].lang] += len(files) - 1
    print(f"\n  по языкам (лишних файлов):")
    for lang, n in by_lang_extra.most_common():
        groups = by_lang.get(lang, 0)
        print(f"    {lang:<14} групп {groups:>4}  лишних {n:>5}")

    # Крупнейшие группы: видно, что это за дубли.
    print(f"\n  крупнейшие группы (по размеру файла):")
    biggest = sorted(dup_groups.values(), key=lambda g: -g[0].bytes)[:6]
    for files in biggest:
        kb = files[0].bytes / 1024
        print(f"\n    {len(files)} копий по {kb:.0f} КБ ({files[0].lang}):")
        for f in files[:8]:
            print(f"      {f.path}")
        if len(files) > 8:
            print(f"      … и ещё {len(files) - 8}")

    # Откуда берутся дубли: по каталогам верхнего уровня.
    print(f"\n  лишние копии по каталогам верхнего уровня:")
    tops: Counter = Counter()
    for files in dup_groups.values():
        for f in files[1:]:
            top = f.path.split("/")[0] if "/" in f.path else "(корень)"
            tops[top] += 1
    for top, n in tops.most_common(8):
        print(f"    {top:<28} {n}")

    # Проверка гипотезы: помогает ли правило «предпочитать короткий путь».
    print(f"\n  проверка правила выбора: длина пути")
    shorter_wins = 0
    same_depth = 0
    longer_wins = 0
    for files in dup_groups.values():
        depths = [f.path.count("/") for f in files]
        if min(depths) < max(depths):
            shorter_wins += 1
        else:
            same_depth += 1
    print(f"    в группе есть путь короче остальных: {shorter_wins}")
    print(f"    все пути одной глубины:              {same_depth}")

    # Проверка: попадают ли «лишние» копии в зеркала/резервные каталоги.
    in_mirror = 0
    for files in dup_groups.values():
        for f in files[1:]:
            low = f.path.lower()
            if any(m in low for m in MIRROR_MARKERS):
                in_mirror += 1
    print(f"    лишних копий в каталогах-зеркалах:   {in_mirror} из {extra}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Числа выше показывают масштаб и природу дублей. Для реализации важно:
    * если у большинства групп есть путь заметно короче остальных —
      правило «оставить самый короткий путь» даёт осмысленный результат;
    * если копии лежат в каталогах-зеркалах (backup, old, release, publish) —
      их надо отдавать под удаление в первую очередь;
    * если дубли есть не только у документации, дедупликацию нельзя
      ограничивать только .md: байт-идентичные копии кода так же удваивают
      индекс и шум в поиске.
""")
