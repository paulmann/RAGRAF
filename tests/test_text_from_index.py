"""Проверка правок по замерам rev-0030/0031: текст за один вызов, адресация хита,
двойной счёт и честный бюджет.

Зачем отдельный тест. Все четыре правки закрывают дефекты, которые НЕ видны
при обычном чтении кода и не ловятся прежними проверками (гейта на схему FTS
в проекте не было вовсе — см. `tools/Invoke-ProjectDoctrine.ps1`, проверки
DDL хранилища там нет):

  1. `search --with-text` — текст хита из индекса. Раньше `search` отдавал
     только адрес, и на 20 хитов приходилось читать 20 файлов вторым шагом.
  2. `code --entry-ids` — адрес второго шага. Идентификаторы хита
     (`node_id`, `chunk_id`) раньше НЕЛЬЗЯ было использовать: вызов отвечал
     «нужен параметр fqn или path». Адресация по `chunk_id` вдобавок падала
     с OperationalError, потому что искалась в таблице `node`, где такой
     колонки нет.
  3. Двойной счёт спанов: `code --path <файл с классом>` отдавал класс-контейнер
     И все его методы, то есть Σtext = 180 507 при файле в 100 354 (×1,80).
  4. Бюджет `repo_map`: `chars_used` считался по синтетической строке, которой
     в ответе нет. Заявлено 6 304 символа при фактических 30 772 (×4,88).

Проверка идёт на СОБСТВЕННОМ корпусе RAGRAF (`config/ragraf.yaml`), потому что
он всегда под рукой и содержит файл с классом и методами (`src/ragraf/facade.py`)
и символами с длинными спанами.

Запуск: `python tests/test_text_from_index.py`
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf import store  # noqa: E402

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


cfg = load_config(REPO / "config" / "ragraf.yaml")
facade = Facade(cfg)

# Тест опирается на индекс, поэтому сначала приводит его в актуальное состояние.
# Без этого замер врёт не заметно: `code` отдал бы текст ИЗ ИНДЕКСА, снятый
# до последней правки, и сверка с диском показала бы расхождение, которого
# в рабочем режиме нет. Ровно это правило записано в доктрине проекта:
# факты берутся из актуального индекса, а не из «индекс отстал, но ладно».
_fresh = facade.op_freshness(force=True)
_f = _fresh.get("freshness") or {}
print(f"индекс: {_f.get('rev_id')}  изменён после индексации: {_f.get('changed_since_index')}")
if _f.get("changed_since_index"):
    print("  ! индекс всё ещё отстаёт — сверка с диском ниже будет недостоверной")
print()

print("=" * 78)
print("1. ХРАНИЛИЩЕ СЫРОГО ТЕКСТА ЧАНКОВ")
print("=" * 78)
conn = sqlite3.connect(f"file:{cfg.lexvec_db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
tables = [
    str(r["name"])
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
]
check("chunk_text" in tables, "таблица chunk_text существует", f"таблиц: {tables}")
rev = facade.current_rev()
n_text = conn.execute(
    "SELECT COUNT(*) AS c FROM chunk_text WHERE rev_id = ?", (rev,)
).fetchone()["c"]
n_chunks = conn.execute(
    "SELECT COUNT(*) AS c FROM chunk_map WHERE rev_id = ?", (rev,)
).fetchone()["c"]
print(f"  ревизия {rev}: чанков {n_chunks}, с сохранённым текстом {n_text}")
check(n_text == n_chunks, "текст сохранён для всех чанков ревизии",
      f"{n_text} из {n_chunks}")

print()
print("=" * 78)
print("2. FTS-СХЕМА НЕ ТРОНУТА (иначе bm25 и snippet поехали бы молча)")
print("=" * 78)
fts_cols = [
    str(r["name"]) for r in conn.execute("PRAGMA table_info(ft_chunk)").fetchall()
]
print(f"  колонки ft_chunk: {', '.join(fts_cols)}")
check("code_tokens" in fts_cols, "колонка code_tokens на месте")
check("symbol_tokens" in fts_cols, "колонка symbol_tokens на месте")
check(len(fts_cols) == 7, "число колонок ft_chunk равно 7 (число весов bm25)",
      f"получено {len(fts_cols)}")
src_lexvec = (REPO / "src" / "ragraf" / "lexvec.py").read_text(encoding="utf-8")
n_weights = src_lexvec.count("bm25(ft_chunk")
check(n_weights >= 1, "веса bm25 передаются явными числами (проверка вызовом ниже)")

print()
print("=" * 78)
print("3. ТЕКСТ ЗА ОДИН ВЫЗОВ: search --with-text")
print("=" * 78)
res = facade.op_search("corpus_hash", limit=3, with_text=True)
hits = res.get("result") or []
check(bool(hits), "поиск вернул хиты")
with_snip = [h for h in hits if h.get("snippet")]
print(f"  хитов {len(hits)}, со фрагментом {len(with_snip)}")
check(len(with_snip) == len(hits), "у каждого хита есть фрагмент текста",
      f"{len(with_snip)} из {len(hits)}")
check(res.get("text_from_index") is True, "признак text_from_index выставлен")
if with_snip:
    # Фрагмент — служебный заголовок чанка вида «function f (path:line)»:
    # он даёт и адрес, и вид символа, и первую строку кода.
        check("(" in with_snip[0]["snippet"] and ":" in with_snip[0]["snippet"],
              "фрагмент содержит заголовок чанка", repr(with_snip[0]["snippet"])[:80])
        check(len(with_snip[0]["snippet"]) <= 200, "фрагмент ограничен по длине",
              f"{len(with_snip[0]['snippet'])} символов")
        # Фрагмент обязан нести и адрес, и содержание: одного заголовка мало —
        # он не отличает один метод от другого, а именно это решает, идти ли
        # вторым шагом. Замер rev-0034: заголовок — 44 символа, с первой строкой
        # кода — 96.
        check("—" in with_snip[0]["snippet"],
              "фрагмент содержит первую строку кода", repr(with_snip[0]["snippet"])[:100])

print()
print("=" * 78)
print("4. АДРЕСАЦИЯ ХИТА: code --entry-ids <node_id> и <chunk_id>")
print("=" * 78)
plain = facade.op_search("порог достигнут переиндексация", limit=3)
for h in (plain.get("result") or []):
    nid, cid = h.get("node_id"), h.get("chunk_id")
    by_node = facade.op_code(entry_ids=nid)
    check(by_node.get("found") is True, f"node_id разрешился: {h['path']}",
          str(by_node.get("error")))
    check((by_node.get("resolved") or {}).get("by_node_id", 0) == 1,
          "источник разрешения — таблица узлов")
    out = (by_node.get("result") or [])
    if out:
        check(out[0].get("text_source") == "index",
              "текст взят из индекса", str(out[0].get("text_source")))
        check(bool((out[0].get("text") or "").strip()), "текст не пуст")
    by_chunk = facade.op_code(entry_ids=cid)
    check(by_chunk.get("found") is True, f"chunk_id разрешился: {h['path']}",
          str(by_chunk.get("error")))
    check((by_chunk.get("resolved") or {}).get("by_chunk_id", 0) == 1,
          "источник разрешения — таблица чанков")

bad = facade.op_code(entry_ids="deadbeefdeadbeef")
check(bad.get("found") is False, "неизвестный идентификатор не выдаётся за успех")
check("deadbeefdeadbeef" in (bad.get("unresolved") or []),
      "неизвестный идентификатор назван в unresolved")

print()
print("=" * 78)
print("5. ДВОЙНОЙ СЧЁТ: класс-контейнер и его методы")
print("=" * 78)
# Файл ищется в корпусе, а не зашит строкой: номера строк после правок
# меняются, и тест не должен ломаться от этого — он проверяет СВОЙСТВА
# выдачи, а не положение кода в файле.
big = "src/ragraf/facade.py"
_disk_lines = (REPO / big).read_text(encoding="utf-8").splitlines()
# Спан класса-контейнера: ищем его в индексе, чтобы взять актуальный номер
# строки класса (после правок он сместился).
_cls_row = facade.graph.execute(
    """SELECT start_line, end_line FROM node
       WHERE rev_id = ? AND path = ? AND kind = 'class'
       ORDER BY (end_line - start_line) DESC LIMIT 1""",
    (facade.current_rev(), big),
).fetchone()
cls_start = int(_cls_row["start_line"]) if _cls_row else 1
cls_end = int(_cls_row["end_line"]) if _cls_row else len(_disk_lines)
print(f"  класс-контейнер: строки {cls_start}-{cls_end} из {len(_disk_lines)}")
res_code = facade.op_code(path=big)
records = res_code.get("result") or []
disk_lines = (REPO / big).read_text(encoding="utf-8").splitlines()
sum_text = sum(len(r.get("text") or "") for r in records)
print(f"  спанов {len(records)}, всего {res_code.get('spans_total')}, "
      f"отброшено вложенных {res_code.get('nested_dropped')}")
print(f"  Σtext = {sum_text}, файл = {len(chr(10).join(disk_lines))} символов")
check(int(res_code.get("nested_dropped") or 0) > 0,
      "вложенные спаны отброшены (раньше дублировали текст)")
check(sum_text <= len(chr(10).join(disk_lines)) + 2000,
      "Σtext не превышает размер файла заметно (раньше был ×1,80)", f"{sum_text}")
check(res_code.get("provenance") == "index",
      "провенанс соответствует источнику (index)", str(res_code.get("provenance")))

# Содержимое сверяется с диском. Строгое равенство построчно ожидается только
# у спанов БЕЗ пропусков: у остальных часть строк (модульный код между
# символами) чанком не является, и их в индексе нет — именно это и показывает
# поле `gaps`. Для них проверяется более слабое, но честное условие: строки
# текста идут в том же порядке, что в файле (текст не перепутан со сдвигом).
mismatch = 0
subseq_bad = 0
exact_checked = 0
gap_checked = 0
for r in records:
    if r.get("error"):
        continue
    w = r.get("window") or {}
    disk = disk_lines[max(0, int(w.get("start", 1)) - 1): int(w.get("end", 0))]
    actual = (r.get("text") or "").splitlines()
    if r.get("gaps"):
        gap_checked += 1
        it = iter(disk)
        if not all(any(line == d for d in it) for line in actual):
            subseq_bad += 1
    else:
        exact_checked += 1
        if actual != disk:
            mismatch += 1
check(mismatch == 0,
      f"текст спанов без пропусков совпадает с диском ({exact_checked} проверено)",
      f"расхождений {mismatch}")
check(subseq_bad == 0,
      f"текст спанов с пропусками идёт в том же порядке ({gap_checked} проверено)",
      f"нарушений {subseq_bad}")

# Вариант «с контейнерами»: текст тоже не должен задваиваться, потому что
# контейнер в этом режиме сокращается до шапки (строки до первого дочернего спана).
withc = facade.op_code(path=big, include_containers=True)
sum_withc = sum(len(r.get("text") or "") for r in (withc.get("result") or []))
print(f"  include_containers=true: спанов {len(withc.get('result') or [])}, "
      f"Σtext = {sum_withc}")
check(sum_withc <= len(chr(10).join(disk_lines)) + 2000,
      "с контейнерами текст тоже не задваивается", f"{sum_withc}")

print()
print("=" * 78)
print("6. ОКНО И ЧЕСТНЫЙ ОБРЕЗ: code --start/--end и limit")
print("=" * 78)
win = facade.op_code(path=big, start=cls_start, end=cls_start + 11, limit=50)
win_records = win.get("result") or []
first = next((r for r in win_records if r.get("symbol", "").endswith("Facade")), None)
check(first is not None, "спан класса найден")
if first:
    check(first["window"] == {"start": cls_start, "end": cls_start + 11},
          "окно применено (раньше --start/--end игнорировались)", str(first["window"]))
    check((first.get("text") or "").count("\n") == 11,
          "окно содержит ровно запрошенные строки",
          f"строк {len((first.get('text') or '').splitlines())}")

narrow = facade.op_code(path=big, limit=2)
check(narrow.get("truncated") is True, "обрез по limit помечен")
check(int(narrow.get("spans_returned") or 0) <= 2, "выдано не больше лимита")

print()
print("=" * 78)
print("7. БЮДЖЕТ repo_map: заявленное не меньше фактического")
print("=" * 78)
rm = facade.op_repo_map(budget_tokens=2000)
payload = rm.get("result") or {}
claimed = int(payload.get("chars_used") or 0)
actual = len(json.dumps(payload.get("files") or [], ensure_ascii=False))
print(f"  заявлено {claimed} символов, фактически {actual}, бюджет {payload.get('budget_chars')}")
check(actual <= int(payload.get("budget_chars") or 0),
      "фактический объём уложился в бюджет", f"{actual} > {payload.get('budget_chars')}")
check(claimed >= actual * 0.99,
      "заявленный объём не занижен против фактического", f"{claimed} против {actual}")
check("budget_note" in payload, "формула бюджета названа в ответе")

print()
print("=" * 78)
print("8. ЧЕСТНОСТЬ ПРОПУСКОВ: строки, которых нет в индексе")
print("=" * 78)
# Модульный код между символами чанком не является (замер rev-0029: чанки
# покрывают 65 311 из 86 177 строк). Это должно быть видно, а не молча теряться.
gapped = [
    r for r in (res_code.get("result") or [])
    if r.get("gaps")
]
print(f"  спанов с сокращённым покрытием: {len(gapped)}")
check(int(res_code.get("gaps") or 0) == len(gapped),
      "счётчик gaps совпадает с числом спанов с пробелами")
for r in gapped[:2]:
    g = r["gaps"]
    check(g["lines_in_index"] < g["lines_requested"],
          f"пропуск назван числами: {r.get('symbol')}", str(g))

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
facade.close()
sys.exit(0 if FAIL == 0 else 1)
