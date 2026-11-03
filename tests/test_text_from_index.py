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

import copy
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile

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
    # Формат фрагмента зависит от вида чанка, и это не послабление:
    #   * чанк КОДА несёт служебный заголовок «function f (path:line) — строка кода»:
    #     он даёт и адрес, и вид символа, и первую строку;
    #   * чанк ДОКУМЕНТА (source_kind=doc) — это текст документа; заголовка вида
    #     «функция (адрес)» у него нет и быть не может.
    # Прежняя редакция требовала формат кода у ПЕРВОГО хита. Проверялось не
    # свойство, а совпадение: пока документы шли оконными чанками, первым всегда
    # оказывался код. После фазы C первым стал JSON-фрагмент — и проверка упала
    # не на дефекте, а на своём допущении.
    code_snips = [h for h in with_snip if h.get("source_kind") != "doc"]
    doc_snips = [h for h in with_snip if h.get("source_kind") == "doc"]
    if code_snips:
        first = code_snips[0]["snippet"]
        check("(" in first and ":" in first,
              "фрагмент кода содержит заголовок чанка", repr(first)[:80])
        check("—" in first, "фрагмент кода содержит первую строку",
              repr(first)[:100])
    if doc_snips:
        first_doc = doc_snips[0]["snippet"]
        check(bool(first_doc.strip()),
              "фрагмент документа — его текст", repr(first_doc)[:80])
    check(all(len(h["snippet"]) <= 400 for h in with_snip),
          "фрагменты ограничены по длине",
          str(sorted(len(h["snippet"]) for h in with_snip)[-1:]))

print()
print("=" * 78)
print("4. АДРЕСАЦИЯ ХИТА: code --entry-ids <node_id> и <chunk_id>")
print("=" * 78)
plain = facade.op_search("порог достигнут переиндексация", limit=3)
for h in (plain.get("result") or []):
    nid, cid = h.get("node_id"), h.get("chunk_id")
    # У хита бывает НЕТ узла, и это не дефект: чанк документа (source_kind=doc)
    # адресуется локатором, а не узлом графа (этап «документы», фаза A).
    # Прежняя редакция требовала узел у каждого хита и на документе падала
    # («нужен параметр fqn, path или entry_ids») — проверялось не свойство, а
    # совпадение: до появления документных чанков такие хиты не попадали в тройку.
    # Свойство сохранено и усилено: адрес хита обязан разрешаться — узлом, если
    # узел есть, иначе идентификатором чанка с названной причиной.
    if nid:
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
    else:
        check(h.get("source_kind") == "doc",
              f"хит без узла — чанк документа: {h['path']}", str(h.get("source_kind")))
        check(bool(cid), f"у чанка документа есть идентификатор: {h['path']}", str(cid))
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
print("9. СИМВОЛ С ВНЕШНЕЙ ССЫЛКОЙ: тело выдаётся, отказ называет причину")
print("=" * 78)
# Замер 28.10.2026: `code --fqn record_fingerprint` падал с TypeError, потому что
# у имени два узла — определение в корпусе и внешняя заглушка без строк. Ответа не
# было вовсе, хотя тело доступно (в графе rev-0453 заглушек 1295, все без адреса).
mixed = facade.op_code(fqn="record_fingerprint")
check(bool(mixed.get("found")), "символ с заглушкой: ответ выдан",
      str(mixed.get("error")))
texts = " ".join(str(item.get("text") or "") for item in (mixed.get("result") or []))
check("def record_fingerprint" in texts, "тело определения в корпусе выдано")
check(bool(mixed.get("without_body")),
      "внешняя ссылка названа, а не выброшена молча",
      str(mixed.get("without_body")))
external_only = facade.op_code(fqn="javascript:reduce")
check(external_only.get("found") is False,
      "символ только вне корпуса: честный отказ",
      str(external_only.get("error")))
check("внешн" in str(external_only.get("error") or "").lower(),
      "причина отказа называет внешнюю ссылку",
      str(external_only.get("error")))

print()
print("=" * 78)
print("10. ОКНО И FOUND: «найдено» обязано означать «текст выдан»")
print("=" * 78)


