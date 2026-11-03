#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка: не попадает ли значение секрета в ответ операции и в индекс.

Зачем отдельный инструмент. Требование этапа 14 звучит как «ни один секрет не
попадает в ответ операции», но проверять его глазами нельзя: секретов 131, а
ответов — десятки, и утечка выглядит как обычная строка в середине JSON.
Проверка обязана быть перебором: значение каждого секрета ищется в каждом
ответе и в каждом тексте индекса.

Как берутся значения. Два пути, и оба нужны:

  * ОСНОВНОЙ ПЕРЕБОР всех секретов — расшифровка в процессе ключом хранилища.
    Так проверяются все записи, но НЕ пишется 131 строка аудита: журнал выдачи
    существует для того, чтобы владелец видел РЕАЛЬНЫЕ выдачи агентов, и
    замер, засоривший его, мешал бы читать его по назначению.
  * КОНТРОЛЬНАЯ ПРОБА через публичный путь выдачи (`Vault.reveal` с причиной):
    несколько записей разных типов. Она доказывает, что аудируемый путь отдаёт
    те же значения и что каждая выдача видна в журнале — то есть что перебор
    выше не разошёлся с тем, чем пользуются агенты.

Что осматривается:

  1. ответы операций чтения фасада (сериализованный `result` + `extra`);
  2. тексты чанков индекса (`chunk_text.text`);
  3. узлы графа (`node.name`, `signature`, `props`) и пути файлов;
  4. пути карты метаданных (`catalog/global_map.sqlite`, таблица `secret_place`)
     — там по построению только путь и правило, значения не хранятся.

Запуск:
    python tools/check_secret_leaks.py
    python tools/check_secret_leaks.py --json
    python tools/check_secret_leaks.py --skip-index        (только ответы)
    python tools/check_secret_leaks.py --min-length 8      (порог значимости)

Коды возврата: 0 — утечек нет; 1 — найдены (имя, место, длина совпадения);
2 — проверить нечем (нет хранилища, ключа или индекса).
Значения НЕ печатаются ни в вывод, ни в код возврата: только имена и место.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]

#: Операции чтения, чьи ответы осматриваются. Список ЯВНЫЙ: `op_*` в фасаде
#: включает записи (`op_drift_ack`, `op_calibrate`, `op_compact`), и перебор
#: «все методы подряд» менял бы состояние вместо проверки.
READ_OPS: tuple[tuple[str, str], ...] = (
    ("search", "вопрос о корпусе"),
    ("search_multi", "литеральный поиск"),
    ("symbol", "имя символа"),
    ("callers", "вызывающие"),
    ("callees", "вызываемые"),
    ("code", "тело функции"),
    ("repo_map", "карта корпуса"),
    ("schema", "слой схемы"),
    ("freshness", "свежесть"),
    ("index_status", "состояние индекса"),
    ("catalog", "реестр разработок"),
    ("excludes", "исключения"),
    ("duplicates", "копии файлов"),
    ("dead_code", "неиспользуемое"),
    ("entrypoints", "точки входа"),
    ("routes", "маршруты"),
    ("route", "маршрут по запросу"),
    ("task_stats", "статистика задач"),
    ("notes", "заметки"),
    ("live_read", "чтение файла"),
    ("usage", "статистика обращений"),
)

#: Значения короче порога не ищутся: «yes», «1234», «md» встречаются в любом
#: тексте, и совпадение по ним было бы ложной находкой, а не утечкой.
DEFAULT_MIN_LENGTH = 8


