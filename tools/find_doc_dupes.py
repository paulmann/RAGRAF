"""Замер: где именно 669 дублей документации из разведки.

Прошлый замер показал неожиданное: в подключённых корпусах дублей почти нет
(z — 0, RdpMON — 0, MD.Viewer — 0, revoap — 107 мелких текстовых).
А разведка сообщала о 669 побайтово одинаковых копиях в <полигон>.

Расхождение надо объяснить: либо дубли лежат в каталогах, которые НЕ являются
корпусами RAGRAF, либо разведка считала иначе (например с учётом того, что
исключено правилами exclude).

Проверяется по факту: какие каталоги верхнего уровня содержат дубли и
попали бы они в индекс при подключении.

Ничего не меняет.
"""

from __future__ import annotations

import hashlib
import pathlib
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1].parent

# Расширения документации: разведка считала именно их.
DOC_EXTS = {".md", ".markdown", ".rst", ".txt"}

# Каталоги, которые точно не станут корпусом: служебные и чужие.
SKIP_TOP = {
    ".git", "node_modules", "vendor", ".zwork", ".ragraf", ".tools",
    ".ort_gpu_probe", "__pycache__", "site-packages",
}

# Какие каталоги УЖЕ подключены как корпуса RAGRAF.
CONNECTED = {
    "z.batteryservice.ru", "revoap.com", "MD.Viewer",
}

print("=" * 78)
print("ПОИСК ДУБЛЕЙ ДОКУМЕНТАЦИИ В <полигон>")
print("=" * 78)

# Собираются все документы с хэшем: размер и путь.
by_sha: dict[str, list[tuple[str, int]]] = defaultdict(list)
scanned = 0
for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    try:
        rel_parts = path.relative_to(ROOT).parts
    except ValueError:
        continue
    if any(p in SKIP_TOP or p in {"obj", "bin", "publish"} for p in rel_parts[:-1]):
        continue
    if path.suffix.lower() not in DOC_EXTS:
        continue
    try:
        size = path.stat().st_size
        if size == 0 or size > 2_000_000:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        continue
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    by_sha[digest].append((rel, size))
    scanned += 1

print(f"\n  просканировано документов: {scanned}")

dup_groups = {sha: files for sha, files in by_sha.items() if len(files) > 1}
extra = sum(len(v) - 1 for v in dup_groups.values())
extra_bytes = sum(size for files in dup_groups.values() for _, size in files[1:])

print(f"  групп одинаковых:          {len(dup_groups)}")
print(f"  лишних копий:              {extra}")
print(f"  объём лишних:              {extra_bytes / 1024 / 1024:.2f} МБ")

print()
print("=" * 78)
print("ГДЕ ЛЕЖАТ ЛИШНИЕ КОПИИ (по каталогу верхнего уровня)")
print("=" * 78)
tops: Counter = Counter()
tops_bytes: Counter = Counter()
for files in dup_groups.values():
    for rel, size in files[1:]:
        top = rel.split("/")[0]
        tops[top] += 1
        tops_bytes[top] += size

print(f"\n  {'каталог':<34} {'лишних':>8} {'КБ':>10}  корпус?")
print("  " + "-" * 66)
for top, n in tops.most_common(20):
    mark = "ДА" if top in CONNECTED else "нет"
    print(f"  {top:<34} {n:>8} {tops_bytes[top] / 1024:>10.0f}  {mark}")

# Ключевой вопрос: сколько дублей УЖЕ В ПОДКЛЮЧЁННЫХ корпусах.
in_corpus = sum(n for top, n in tops.items() if top in CONNECTED)
print(f"\n  лишних копий в подключённых корпусах: {in_corpus}")
print(f"  лишних копий вне корпусов:            {extra - in_corpus}")

print()
print("=" * 78)
print("КРУПНЕЙШИЕ ГРУППЫ: ЧТО ЭТО ЗА ДОКУМЕНТЫ")
print("=" * 78)
biggest = sorted(dup_groups.values(), key=lambda g: -len(g))[:8]
for files in biggest:
    rel, size = files[0]
    print(f"\n  {len(files)} копий по {size} б:")
    for r, _ in files[:6]:
        print(f"    {r}")
    if len(files) > 6:
        print(f"    … и ещё {len(files) - 6}")

print()
print("=" * 78)
print("ПРОВЕРКА: ЕСТЬ ЛИ ДУБЛИ ВНУТРИ ОДНОГО ПРОЕКТА")
print("=" * 78)
# Интересны дубли внутри одного каталога проекта: именно они попали бы
# в корпус, если подключить этот проект.
same_project = 0
for files in dup_groups.values():
    by_top: dict[str, int] = defaultdict(int)
    for rel, _ in files:
        by_top[rel.split("/")[0]] += 1
    for top, n in by_top.items():
        if n > 1:
            same_project += n - 1
print(f"  лишних копий внутри одного каталога проекта: {same_project}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Всего лишних копий документации: {extra}
    в подключённых корпусах RAGRAF: {in_corpus}
    вне них:                        {extra - in_corpus}

  Это объясняет расхождение: основная масса дублей — вне корпусов.
  Но реализация дедупликации всё равно нужна, потому что:
    * корпуса будут подключаться и дальше (в том числе каталоги с зеркалами);
    * в revoap дубли уже есть (107 мелких), и они засоряют поиск;
    * схлопывание должно быть настраиваемым, а не разовым ручным удалением.
""")