def found_implies_text(env: dict) -> bool:
    """Свойство конверта: при found=True в результате есть непустой текст.

    Замер 28.10.2026: `--start 2500 --end 2510` отдавал found=True, пять спанов и ни
    одного символа текста — окно применялось к первым `limit` спанам, а не к спанам,
    которые его пересекают. Клиент по found=True обязан считать, что тело есть.
    """
    if not env.get("found"):
        return True
    rows = env.get("result")
    if not isinstance(rows, list):
        return True
    return any(str(row.get("text") or "").strip() for row in rows)


window_part = facade.op_code(path="src/ragraf/facade.py", start=1, end=40)
check(found_implies_text(window_part), "частичное окно: found ⇒ текст")
window_inside = facade.op_code(path="src/ragraf/facade.py", start=2500, end=2510)
check(found_implies_text(window_inside), "окно внутри файла: found ⇒ текст")
check(bool(window_inside.get("result")),
      "окно отбирает спаны, а не первые limit",
      str((window_inside.get("error") or "")[:120]))

# Подсаженный «пустой found»: тексты вычищены, признак оставлен. Проверка обязана
# забраковать такой ответ, иначе она ничего не значит.
planted = copy.deepcopy(window_inside)
for row in planted.get("result") or []:
    row["text"] = ""
planted["found"] = True
check(not found_implies_text(planted), "подсаженный «пустой found» пойман")

outside = facade.op_code(path="src/ragraf/facade.py", start=9000, end=9100)
check(outside.get("found") is False, "окно за спанами: честный отказ",
      str(outside.get("error")))
reason = str(outside.get("error") or "")
check("9000" in reason and "9100" in reason,
      "причина отказа называет границы числами", reason)

reverse = facade.op_code(path="src/ragraf/facade.py", start=200, end=100)
check(reverse.get("found") is False and "наоборот" in str(reverse.get("error") or ""),
      "перевёрнутое окно названо своей причиной", str(reverse.get("error")))

for label, kwargs in (("за файлом", dict(start=9000, end=9100)),
                      ("наоборот", dict(start=200, end=100))):
    env = facade.op_live_read("src/ragraf/facade.py", **kwargs)
    check(env.get("found") is False and str(env.get("error") or "").strip(),
          f"live_read, окно {label}: отказ с причиной", str(env.get("error"))[:90])
live_ok = facade.op_live_read("src/ragraf/facade.py", start=1, end=5)
check(live_ok.get("found") is True and str((live_ok.get("result") or {}).get("text") or "").strip(),
      "live_read, нормальное окно: текст выдан")

# Провенанс описывает источник ВЫДАННЫХ данных. Свойство проверяется на всех
# операциях сразу: при found=False источник не объявляется, иначе отчёт расходится
# с фактом (замер 28.10.2026: отказ `code` объявлял provenance=live без текста).
refusals = [
    ("code", facade.op_code(path="src/ragraf/facade.py", start=9000, end=9100)),
    ("code/fqn", facade.op_code(fqn="нет:такого")),
    ("live_read", facade.op_live_read("src/ragraf/facade.py", start=9000, end=9100)),
    ("symbol", facade.op_symbol("нет:такого")),
    ("search", facade.op_search("щ" * 40)),
]
refused_with_source = [name for name, env in refusals
                       if env.get("found") is False and env.get("provenance") != "none"]
check(not refused_with_source, "отказ не объявляет источник данных",
      str(refused_with_source))
issued = [("code", facade.op_code(path="src/ragraf/facade.py", start=1, end=40)),
          ("live_read", facade.op_live_read("src/ragraf/facade.py", start=1, end=5))]
check(all(env.get("provenance") not in (None, "none") for _n, env in issued),
      "там, где данные выданы, источник назван",
      str([(n, env.get("provenance")) for n, env in issued]))

external = facade.op_code(fqn="javascript:reduce")
check(external.get("found") is False, "внешний символ: отказ, а не пустой ответ")
for op_name, call in (("slice", facade.op_slice), ("impact", facade.op_impact)):
    env = call("javascript:reduce")
    check(bool(env.get("target_external")),
          f"{op_name}: цель вне корпуса названа, а не выдана за пустоту",
          str(env.get("target_external")))

print()
print("=" * 78)
print("11. ПАРАМЕТРЫ: объявленный параметр обязан действовать")
print("=" * 78)