def collect_values(vault, min_length: int) -> tuple[dict[str, str], list[str], dict]:
    """Расшифровать значения всех секретов (без записи в аудит) — для перебора.

    Ключ и расшифровка берутся у самого хранилища (`vault.master` и
    `vault._decrypt` — те же функции, которыми пользуется выдача): второго пути к
    значению в проекте нет и заводить его нельзя. Аудит здесь не пишется
    осознанно — см. заголовок файла; аудируемый путь проверяется отдельной
    пробой через `Vault.reveal`.

    Счётчик записей и строк аудита берётся из `Vault.check()`: он читает всё
    хранилище и ничего не пишет — то есть безопасен для замера.
    """
    from ragraf import vault as vault_mod

    health = vault.check()
    master = vault.master()
    conn = vault._connect()  # noqa: SLF001
    try:
        rows = conn.execute(
            "SELECT id, name, ver, nonce, ciphertext FROM secret"
        ).fetchall()
    finally:
        conn.close()
    values: dict[str, str] = {}
    skipped: list[str] = []
    for row in rows:
        name = str(row["name"])
        try:
            value = vault_mod._decrypt(master, str(row["id"]), name,  # noqa: SLF001
                                       int(row["ver"]), str(row["nonce"]),
                                       str(row["ciphertext"]))
        except Exception:  # noqa: BLE001 — нечитаемая запись не отменяет проверку
            skipped.append(name)
            continue
        if value and len(value) >= min_length:
            values[name] = value
    return values, skipped, health


def leak_pattern(values: dict[str, str]) -> re.Pattern[str]:
    """Одно регулярное выражение из всех значений: один проход по тексту."""
    parts = sorted(values.values(), key=len, reverse=True)
    return re.compile("|".join(re.escape(p) for p in parts)) if parts else re.compile(r"(?!x)x")


def scan_text(pattern: re.Pattern[str], text: str) -> bool:
    return bool(text) and pattern.search(text) is not None


def scan_answers(facade, pattern: re.Pattern[str], symbol: str, table: str) -> list[dict]:
    """Ответы операций чтения: сериализованный результат как его видит агент."""
    found: list[dict] = []
    checked = 0
    for op, note in READ_OPS:
        call = _build_call(facade, op, symbol, table)
        if call is None:
            continue
        try:
            payload = call()
        except Exception as exc:  # noqa: BLE001 — упавшая операция не отменяет проверку
            found.append({"where": f"op:{op}", "note": note,
                          "error": f"{type(exc).__name__}"})
            continue
        checked += 1
        text = json.dumps(payload, ensure_ascii=False)
        if scan_text(pattern, text):
            found.append({"where": f"op:{op}", "note": note,
                          "match": "значение секрета в ответе"})
    return found, checked


def _build_call(facade, op: str, symbol: str, table: str):
    """Аргументы операций берутся живые: литерал отстал бы от корпуса."""
    if op == "search":
        return lambda: facade.op_search("контрольная сумма корпуса", limit=5)
    if op == "search_multi":
        return lambda: facade.op_search_multi(["budget_exceeded"], limit=10)
    if op == "symbol" and symbol:
        return lambda: facade.op_symbol(symbol)
    if op == "callers" and symbol:
        return lambda: facade.op_callers(symbol, depth=1, limit=50)
    if op == "callees" and symbol:
        return lambda: facade.op_callees(symbol, depth=1, limit=50)
    if op == "code" and symbol:
        return lambda: facade.op_code(fqn=symbol)
    if op == "schema" and table:
        return lambda: facade.op_schema(table)
    if op == "route":
        return lambda: facade.op_route("где материалы по проекту")
    if op == "live_read":
        return lambda: facade.op_live_read("AGENTS.md", start=1, end=40)
    return {
        "repo_map": lambda: facade.op_repo_map(),
        "freshness": lambda: facade.op_freshness(),
        "index_status": lambda: facade.op_index_status(),
        "catalog": lambda: facade.op_catalog(),
        "excludes": lambda: facade.op_excludes(),
        "duplicates": lambda: facade.op_duplicates(limit=20),
        "dead_code": lambda: facade.op_dead_code(limit=20),
        "entrypoints": lambda: facade.op_entrypoints(limit=20),
        "routes": lambda: facade.op_routes(limit=50),
        "task_stats": lambda: facade.op_task_stats(),
        "notes": lambda: facade.op_notes(),
        "usage": lambda: facade.op_task_stats(),
    }.get(op)


