"""Проверка главного риска: не зациклится ли сторож свежести.

Риск, который надо исключить до сдачи. Если `corpus_hash` при индексации
и при проверке свежести считается по РАЗНЫМ наборам файлов, значения не
совпадут, и сторож решит, что корпус изменился. Он запустит переиндексацию,
та заново запишет несогласованный хэш — и так по кругу, бесконечно.

Проверяется воспроизводимость:
  1. два подряд вызова `corpus.scan` при включённом схлопывании дают
     одинаковый набор файлов и одинаковый хэш;
  2. хэш не зависит от порядка обхода каталога (проверяется повторным
     сканом);
  3. хэш после индексации совпадает с хэшем при проверке;
  4. выбор главной копии детерминирован.

Ничего не меняет: только читает.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, dedupe, store, watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

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


print("=" * 78)
print("1. ВОСПРОИЗВОДИМОСТЬ: ДВА СКАНА ПОДРЯД")
print("=" * 78)
cfg = load_config(REPO / "config" / "revoap.yaml")
print(f"  схлопывание: включено={cfg.index.dedupe_enabled}, "
      f"область={cfg.index.dedupe_scope}")

first = corpus.scan(cfg)
second = corpus.scan(cfg)
print(f"  скан 1: файлов {len(first.files)}, хэш {first.corpus_hash[:20]}…")
print(f"  скан 2: файлов {len(second.files)}, хэш {second.corpus_hash[:20]}…")

check(first.corpus_hash == second.corpus_hash,
      "хэш корпуса воспроизводим (два скана дают одно значение)")
check([f.path for f in first.files] == [f.path for f in second.files],
      "состав файлов воспроизводим")
check(first.duplicates.get("dropped_files") ==
      second.duplicates.get("dropped_files"),
      "число отброшенных копий воспроизводимо")
check([g["kept"] for g in first.duplicates.get("groups", [])] ==
      [g["kept"] for g in second.duplicates.get("groups", [])],
      "выбор главной копии детерминирован")

print()
print("=" * 78)
print("2. ВЫБОР НЕ ЗАВИСИТ ОТ ПОРЯДКА ВХОДА")
print("=" * 78)
# Файлы перемешиваются и схлопывание считается заново. Если выбор главной
# копии зависит от порядка, результат изменится — и это дефект.
import random  # noqa: E402

shuffled = corpus.CorpusScan(
    files=list(first.files),
    files_found=first.files_found,
)
# Перемешивается только для проверки: настоящий скан уже отсортирован.
random.Random(42).shuffle(shuffled.files)
decision_a = dedupe.dedupe(shuffled, cfg)
random.Random(7).shuffle(shuffled.files)
decision_b = dedupe.dedupe(shuffled, cfg)

kept_a = sorted(f.path for f in decision_a.kept_files)
kept_b = sorted(f.path for f in decision_b.kept_files)
print(f"  оставлено файлов: {len(kept_a)} и {len(kept_b)}")
check(kept_a == kept_b,
      "состав оставленных файлов не зависит от порядка обхода",
      f"различий: {len(set(kept_a) ^ set(kept_b))}")

keeps_a = sorted(g.kept for g in decision_a.groups)
keeps_b = sorted(g.kept for g in decision_b.groups)
check(keeps_a == keeps_b,
      "выбранные главные копии не зависят от порядка")

print()
print("=" * 78)
print("3. СТОРОЖ СВЕЖЕСТИ: ЧТО ОН ВИДИТ")
print("=" * 78)
# Считается то же, что считает сторож, и сравнивается с последней ревизией.
# auto_reindex=False — важно: проверка не должна сама запускать индексацию,
# иначе она не наблюдает, а меняет состояние.
fresh = watch.check(cfg, auto_reindex=False)
print(f"  изменилось с индексации: {fresh.changed}")
print(f"  устарело (stale):        {fresh.stale}")
print(f"  корпус сейчас:  {str(fresh.corpus_hash)[:20]}…")
print(f"  в индексе:      {str(fresh.expected_hash)[:20]}…")
print(f"  ревизия:        {fresh.rev_id}")

conn = store.connect(cfg.graph_db, readonly=True)
row = conn.execute(
    "SELECT rev_id, corpus_hash, file_count FROM rev "
    "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()
conn.close()
if row:
    print(f"\n  последняя ревизия {row['rev_id']}: файлов {row['file_count']}")
    print(f"  её corpus_hash: {str(row['corpus_hash'])[:20]}…")
    check(str(row["corpus_hash"]) == first.corpus_hash,
          "хэш в ревизии = хэш текущего скана (сторож не зациклится)",
          f"в ревизии {str(row['corpus_hash'])[:12]}…, "
          f"сейчас {first.corpus_hash[:12]}…")
    check(row["file_count"] == len(first.files),
          "число файлов в ревизии = число в скане",
          f"в ревизии {row['file_count']}, в скане {len(first.files)}")
    # Главная проверка риска: сторож не считает корпус изменившимся.
    check(not fresh.changed,
          "сторож не считает корпус изменившимся (зацикливания нет)",
          f"changed={fresh.changed}, stale={fresh.stale}")
else:
    check(False, "в корпусе есть готовая ревизия")

print()
print("=" * 78)
print("4. ПРОВЕРКА НЕСКОЛЬКИХ РАЗ ПОДРЯД: НЕ ДРОЖИТ ЛИ ХЭШ")
print("=" * 78)
hashes = []
for i in range(3):
    s = corpus.scan(cfg)
    hashes.append(s.corpus_hash)
    print(f"  скан {i + 1}: {s.corpus_hash[:20]}…")
check(len(set(hashes)) == 1, "три скана дали один хэш",
      f"различных значений: {len(set(hashes))}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