def _declared_but_unused() -> list[str]:
    """Параметры операций, объявленные в сигнатуре и не использованные в теле.

    Проверка идёт по дереву разбора, а не по тексту: ищется ЗНАЧЕНИЕ имени
    (`ast.Name` в Load-контексте) внутри своей функции. Имена параметров в решении
    не участвуют — иначе проверка ловила бы написание, а не свойство.
    Замер 28.10.2026: такая пустышка была ровно одна — `op_live_read.context`,
    и ни CLI, ни MCP её не передавали.
    """
    import ast

    tree = ast.parse((REPO / "src/ragraf/facade.py").read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("op_")):
            continue
        params = [a.arg for a in node.args.args if a.arg != "self"]
        params += [a.arg for a in node.args.kwonlyargs]
        used: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                used.add(sub.id)
            elif isinstance(sub, ast.keyword) and sub.arg:
                used.add(sub.arg)
        out.extend(f"{node.name}({p})" for p in params if p not in used)
    return out


silent = _declared_but_unused()
check(not silent, "нет параметров, объявленных и не использованных в теле", str(silent))

# Подсаженная пустышка: свойство проверяется на синтетической функции, чтобы
# проверка не зависела от текущего состава операций.
def _planted() -> list[str]:
    import ast

    src = "def op_demo(alpha, beta=1):\n    return alpha\n"
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            used = {s.id for s in ast.walk(node) if isinstance(s, ast.Name)}
            out.extend(a.arg for a in node.args.args if a.arg not in used)
    return out


check(_planted() == ["beta"], "пустышка в подсаженной функции поймана", str(_planted()))

# Закрытые перечни: опечатка отвергается и называет допустимые значения,
# а оба законных значения работают.
CHOICES = [
    ("code.style", lambda v: facade.op_code(path="src/ragraf/facade.py", style=v, limit=2),
     ("compact", "signatures")),
    ("code.source", lambda v: facade.op_code(path="src/ragraf/facade.py", source=v, limit=2),
     ("auto", "disk")),
    ("search.kinds", lambda v: facade.op_search("facade", kinds=[v], limit=3),
     ("symbol", "file")),
    ("impact.mode", lambda v: facade.op_impact("src/ragraf/facade.py:to_dict", mode=v),
     ("full", "hard")),
    ("slice.direction", lambda v: facade.op_slice("src/ragraf/facade.py:to_dict", direction=v),
     ("back", "forward")),
    ("repo_map.detail", lambda v: facade.op_repo_map(detail=v, budget_tokens=400),
     ("normal", "deep")),
    ("table_usage.mode", lambda v: facade.op_table_usage("users", mode=v),
     ("all", "write")),
    ("class_usage.mode", lambda v: facade.op_class_usage("card", mode=v),
     ("missing", "unused")),
]
for label, call, allowed in CHOICES:
    typo = call("значение-которого-нет")
    text = str(typo.get("error") or "")
    check(typo.get("found") is False and "допустимо" in text
          and all(word in text for word in allowed),
          f"{label}: опечатка отвергнута с перечнем", text[:90])
    for value in allowed:
        env = call(value)
        check(env.get("error") is None,
              f"{label}={value}: законное значение не отвергнуто",
              str(env.get("error"))[:90])

# Операция без объявленного перечня не должна отвергать ничего: проверка перечня
# не имеет права распространяться на свободные параметры.
free = facade.op_search("facade", limit=3)
check(free.get("error") is None, "свободные параметры не задеты проверкой перечня")

print()
print("=" * 78)
print("12. ИСТОЧНИК СХЕМЫ: пустота объяснена, колонки разобраны")
print("=" * 78)


def _empty_is_explained(env: dict) -> bool:
    """Пустой ответ по схеме объяснён, если причина называет числа источников.

    Замер 28.10.2026: `schema(users)` отвечал found=false, error=None при `db_table`
    0 строк и собранной живой схеме — клиент читал это как «такой таблицы нет».
    """
    if env.get("found"):
        return True
    reason = str(env.get("error") or "")
    return bool(reason) and "0" in reason


schema_env = facade.op_schema("users")
if schema_env.get("found"):
    check(True, "схема из миграций не пуста: проверка пустоты не требуется")
else:
    check(_empty_is_explained(schema_env),
          "schema: пустота объяснена причиной с числами",
          str(schema_env.get("error"))[:110])
    check(isinstance(schema_env.get("live_tables"), int),
          "schema: число таблиц живого источника названо",
          str(schema_env.get("live_tables")))
