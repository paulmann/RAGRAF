"""Почему ребро потеряло цель: замер причины, а не только числа.

ЗАЧЕМ. Этап 8 закрылся числом «249 рёбер ведут в никуда» и строкой в `limits`.
Число названо, причина — нет, поэтому «потеря цели» осталась бы мнением:
неизвестно, заглушка не создана, удалена при разрешении имён или цель
переименована. Три причины требуют трёх разных исправлений, и выбрать одну
без замера значило бы править наугад.

ЧТО КЛАССИФИЦИРУЕТСЯ (по одной ревизии, из самого хранилища):

  stub_missing    адрес-заглушка (`@ext:` …), и узла с таким id не было НИ В
                  ОДНОЙ ревизии хранилища: заглушка не создаётся вовсе.
  stub_dropped    узел-заглушка существовал в прежней ревизии, а в этой его
                  нет: заглушка удалена (разрешение имён или ротация).
  target_renamed  узел с тем же именем в ревизии ЕСТЬ, но ребро на него не
                  перепривязано: цель переименована или переехала.
  edge_invented   ребро не должно было возникать: разобрано из литерала,
                  который не является запросом (проза со словом `update`).
  unknown         ни одно правило не подошло: адрес надо открыть и посмотреть.

Плюс к причине называется ПРОИЗВОДИТЕЛЬ — код, который ребро пишет, — и
удерживающий его резолвер. Без этого «исправить» некуда адресовать.

Запуск:
    python tools/measure_dangling_edges.py                 # текст по активной
    python tools/measure_dangling_edges.py --json --save f.json
    python tools/measure_dangling_edges.py --rev rev-0925  # конкретная ревизия
    python tools/measure_dangling_edges.py --assert-zero   # код 1, если есть
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Кто пишет ребро такого вида. Ключ — вид ребра, значение — код-производитель.
PRODUCERS: dict[str, str] = {
    "calls": "extract/* (литерал вызова) → indexer._resolve_external_names",
    "calls_heuristic": "extract/* (вызов по динамическому имени) → indexer._resolve_external_names",
    "calls_builtin": "extract/php_ast (встроенная функция языка)",
    "instantiates": "extract/* (создание объекта)",
    "extends": "extract/* (объявление класса) → indexer._resolve_inheritance_names",
    "implements": "extract/* (объявление класса) → indexer._resolve_inheritance_names",
    "imports": "extract/php_ast.file_ref_edges (упоминание пути) → indexer._resolve_external_names",
    "queries": "extract/php_ast._sql_from_text, extract/generic._sql_from_text",
}


def _cause(conn, rev_id: str, item: dict) -> str:
    """Причина потери цели для одного адреса."""
    dst = item["dst"]
    prefix = item["prefix"]

    # Ребро, которого не должно было быть: литерал не является запросом.
    if prefix == "@table:":
        rows = conn.execute(
            "SELECT props FROM edge WHERE rev_id = ? AND dst = ?", (rev_id, dst)
        ).fetchall()
        texts = []
        for row in rows:
            try:
                props = json.loads(row[0] or "{}")
            except Exception:  # noqa: BLE001 — свойства не имеют права ломать замер
                props = {}
            if props.get("sql"):
                texts.append(str(props["sql"]))
        if texts:
            sys.path.insert(0, str(REPO / "src"))
            from ragraf.extract.php_ast import looks_like_sql_statement

            if not any(looks_like_sql_statement(t) for t in texts):
                return "edge_invented"

    if not prefix:
        # Адрес не заглушка: узел обязан был существовать. Отдельная причина,
        # потому что это потеря цели ВНУТРИ корпуса, а не внешняя ссылка.
        return "unknown"

    seen_revs = int(
        conn.execute(
            "SELECT COUNT(DISTINCT rev_id) FROM node WHERE node_id = ?", (dst,)
        ).fetchone()[0]
        or 0
    )
    if seen_revs:
        return "stub_dropped"

    # Цель есть, но критерий разрешения её не выбрал, либо её нет вовсе.
    #
    # Правило узкое намеренно. Первая редакция сверяла только короткое имя, и
    # `@ext:Site\\Support\\Ui::t` был объявлен «переименованным» из-за функции
    # `t` в `admin/static/app.js` — совпало имя, а не цель. Такое «объяснение»
    # хуже отсутствия объяснения: оно отправляет искать переименование там,
    # где цель просто вне корпуса.
    #
    # Поэтому различаются три состояния, и каждое проверяется по тому же
    # признаку, по которому выбирает резолвер:
    #   target_renamed — квалифицированное имя цели есть в ревизии (совпало
    #                    полное имя, а не последний сегмент): резолвер обязан
    #                    был привязать;
    #   ambiguous      — имя есть у нескольких узлов, и резолвер отказался
    #                    выбирать (правильное поведение, а не дефект);
    #   stub_missing   — совпадений нет: цель вне корпуса.
    name = dst[len(prefix):].strip()
    norm = name.replace("\\", "/")
    first_kind = (item["kinds"] or [""])[0]
    if "::" in norm or "\\" in name or "/" in norm:
        exact = conn.execute(
            "SELECT COUNT(*) FROM node WHERE rev_id = ? AND fqn = ?", (rev_id, name)
        ).fetchone()[0]
        if int(exact) == 1:
            # Ровно одна цель по полному имени: резолвер обязан был привязать.
            return "target_renamed"
        if int(exact) > 1:
            # Два объявления с одним полным именем в разных файлах: выбирать
            # нельзя, и резолвер правильно оставил ребро внешним.
            return "ambiguous"
        suffix = conn.execute(
            "SELECT COUNT(*) FROM node WHERE rev_id = ? AND path IS NOT NULL"
            " AND REPLACE(fqn, '\\', '/') LIKE ?",
            (rev_id, f"%{norm}"),
        ).fetchone()[0]
        return "ambiguous" if int(suffix) else "stub_missing"

    short = norm.rsplit("/", 1)[-1]
    if first_kind in ("extends", "implements"):
        cands = conn.execute(
            "SELECT COUNT(*) FROM node WHERE rev_id = ? AND name = ?"
            " AND kind IN ('class','interface','struct','enum')",
            (rev_id, short),
        ).fetchone()[0]
        if int(cands) == 1:
            return "target_renamed"
        if int(cands) > 1:
            return "ambiguous"
        return "stub_missing"

    cands = conn.execute(
        "SELECT COUNT(*) FROM node WHERE rev_id = ? AND name = ?"
        " AND kind IN ('function','method','class')",
        (rev_id, short),
    ).fetchone()[0]
    if int(cands):
        # Одно имя без места не позволяет выбрать: вызывающих может быть
        # несколько, и привязка к любому была бы выдумкой.
        return "ambiguous"
    return "stub_missing"


def main() -> int:
    parser = argparse.ArgumentParser(description="Почему рёбра потеряли цель")
    parser.add_argument("--root", default=str(REPO))
    parser.add_argument("--rev", default="", help="ревизия (по умолчанию активная)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save", default="")
    parser.add_argument("--assert-zero", action="store_true",
                        help="код 1, если есть хотя бы одно ребро в никуда")
    parser.add_argument("--show", type=int, default=15,
                        help="сколько адресов показать в текстовом выводе")
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    sys.path.insert(0, str(root / "src"))

    from ragraf import config as config_mod
    from ragraf import references as references_mod
    from ragraf import store

    cfg_path = root / "config" / "ragraf.yaml"
    if not cfg_path.is_file():
        print(f"нет конфигурации корпуса: {cfg_path}")
        return 1
    cfg = config_mod.load(cfg_path)
    conn = store.connect(cfg.graph_db)
    if args.rev:
        rev = args.rev
    else:
        row = conn.execute(
            "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        rev = str(row[0]) if row else ""
    if not rev:
        print("в хранилище нет готовой ревизии")
        return 1

    audit = references_mod.audit(conn, rev)
    items = references_mod.missing_targets(conn, rev)
    for item in items:
        item["cause"] = _cause(conn, rev, item)
        item["producer"] = PRODUCERS.get(
            (item["kinds"] or ["?"])[0], "не назван (вид ребра неизвестен замеру)"
        )

    by_cause: dict[str, int] = {}
    by_cause_targets: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for item in items:
        by_cause[item["cause"]] = by_cause.get(item["cause"], 0) + item["edges"]
        by_cause_targets[item["cause"]] = by_cause_targets.get(item["cause"], 0) + 1
        for kind in item["kinds"]:
            by_kind[kind] = by_kind.get(kind, 0) + 1

    result = {
        "rev": rev,
        "edges": audit["edges"],
        "nodes": audit["nodes"],
        "dangling_edges": audit["dangling_edges"],
        "dangling_targets": audit["dangling_targets"],
        "by_cause": dict(sorted(by_cause.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_cause_targets": dict(sorted(by_cause_targets.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_prefix": audit["by_prefix"],
        "by_kind": audit["by_kind"],
        "non_placeholder": audit["non_placeholder"],
        "items": items,
        "integrity": audit["integrity"],
    }
    if args.save:
        pathlib.Path(args.save).write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "items"},
                         ensure_ascii=False, indent=1))
        print(json.dumps(result["items"][: args.show], ensure_ascii=False, indent=1))
    else:
        print(f"ревизия {rev}: рёбер {result['edges']}, узлов {result['nodes']}")
        print(f" рёбер в никуда: {result['dangling_edges']} "
              f"на адресах {result['dangling_targets']}")
        if not result["dangling_edges"]:
            print(" целостность ссылок: каждый адрес ребра — существующий узел")
        for cause, count in result["by_cause"].items():
            print(f"  причина {cause:<15} рёбер {count:>5}  "
                  f"адресов {result['by_cause_targets'].get(cause, 0)}")
        for prefix, count in result["by_prefix"].items():
            print(f"  префикс  {prefix:<15} рёбер {count:>5}")
        for kind, count in result["by_kind"].items():
            print(f"  вид      {kind:<15} адресов {count:>5}")
        if result["non_placeholder"]:
            print(" адреса без префикса-заглушки (дефект, а не внешняя ссылка):")
            for item in result["non_placeholder"]:
                print(f"   {item['dst']} — рёбер {item['edges']}")
        if items:
            print(f" первые {min(args.show, len(items))} адресов:")
            for item in items[: args.show]:
                print(f"  {item['dst'][:52]:<52} рёбер {item['edges']:>3}  "
                      f"{item['cause']:<14} {', '.join(item['kinds'])}")

    if args.assert_zero and result["dangling_edges"]:
        print(f"ОТКАЗ: рёбер в никуда {result['dangling_edges']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