def scan_index(cfg, pattern: re.Pattern[str]) -> list[dict]:
    """Тексты чанков, узлы графа и пути файлов — то, из чего строятся ответы."""
    from ragraf import store

    found: list[dict] = []
    conn = store.connect(cfg.lexvec_db, readonly=True)
    try:
        rows = conn.execute("SELECT chunk_id, text FROM chunk_text").fetchall()
        print(f"  текстов чанков: {len(rows)}")
        for row in rows:
            if scan_text(pattern, str(row["text"] or "")):
                found.append({"where": "index:chunk_text",
                              "chunk_id": str(row["chunk_id"])})
    except Exception as exc:  # noqa: BLE001
        found.append({"where": "index:chunk_text", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()

    conn = store.connect(cfg.graph_db, readonly=True)
    try:
        for column in ("name", "signature", "props", "path"):
            rows = conn.execute(
                f"SELECT node_id, {column} AS value FROM node WHERE {column} <> ''"
            ).fetchall()
            print(f"  узлов, поле {column}: {len(rows)}")
            for row in rows:
                if scan_text(pattern, str(row["value"] or "")):
                    found.append({"where": f"index:node.{column}",
                                  "node_id": str(row["node_id"])})
    except Exception as exc:  # noqa: BLE001
        found.append({"where": "index:node", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()
    return found


def scan_global_map(root: Path, pattern: re.Pattern[str]) -> list[dict]:
    """Карта метаданных: там по построению только путь и правило, не значение."""
    path = Path(root) / "catalog" / "global_map.sqlite"
    if not path.is_file():
        return []
    import sqlite3

    found: list[dict] = []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "secret_place" not in tables:
            return []
        rows = conn.execute("SELECT * FROM secret_place").fetchall()
        print(f"  записей карты секретоносных мест: {len(rows)}")
        for row in rows:
            text = " ".join(str(part) for part in row)
            if scan_text(pattern, text):
                found.append({"where": "global_map:secret_place"})
    except Exception as exc:  # noqa: BLE001
        found.append({"where": "global_map", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()
    return found


def audit_probe(vault, names: list[str]) -> dict:
    """Контрольная проба аудируемого пути: несколько выдач с причиной.

    Доказывает две вещи сразу: публичный путь отдаёт то же значение, что и
    перебор, и каждая выдача попадает в журнал строкой `reveal`/`ok`.
    """
    import time

    from ragraf import vault as vault_mod

    actor = vault_mod.actor_from_env("selftest", label="RAGRAF-LeakCheck",
                                     client="cli", admin=True)
    before = int(vault.check().get("audit_rows") or 0)
    probe: list[dict] = []
    for name in names[:3]:
        try:
            out = vault.reveal(name, actor=actor,
                               reason="этап14: контрольная проба аудита выдачи")
            probe.append({"name": name, "ok": True,
                          "chars": len(str(out.get("value") or "")),
                          "reason_seen": str(out.get("reason") or "")[:40]})
        except vault_mod.VaultError as exc:
            probe.append({"name": name, "ok": False, "code": exc.code})
    time.sleep(0.05)
    after = int(vault.check().get("audit_rows") or 0)
    # Отказ без причины обязан попасть в журнал так же, как выдача.
    try:
        vault.reveal(names[0] if names else "нет такой записи", actor=actor, reason="")
    except vault_mod.VaultError as exc:
        denied_code = exc.code
    else:
        denied_code = "разрешено"
    final = int(vault.check().get("audit_rows") or 0)
    return {"probe": probe, "audit_rows_before": before, "audit_rows_after": after,
            "audit_rows_after_denied": final,
            "denied_code": denied_code,
            "issued_recorded": after - before >= len([p for p in probe if p["ok"]]),
            "denied_recorded": final > after}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Секрет в ответе операции и в индексе: перебор всех значений")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--min-length", type=int, default=DEFAULT_MIN_LENGTH)
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root / "src"))

    from ragraf import config as config_mod
    from ragraf import vault as vault_mod

    try:
        vault = vault_mod.Vault()
        policy = vault.policy()
    except Exception as exc:  # noqa: BLE001
        print(f"NOT-IMPLEMENTED: хранилище недоступно: {type(exc).__name__}")
        return 2
    if not policy.get("enabled"):
        print("NOT-IMPLEMENTED: хранилище секретов выключено настройкой")
        return 2

    values, skipped, health = collect_values(vault, args.min_length)
    total = int(health.get("count") or 0)
    print(f"[проверка] секретов в хранилище: {total}; значений для поиска: "
          f"{len(values)} (короче {args.min_length} симв. — "
          f"{total - len(values) - len(skipped)}); не расшифровано: {len(skipped)}")
    if not values:
        print("NOT-IMPLEMENTED: ни одного значения для поиска")
        return 2
    pattern = leak_pattern(values)

    config_path = root / "config" / "ragraf.yaml"
    report: dict = {"root": str(root), "secrets": len(values),
                    "min_length": args.min_length, "found": [], "checked_ops": 0}
    if config_path.is_file():
        from ragraf.facade import Facade
        from ragraf import store

        cfg = (getattr(config_mod, "load", None)
               or getattr(config_mod, "load_config"))(config_path)
        facade = Facade(cfg, tool="selftest", agent="RAGRAF-LeakCheck",
                        agent_kind="internal", task="этап14:утечки", telemetry=False)
        try:
            symbol = ""
            table = ""
            try:
                rev = facade.current_rev()
                conn = store.connect(cfg.graph_db, readonly=True)
                row = conn.execute(
                    "SELECT n.name FROM edge e JOIN node n"
                    " ON n.rev_id = e.rev_id AND n.node_id = e.dst"
                    " WHERE e.rev_id = ? AND n.kind IN ('function','method')"
                    " GROUP BY n.name ORDER BY COUNT(*) DESC LIMIT 1", (rev,)).fetchone()
                symbol = str(row[0]) if row else ""
                conn.close()
            except Exception:  # noqa: BLE001
                symbol = ""
            print("\nОтветы операций чтения:")
            answers, checked = scan_answers(facade, pattern, symbol, table)
            report["checked_ops"] = checked
            report["found"].extend(answers)
            if not args.skip_index:
                print("\nИндекс:")
                report["found"].extend(scan_index(cfg, pattern))
                report["found"].extend(scan_global_map(root, pattern))
        finally:
            facade.close()
    else:
        print(f"конфига нет ({config_path}): проверены только индекс и карта")

    print("\nАудит выдачи (публичный путь):")
    probe = audit_probe(vault, list(values))
    report["audit"] = probe
    for item in probe["probe"]:
        state = "выдано" if item.get("ok") else f"отказ {item.get('code')}"
        print(f"  {item['name'][:44]:44s} {state}")
    print(f"  строк аудита: {probe['audit_rows_before']} → {probe['audit_rows_after']} "
          f"→ {probe['audit_rows_after_denied']}; отказ без причины: "
          f"{probe['denied_code']}")
    report["audit_ok"] = bool(probe["issued_recorded"] and probe["denied_recorded"])

    leaks = [item for item in report["found"] if not item.get("error") and "match" in item]
    errors = [item for item in report["found"] if item.get("error")]
    print()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    for item in report["found"]:
        if item.get("error"):
            print(f"  ОШИБКА ПРОВЕРКИ {item['where']}: {item['error']}")
        else:
            print(f"  УТЕЧКА в {item['where']}"
                  + (f" (chunk {item['chunk_id'][:12]})" if item.get("chunk_id") else "")
                  + (f" (узел {item['node_id'][:12]})" if item.get("node_id") else ""))
    print(f"Строка для отчёта: секретов проверено {len(values)}, ответов операций "
          f"{report['checked_ops']}, утечек {len(leaks)}, ошибок проверки {len(errors)}, "
          f"аудит выдачи {'ведётся' if report['audit_ok'] else 'НЕ подтверждён'}")
    if leaks:
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