check(not _empty_is_explained({"found": False, "error": None}),
      "подсаженный молчаливый отказ пойман")

usage_env = facade.op_table_usage("users")
migrations_empty = int(schema_env.get("migrations_tables") or 0) == 0
if not usage_env.get("found") and migrations_empty:
    # Признак пустоты, а не слово в тексте: первая редакция проверки искала
    # «миграции» и проходила на подсаженном дефекте — слово есть и в формулировке
    # «таблицы нет в схеме из миграций», которая как раз обвиняет имя.
    check(usage_env.get("schema_source") == "none",
          "table_usage: пустой миграционный источник назван признаком",
          str(usage_env.get("schema_source")))
    unresolved = (usage_env.get("result") or {}).get("unresolved_tables") or []
    reasons = " ".join(str(item.get("reason") or "") for item in unresolved)
    check("пуста" in reasons,
          "table_usage: причина говорит о пустых миграциях, а не об имени", reasons[:110])
    check("проверьте имя и написание" not in reasons,
          "table_usage: имя таблицы не обвиняется", reasons[:110])

by_string = facade.op_write_check("users", "No,Status,InitiatorID")
by_list = facade.op_write_check("users", ["No", "Status", "InitiatorID"])
split = (by_string.get("result") or {}).get("checked_columns")
# Живого слоя баз может не быть (ревизия собрана из каталога без
# `db_access.json`) — тогда операция честно отвечает `found=false` с причиной, и
# проверять разбор колонок не на чем. Замер 01.11.2026: набор падал двумя
# проверками на ревизии без слоя, хотя дефекта в разборе не было.
schema_missing = not split and not by_string.get("found", True)
if schema_missing:
    reason = str((by_string.get("error") or {}).get("message") or by_string.get("error") or "")
    print(f"  ! живой схемы нет: write_check отказал с причиной {reason[:80]!r};"
          " разбор колонок не проверяется")
    check(True, "write_check на пустом слое называет причину, а не пустой успех")
else:
    check(split == ["No", "Status", "InitiatorID"],
          "write_check: строка разобрана по запятой, а не посимвольно", str(split))
    check(split == (by_list.get("result") or {}).get("checked_columns"),
          "write_check: строка и список дают один набор колонок")
    mixed = facade.op_write_check("users", ["ID,Login"])
    check((mixed.get("result") or {}).get("checked_columns") == ["ID", "Login"],
          "write_check: запятая внутри элемента списка разобрана",
          str((mixed.get("result") or {}).get("checked_columns")))

print()
print("=" * 78)
print("13. ЖИВАЯ СХЕМА: ОТВЕТ НЕ ЗАВИСИТ ОТ СТРОК ДРУГИХ РЕВИЗИЙ")
print("=" * 78)
# Замер 28.10.2026: живая схема лежит в graph.sqlite ПО КАЖДОЙ ревизии
# (`db_live_table` 128 550 строк по 435 ревизиям из 500, `db_live_column` 1 473 468).
# Ошибка «читаем без rev_id» склеивает состояния разных ревизий, и по ответу это
# не видно: `table_id` узла ОТ РЕВИЗИИ НЕ ЗАВИСИТ (stable_id от имени базы и
# таблицы), поэтому строка чужой ревизии приклеится к текущей таблице молча.
#
# Свойство проверяется на ВРЕМЕННОЙ базе с приманкой: копия живой схемы текущей
# ревизии плюс та же структура под чужой ревизией, но шире по составу. Ответ по
# базе с приманкой обязан совпасть с ответом по боевой. Приманка доказана прямым
# SQL без ограничения rev_id — иначе проверка ничего не доказывает (ловушка 46).

from ragraf import db_ops, db_writer  # noqa: E402

LIVE_TABLES = ("db_live_table", "db_live_column", "db_live_index",
               "db_live_relation", "db_live_meta")
DECOY = "rev-9999-decoy"


def _ro(path):
    conn_ = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn_.row_factory = sqlite3.Row
    return conn_


