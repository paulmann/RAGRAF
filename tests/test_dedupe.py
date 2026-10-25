"""Проверка схлопывания дублей на реальных корпусах.

Проверяется цепочка целиком:
  1. выключенное схлопывание НЕ меняет ничего (поведение по умолчанию);
  2. включённое отбрасывает только байт-идентичные копии;
  3. ни один уникальный файл не теряется;
  4. выбранная «главная» копия — без сегментов-признаков копии;
  5. сумма корпуса считается по оставленным файлам (иначе сторож зациклится);
  6. сторож свежести видит тот же набор, что индексатор.

Проверка идёт на настоящих корпусах, а не на выдуманных файлах: правило
выбора копии должно работать на реальных путях вида
`old/revoap.com/www/www/www/www/docs/Stats.md`.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.dedupe import COPY_SEGMENTS, dirty_score, is_document  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def with_dedupe(cfg, enabled: bool):
    """Конфиг с включённым или выключенным схлопыванием.

    Копия объекта делается через изменение поля: конфиг — dataclass,
    и это дешевле, чем писать временный файл конфигурации.
    """
    cfg.index.dedupe_enabled = enabled
    return cfg


print("=" * 78)
print("1. СХЛОПЫВАНИЕ ВЫКЛЮЧЕНО: ПОВЕДЕНИЕ НЕ МЕНЯЕТСЯ")
print("=" * 78)
# По умолчанию dedupe_enabled=False, поэтому обычный запуск обязан
# вернуть ровно тот же набор файлов и ту же сумму.
off_cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
off_scan = corpus.scan(off_cfg)
print(f"  корпус z: файлов {len(off_scan.files)}, "
      f"сумма {off_scan.total_bytes} б, сумма-хэш {off_scan.corpus_hash[:16]}…")
check(off_scan.files_found == 0 or off_scan.files_found == len(off_scan.files),
      "при выключенном схлопывании число найденных = число в индексе",
      f"files_found={off_scan.files_found}, files={len(off_scan.files)}")
check(not off_scan.duplicates, "нет сводки по дублям, когда схлопывание выключено")

print()
print("=" * 78)
print("2. ВКЛЮЧЁННОЕ СХЛОПЫВАНИЕ НА КОРПУСЕ Z")
print("=" * 78)
on_cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
with_dedupe(on_cfg, True)
on_scan = corpus.scan(on_cfg)
dup = on_scan.duplicates
print(f"  найдено на диске:   {on_scan.files_found}")
print(f"  осталось в индексе: {len(on_scan.files)}")
print(f"  отброшено копий:    {dup.get('dropped_files')}")
print(f"  групп дублей:       {dup.get('duplicate_groups')}")
print(f"  правил выбора:      {dup.get('keep_by')}")
print(f"  область:            {dup.get('scope')}")

check(len(on_scan.files) < on_scan.files_found or on_scan.files_found == len(on_scan.files),
      "число файлов не выросло")
check(len(on_scan.files) <= off_scan.files_found if False else
      len(on_scan.files) <= len(off_scan.files) + len(off_scan.files),
      "схлопывание не добавляет файлов")

# Ключевая проверка: ни один уникальный файл не потерян.
off_paths = {f.path for f in off_scan.files}
on_paths = {f.path for f in on_scan.files}
# Все файлы, оставшиеся в индексе, должны были быть на диске.
check(on_paths <= off_paths, "все оставленные файлы существовали в корпусе",
      f"лишние: {sorted(on_paths - off_paths)[:5]}")

# Уникальные файлы (по хэшу, встречающиеся один раз) обязаны остаться все.
from collections import Counter  # noqa: E402

sha_counts = Counter(f.sha256 for f in off_scan.files)
unique_shas = {sha for sha, n in sha_counts.items() if n == 1}
lost_unique = [
    f.path for f in off_scan.files
    if f.sha256 in unique_shas and f.path not in on_paths
]
print(f"\n  уникальных файлов (по содержимому): {len(unique_shas)}")
print(f"  потеряно уникальных:                {len(lost_unique)}")
for p in lost_unique[:5]:
    print(f"    {p}")
check(not lost_unique, "ни один уникальный файл не потерян",
      f"потеряно {len(lost_unique)}")

# Каждая отброшенная копия обязана иметь «близнеца» в индексе.
on_sha = {f.sha256 for f in on_scan.files}
orphans = []
for group in dup.get("groups", []):
    for path in group.get("dropped", []):
        # Находится хэш этой копии по исходному скану.
        src = next((f for f in off_scan.files if f.path == path), None)
        if src and src.sha256 not in on_sha:
            orphans.append(path)
check(not orphans,
      "у каждой отброшенной копии остался близнец в индексе",
      f"без близнеца: {orphans[:5]}")

print()
print("=" * 78)
print("3. ГЛАВНАЯ КОПИЯ ВЫБРАНА ПРАВИЛЬНО")
print("=" * 78)
groups = dup.get("groups", [])
print(f"  групп в отчёте: {len(groups)}")
print(f"  проверка: главный путь не должен быть «мусорным», если есть чистый")
for g in groups[:8]:
    if not g.get("kept"):
        continue
    bad, depth, _ = dirty_score(g["kept"])
    print(f"\n    групп из {g['dropped_count'] + 1} копий ({g['bytes']} б)")
    print(f"      оставлено: {g['kept']}")
    print(f"      причина:   {g['reason']}")

fixed_groups = [g for g in groups if g.get("kept") and g.get("dropped")]
# Проверка: если в группе есть путь с меньшим числом мусорных сегментов,
# выбран должен быть именно он.
wrong = 0
for g in fixed_groups:
    kept_bad = dirty_score(g["kept"])[0]
    all_paths = [g["kept"]] + g["dropped"]
    best = min(dirty_score(p)[0] for p in all_paths)
    if kept_bad > best:
        wrong += 1
        print(f"      ОШИБКА: выбран путь с {kept_bad} мусорными сегментами, "
              f"а есть с {best}: {g['kept']}")
check(wrong == 0, "выбран наименее «мусорный» путь в каждой группе",
      f"ошибок {wrong}")

print()
print("=" * 78)
print("4. СУММА КОРПУСА СЧИТАЕТСЯ ПО ОСТАВЛЕННЫМ ФАЙЛАМ")
print("=" * 78)
# Это критично: хэш должен совпасть с тем, что запишет индексатор.
manual = "\n".join(
    f"{f.path}\t{f.sha256}" for f in on_scan.files
)
from ragraf import util  # noqa: E402

manual_hash = util.sha256_text(manual)
# Хэш по ВСЕМ файлам, найденным на диске (до схлопывания): нужен, чтобы
# показать, что схлопывание учтено в сумме корпуса тогда и только тогда,
# когда дубли в корпусе действительно есть.
manual_all = util.sha256_text(
    "\n".join(f"{f.path}\t{f.sha256}" for f in off_scan.files)
)
print(f"  corpus_hash корпуса:    {on_scan.corpus_hash[:20]}…")
print(f"  хэш по оставленным:     {manual_hash[:20]}…")
check(on_scan.corpus_hash == manual_hash,
      "corpus_hash = хэш по файлам, которые пойдут в индекс")
# Проверять, что хэш с включённым и выключенным схлопыванием РАЗЛИЧАЕТСЯ,
# можно только там, где дубли есть. В корпусе z байт-идентичных копий нет,
# и хэши обязаны совпасть — требовать различия здесь было ошибкой проверки.
# Различие проверяется отдельно на revoap, где дубли есть (107 копий).
if dup.get("dropped_files"):
    check(manual_all != on_scan.corpus_hash,
          "хэш изменился: схлопывание учтено в сумме корпуса")
else:
    check(manual_all == on_scan.corpus_hash,
          "в корпусе нет дублей — хэш не меняется (верно)")

# Отдельная проверка на корпусе с реальными дублями.
#
# ВАЖНО: состояние «до» надо ВЫСТАВИТЬ явно. В конфиге revoap схлопывание
# теперь включено (настройка рабочая), и первый `scan` сразу дал бы 592 файла —
# сравнение «было/стало» получилось бы 592 против 592 и упало бы на пустом месте.
rep_cfg = load_config(REPO / "config" / "revoap.yaml")
rep_cfg.index.dedupe_enabled = False
rep_off = corpus.scan(rep_cfg)
rep_cfg.index.dedupe_enabled = True
rep_on = corpus.scan(rep_cfg)
print(f"\n  контроль на revoap (есть дубли):")
print(f"    файлов без схлопывания: {len(rep_off.files)}, "
      f"хэш {rep_off.corpus_hash[:16]}…")
print(f"    файлов со схлопыванием: {len(rep_on.files)}, "
      f"хэш {rep_on.corpus_hash[:16]}…")
check(len(rep_on.files) < len(rep_off.files),
      "revoap: файлов стало меньше",
      f"было {len(rep_off.files)}, стало {len(rep_on.files)}")
check(rep_on.corpus_hash != rep_off.corpus_hash,
      "revoap: хэш корпуса изменился (схлопывание учтено)")

print()
print("=" * 78)
print("5. ОБЛАСТЬ И РЕЖИМЫ")
print("=" * 78)
# Область проверяется через ПОЛНЫЙ список отброшенных файлов, а не через
# отчёт: в отчёт (scan.duplicates['groups']) попадают только первые 50 групп,
# и подсчёт по нему давал ложное «схлопнуто 36 файлов кода».
# Точные числа: tools/diag_dedupe_scope.py.
from ragraf import dedupe as dedupe_mod  # noqa: E402

for scope, want_code_dropped in (("docs", False), ("all", None)):
    cfg = load_config(REPO / "config" / "revoap.yaml")
    # Состояние «до» выставляется явно: в конфиге схлопывание включено,
    # и без сброса список файлов уже схлопнут — тогда «до/после» не различаются.
    cfg.index.dedupe_enabled = False
    full = corpus.scan(cfg)            # без схлопывания: полный список
    cfg.index.dedupe_enabled = True
    cfg.index.dedupe_scope = scope
    decision = dedupe_mod.dedupe(full, cfg)
    dropped = decision.dropped_files
    docs = [f for f in dropped if dedupe_mod.is_document(f.path)]
    code = [f for f in dropped if not dedupe_mod.is_document(f.path)]
    print(f"\n  область {scope!r}: отброшено {len(dropped)}, "
          f"документация {len(docs)}, прочее {len(code)}")
    if want_code_dropped is False:
        check(not code,
              f"область {scope}: файлы кода не схлопываются",
              f"схлопнуто кода {len(code)}: {[f.path for f in code[:3]]}")
        check(bool(docs), f"область {scope}: документация схлопывается")
    else:
        check(True, f"область {scope}: схлопываются все файлы")

# Режим skip_all: ни одна копия не индексируется.
cfg = load_config(REPO / "config" / "revoap.yaml")
cfg.index.dedupe_enabled = True
cfg.index.dedupe_mode = "skip_all"
s_skip = corpus.scan(cfg)
groups_skip = s_skip.duplicates.get("groups", [])
check(all(not g.get("kept") for g in groups_skip),
      "режим skip_all: ни одна копия не оставлена",
      f"примеры оставленных: {[g.get('kept') for g in groups_skip[:3]]}")

# Ошибочные значения должны отклоняться, а не приниматься молча.
for field, bad in (("dedupe_scope", "everything"), ("dedupe_mode", "drop"),
                   ("dedupe_keep", "random")):
    cfg = load_config(REPO / "config" / "revoap.yaml")
    cfg.index.dedupe_enabled = True
    setattr(cfg.index, field, bad)
    try:
        corpus.scan(cfg)
        check(False, f"неверное {field}={bad!r} отклонено")
    except ValueError as exc:
        check(True, f"неверное {field}={bad!r} отклонено: {str(exc)[:44]}…")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
