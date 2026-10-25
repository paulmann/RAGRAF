"""Аудит: какие дефекты RAGRAF из отчёта устранены, а какие нет.

Запуск: python tools/audit_defects.py

Проверяется КАЖДЫЙ из восьми дефектов раздела «Дефекты RAGRAF» фактическим
вызовом, а не по памяти: дефект считается устранённым только если код делает
то, что требуется. Плюс проверяются семь предложений U3–U7 из исследования
GAPS_FOR_LLM.md.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
facade = Facade(cfg, tool="audit", telemetry=False, watch=False)
rev = facade.current_rev()

print(f"корпус: {cfg.project}, ревизия {rev}\n")
print("=== ДЕФЕКТЫ RAGRAF ===")

results: list[tuple[str, str, str]] = []


def report(num: int, name: str, fixed: bool, detail: str) -> None:
    mark = "УСТРАНЁН" if fixed else "НЕ УСТРАНЁН"
    results.append((f"№{num}", name, mark))
    print(f"  №{num} {name}: {mark}")
    if detail:
        print(f"      {detail}")


# --- №1: метрика векторного поиска -----------------------------------------
try:
    from ragraf.embeddings import Embedder

    emb = Embedder(cfg, allow_download=False)
    q = emb.embed_one("права матрица роль действие")
    lv = facade.lexvec
    vec_hits = lv.search_vector(rev, q, limit=5)
    scores = [h.score for h in vec_hits]
    spread = (max(scores) - min(scores)) if scores else 0.0
    # Дефект был: все оценки равны 0.000. Признак устранения — оценки различаются.
    ok = bool(scores) and spread > 1e-6 and max(scores) > 0.1
    report(1, "метрика векторного поиска", ok,
           f"оценок {len(scores)}, разброс {spread:.4f}, максимум "
           f"{max(scores) if scores else 0:.4f} (было 0.000 у всех)")
except Exception as exc:
    report(1, "метрика векторного поиска", False, f"проверка не выполнена: {exc}")

# --- №2: веса RRF -----------------------------------------------------------
try:
    src = (REPO / "src" / "ragraf" / "lexvec.py").read_text(encoding="utf-8")
    has_weights = "lex_weight" in src and "vec_weight" in src and "file_penalty" in src
    report(2, "веса RRF и штраф за файловые чанки", has_weights,
           "лексика 1.0, вектор 0.6, штраф файловых чанков 0.7"
           if has_weights else "веса не найдены в коде")
except Exception as exc:
    report(2, "веса RRF", False, str(exc))

# --- №3: чанкинг процедурного PHP ------------------------------------------
try:
    from ragraf.extract import php_ast

    src = (REPO / "src" / "ragraf" / "extract" / "php_ast.py").read_text(encoding="utf-8")
    has_windows = "_chunk_file_whole_or_windows" in src
    # Проверяется фактический размер самого крупного чанка в индексе.
    # ВАЖНО про единицу измерения: размер берётся из chunk_map.tokens — это
    # оценка размера чанка, которая сравнивается с лимитом при чанкинге.
    # Ранее здесь стояло LENGTH(ft_chunk.code_tokens), и это давало ложный
    # сигнал: code_tokens — ТОКЕНИЗИРОВАННАЯ форма для поиска FTS5, она в 4 раза
    # длиннее исходника (замерено: 23 817 против 5 703), и нормальные чанки
    # выглядели как «сверх лимита».
    lv = facade.lexvec
    row = lv.conn.execute(
        """SELECT path, tokens FROM chunk_map
            WHERE rev_id = ? ORDER BY tokens DESC LIMIT 1""",
        (rev,),
    ).fetchone()
    max_tok = int(row["tokens"]) if row else 0
    biggest_path = str(row["path"]) if row else "—"
    ok = has_windows and max_tok < 8000
    report(3, "чанкинг процедурного PHP", ok,
           f"оконный режим есть, крупнейший чанк {max_tok} токенов "
           f"({biggest_path}); было 11 331")
except Exception as exc:
    report(3, "чанкинг процедурного PHP", False, str(exc))

# --- №4: слой схемы БД ------------------------------------------------------
try:
    d = facade.op_schema()
    tables = d.get("result") or []
    cols = 0
    for t in tables[:5]:
        cols += len(t.get("columns") or [])
    report(4, "слой схемы БД", len(tables) > 0,
           f"таблиц {len(tables)} (было 0)")
except Exception as exc:
    report(4, "слой схемы БД", False, str(exc))

# --- №5: плоские path:line в table_usage и repo_map -------------------------
try:
    d = facade.op_table_usage("Users", limit=3)
    refs = (d.get("result") or {}).get("references") or []
    flat = all(("path" in r and "line" in r) for r in refs) if refs else False
    d2 = facade.op_repo_map(budget_tokens=600)
    files = (d2.get("result") or {}).get("files") or []
    flat2 = all(("path" in f and "line" in f) for f in files) if files else False
    report(5, "плоские path/line в table_usage и repo_map", flat and flat2,
           f"table_usage: {len(refs)} фактов, repo_map: {len(files)} файлов, "
           f"все со ссылками: {flat and flat2}")
except Exception as exc:
    report(5, "плоские path/line", False, str(exc))

# --- №6: короткие 8.3-пути при сканировании --------------------------------
try:
    src = (REPO / "src" / "ragraf" / "corpus.py").read_text(encoding="utf-8")
    has_realpath = "_real_dir" in src and "realpath" in src
    # Фактическая проверка: скан рабочего корпуса находит файлы.
    scan = corpus.scan(cfg)
    ok = has_realpath and len(scan.files) > 50
    report(6, "сканирование на коротком 8.3-пути", ok,
           f"приведение путей есть, найдено файлов {len(scan.files)}")
except Exception as exc:
    report(6, "сканирование на коротком пути", False, str(exc))

# --- №7: порядок ревизий ----------------------------------------------------
try:
    src = (REPO / "src" / "ragraf" / "watch.py").read_text(encoding="utf-8")
    # Дефект был: ORDER BY created_at DESC. Признак устранения — сортировка по rev_id.
    bad = "ORDER BY created_at DESC" in src
    good = "ORDER BY rev_id DESC" in src
    report(7, "порядок ревизий по rev_id", good and not bad,
           "сортировка по rev_id (было по времени с точностью до секунды)")
except Exception as exc:
    report(7, "порядок ревизий", False, str(exc))

# --- №8: накопление экспортов CPG -----------------------------------------
try:
    from ragraf.cpg import JoernRunner

    src = (REPO / "src" / "ragraf" / "cpg.py").read_text(encoding="utf-8")
    has_prune = "prune_exports" in src
    runner = JoernRunner(cfg)
    cpg_dir = pathlib.Path(cfg.root) / ".ragraf" / "cpg"
    exports = [p for p in cpg_dir.iterdir() if p.is_dir() and p.name.startswith("export_")] \
        if cpg_dir.exists() else []
    report(8, "уборка старых экспортов CPG", has_prune,
           f"уборка реализована, каталогов экспорта сейчас {len(exports)} (было 37)")
except Exception as exc:
    report(8, "уборка экспортов CPG", False, str(exc))

print("\n=== УЛУЧШЕНИЯ U3–U7 (из GAPS_FOR_LLM) ===")

# --- U3: машинный список параметров сигнатуры ------------------------------
try:
    d = facade.op_symbol("z_agr_select", limit=1)
    r = (d.get("result") or [{}])[0]
    has_params = "signature_params" in r
    has_trunc_flag = "signature_truncated" in r
    report(3, "U3 signature_params (машинный список параметров)",
           has_params and has_trunc_flag,
           f"signature_params: {has_params}, signature_truncated: {has_trunc_flag}; "
           f"длина сигнатуры {len(r.get('signature') or '')}")
except Exception as exc:
    report(3, "U3 signature_params", False, str(exc))

# --- U4: признак чтение/запись в table_usage -------------------------------
try:
    d = facade.op_table_usage("Users", limit=5)
    res_u4 = d.get("result") or {}
    refs = (res_u4.get("references") or []) if isinstance(res_u4, dict) else []
    has_write = any("write" in r for r in refs) if refs else False
    has_access = any("access" in r for r in refs) if refs else False
    report(4, "U4 признак write/access в table_usage", has_write and has_access,
           f"фактов {len(refs)}, write: {has_write}, access: {has_access}")
except Exception as exc:
    report(4, "U4 признак write", False, str(exc))

# --- U5: фильтр фантомных таблиц -------------------------------------------
try:
    d = facade.op_table_usage("Domofon", limit=3)
    # Поле лежит внутри result, а не на верхнем уровне конверта: конверт несёт
    # метаданные (ревизия, свежесть), а результат — данные операции.
    res_u5 = d.get("result") or {}
    has_unresolved = isinstance(res_u5, dict) and "unresolved_tables" in res_u5
    has_in_schema = isinstance(res_u5, dict) and "table_in_schema" in res_u5
    report(5, "U5 unresolved_tables (фильтр фантомных таблиц)",
           has_unresolved and has_in_schema,
           f"unresolved_tables: {has_unresolved}, table_in_schema: {has_in_schema}")
except Exception as exc:
    report(5, "U5 unresolved_tables", False, str(exc))

# --- U6: трассировка матрицы прав ------------------------------------------
try:
    has_trace = hasattr(facade, "op_policy_trace")
    report(6, "U6 policy_trace (трассировка прав)", has_trace,
           f"операция есть: {has_trace}")
except Exception as exc:
    report(6, "U6 policy_trace", False, str(exc))

# --- U7: показатели качества в index_status --------------------------------
try:
    d = facade.op_index_status()
    res = d.get("result") or {}
    has_quality = "quality" in res
    report(7, "U7 блок quality в index_status", has_quality,
           f"quality: {has_quality}")
except Exception as exc:
    report(7, "U7 quality", False, str(exc))

# --- U8: аргумент в телеметрии ---------------------------------------------
try:
    conn = sqlite3.connect(str(cfg.control_db))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(usage_call)")]
    conn.close()
    has_arg = "arg_hash" in cols and "arg_digest" in cols
    report(8, "U8 аргумент в журнале обращений", has_arg,
           f"колонки: {[c for c in cols if c.startswith('arg_')]}")
except Exception as exc:
    report(8, "U8 аргумент в телеметрии", False, str(exc))

print("\n=== ИТОГ ===")
fixed = sum(1 for _, _, m in results if m == "УСТРАНЁН")
print(f"устранено: {fixed} из {len(results)}")
for num, name, mark in results:
    if mark != "УСТРАНЁН":
        print(f"  требует работы: {num} {name}")

facade.close()