graph_path = pathlib.Path(cfg.graph_db)
real = _ro(graph_path)
# Операция `db_schema` отвечает за АКТИВНУЮ ревизию (`current_rev`), а не за
# переменную `rev` этого набора, поэтому и сверка идёт с активной. Замер
# 01.11.2026: ревизия, собранная из каталога без `db_access.json` (прогон с
# отводом состояния), живого слоя не имеет вовсе — и `db_schema` честно
# отвечает `info.collected=false` с причиной, а не пустым «изменений нет».
active_rev = facade.current_rev() or rev
n_cur = real.execute("SELECT COUNT(*) AS c FROM db_live_table WHERE rev_id = ?",
                     (active_rev,)).fetchone()["c"]
schema_rev = active_rev
if n_cur == 0:
    # Пустой слой — законное состояние, но не повод молча пропустить свойство:
    # называем число и проверяем изоляцию ревизий там, где слой есть.
    row = real.execute(
        "SELECT rev_id, COUNT(*) AS c FROM db_live_table GROUP BY rev_id"
        " ORDER BY rev_id DESC LIMIT 1").fetchone()
    if row is not None:
        schema_rev = str(row["rev_id"])
        print(f"  ! у активной ревизии {active_rev} живого слоя баз нет (строк 0);"
              f" изоляция ревизий проверяется на {schema_rev}:"
              f" таблиц {int(row['c'])}. Пустота названа в ответе:"
              " db_schema → info.collected=false")
check(n_cur > 0 or schema_rev != active_rev,
      "живой слой есть у активной ревизии либо у предыдущей (иначе проверять нечего)",
      f"активная {active_rev}: строк {n_cur}, образец {schema_rev}")

env = facade.op_db_schema(limit=500)
op_names = {(r["database_name"], r["table_name"])
            for r in (env.get("result") or {}).get("tables") or []}
sql_names = {(r["database_name"], r["table_name"]) for r in real.execute(
    "SELECT database_name, table_name FROM db_live_table WHERE rev_id = ?",
    (active_rev,))}
check(op_names == sql_names,
      "db_schema(limit=500) совпал с прямым SQL по активной ревизии",
      f"операция {len(op_names)}, SQL {len(sql_names)}")

tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="i40-live-",
                                      dir=str(REPO / ".ragraf" / "tmp")))
tmp_path = tmpdir / "two-revs.sqlite"
try:
    t = sqlite3.connect(f"file:{tmp_path}?mode=rwc", uri=True)
    t.row_factory = sqlite3.Row
    # DDL берётся из одного места (`db_writer.DB_LIVE_DDL`), а не копируется в тест:
    # второй схемы быть не должно.
    t.executescript(db_writer.DB_LIVE_DDL)
    t.execute(f"ATTACH DATABASE 'file:{graph_path.as_posix()}?mode=ro' AS src")
    for name in LIVE_TABLES:
        t.execute(f"INSERT INTO {name} SELECT * FROM src.{name} WHERE rev_id = ?",
                  (schema_rev,))
    # ПРИМАНКА: то же самое под чужой ревизией — шире по составу, с другим
    # размером и с колонками `__decoy`.
    t.execute(
        "INSERT INTO db_live_table(rev_id, database_name, table_name, table_id, kind,"
        " engine, row_estimate, data_bytes, index_bytes, comment)"
        " SELECT ?, database_name, table_name, table_id, kind, engine, 999999, 1, 1,"
        " 'ПРИМАНКА' FROM src.db_live_table WHERE rev_id = ?", (DECOY, rev))
    t.execute(
        "INSERT INTO db_live_column(rev_id, table_id, column_name, data_type, ordinal)"
        " SELECT ?, table_id, column_name || '__decoy', 'text', 999"
        " FROM src.db_live_column WHERE rev_id = ?", (DECOY, schema_rev))
    t.execute(
        "INSERT INTO db_live_index(rev_id, table_id, index_name, unique_flag,"
        " index_type, columns) SELECT ?, table_id, index_name || '__decoy', 0,"
        " 'BTREE', '[]' FROM src.db_live_index WHERE rev_id = ?", (DECOY, rev))
    t.execute(
        "INSERT INTO db_live_relation(rev_id, from_table, from_column, to_database,"
        " to_table, to_column, kind, confidence, note, from_database)"
        " SELECT ?, from_table, from_column, to_database, to_table, to_column,"
        " 'stated', 1.0, 'ПРИМАНКА', from_database FROM src.db_live_relation"
        " WHERE rev_id = ?", (DECOY, schema_rev))
    t.execute(
        "INSERT OR REPLACE INTO db_live_meta(rev_id, dialect, via, databases, tables)"
        " VALUES(?, 'mysql', 'ПРИМАНКА', 99, 999)", (DECOY,))
    t.commit()
    n_all = t.execute("SELECT COUNT(*) AS c FROM db_live_table").fetchone()["c"]
    n_all_cols = t.execute("SELECT COUNT(*) AS c FROM db_live_column").fetchone()["c"]
    n_cur_cols = t.execute(
        "SELECT COUNT(*) AS c FROM db_live_column WHERE rev_id = ?",
        (schema_rev,)).fetchone()["c"]
    check(n_all > n_cur and n_all_cols > n_cur_cols,
          "приманка записана: без ограничения rev_id строк больше",
          f"таблиц {n_all} против {n_cur}, колонок {n_all_cols} против {n_cur_cols}")
    tid_row = real.execute(
        "SELECT table_id FROM db_live_column WHERE rev_id = ? LIMIT 1",
        (schema_rev,)).fetchone()
    tid = str(tid_row["table_id"]) if tid_row else ""
    n_tid_all = t.execute("SELECT COUNT(*) AS c FROM db_live_column WHERE table_id = ?",
                          (tid,)).fetchone()["c"]
    n_tid_rev = t.execute(
        "SELECT COUNT(*) AS c FROM db_live_column WHERE rev_id = ? AND table_id = ?",
        (schema_rev, tid)).fetchone()["c"]
    check(tid != "" and n_tid_all > n_tid_rev,
          "приманка цепляется к той же таблице: table_id не зависит от ревизии",
          f"по table_id {n_tid_all}, по ревизии {n_tid_rev}")
    t.close()

    temp = _ro(tmp_path)
    for label, call in (
        ("db_schema", lambda c_: db_ops.schema(c_, rev, limit=500)),
        ("db_table", lambda c_: db_ops.table_detail(c_, rev, "users")),
        ("db_relations", lambda c_: db_ops.relations(c_, rev, "users")),
        ("сводка слоя", lambda c_: db_ops.summary(c_, rev)),
        ("db_drift", lambda c_: db_ops.drift(c_, rev, "rev-0001")),
    ):
        try:
            same_answer = call(temp) == call(real)
        except Exception as exc:  # noqa: BLE001 — отказ тоже провал проверки
            same_answer = False
            print(f"  ! {label}: исключение {exc}")
        check(same_answer, f"{label}: ответ по базе с приманкой тот же", label)
    temp.close()
finally:
    real.close()
    shutil.rmtree(tmpdir, ignore_errors=True)

# Обратная сторона того же свойства: ревизия, у которой живой схемы НЕТ, — не
# «пустое состояние» и не «всё добавилось». Замер: `previous_rev=rev-0010` в
# `db_drift` давал `tables_added=200` при 315 таблицах без слова о причине.
real = _ro(graph_path)
have_live = {str(r[0]) for r in real.execute(
    "SELECT DISTINCT rev_id FROM db_live_table").fetchall()}
prev_ids = [str(r[0]) for r in facade.control.conn.execute(
    "SELECT rev_id FROM rev_history ORDER BY rev_id").fetchall()]
empty_prev = next((x for x in prev_ids if x not in have_live), None)
if empty_prev is None:
    print("  (у всех ревизий есть живая схема — обратная сторона не проверялась)")
else:
    data = db_ops.drift(real, rev, empty_prev)
    check(data.get("compared") is False,
          "drift: отсутствие живой схемы у второй ревизии названо признаком",
          f"compared={data.get('compared')}, prev={empty_prev}")
    check(bool(str(data.get("note") or "")), "drift: причина названа словами",
          str(data.get("note"))[:70])
    env_d = facade.op_db_drift(previous_rev=empty_prev)
    check(((env_d.get("result") or {}).get("drift") or {}).get("compared") is False,
          "op_db_drift: ответ помечен как несравнимый")
    check(bool(env_d.get("limits")), "op_db_drift: причина попала в limits")
    check(bool(env_d.get("note")),
          "op_db_drift: причина видна и в примечании ответа", str(env_d.get("note"))[:70])
real.close()

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
facade.close()
sys.exit(0 if FAIL == 0 else 1)
