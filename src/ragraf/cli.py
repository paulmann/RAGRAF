"""CLI RAGRAF: index · reindex · query · drift · status · drop · selftest.

Все команды возвращают машиночитаемый JSON (--json) или краткий текст.
Свободного языка запросов нет: набор операций фиксирован (ФТ-15).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__, util
from .config import ConfigError, ProjectConfig, load as load_config

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
# Код 3 означает ОТКАЗ СЛОЯ, а не «ничего не найдено». Различие важно
# вызывающему: функция RAGRAF в SagaAI считает любой ненулевой код поломкой
# инструмента, поэтому «в индексе этого нет» отдаётся кодом 0 с found=false,
# а кодом 3 — только случай, когда операция не может быть выполнена вообще
# (например slice при недостроенном CPG). Иначе обычный пустой результат
# выглядел бы для агента как сломанный инструмент, и агент отказывался
# от RAGRAF вместо продолжения работы.
EXIT_UNAVAILABLE = 3
# Оставлено имя EXIT_NOT_FOUND как псевдоним: внешние вызовы могли
# ссылаться на него, но смысл теперь один — отказ слоя.
EXIT_NOT_FOUND = EXIT_UNAVAILABLE

OPS = [
    "search", "search_multi", "symbol", "references", "callers", "callees",
    "impact", "slice",
    "code", "repo_map", "schema", "table_usage", "routes", "index_status",
    "drift_report", "drift_ack", "live_read", "calibrate", "write_check", "usage",
    "freshness", "catalog", "policy_trace", "class_usage",
    # Структура корпуса: копии, точки входа, мёртвый код (P1-2).
    "duplicates", "entrypoints", "dead_code",
    # Слой живых баз данных: структура, связи, примеры, сверки.
    "db_schema", "db_table", "db_relations", "db_sample", "db_drift", "db_cross",
    # P2: сравнение ревизий и память о решениях.
    "diff", "note", "notes",
    # P3 (§3.2/§3.4): гейт использования индекса и отчёт по задаче.
    "gate", "task_stats", "task_set",
]

# Операции, которые в релизе 1 требуют слоя, добавляемого в M2/M3.
OP_REQUIRES_CPG = {"slice"}

# Значения --mode зависят от операции: у одних это режим обхода графа
# (hard/full), у других — вид обращений к таблице (all/read/write) или вид
# расхождения классов оформления (all/missing/unused). Один общий choices
# отвергал верные команды до запуска: `table_usage --mode write` и
# `class_usage --mode missing` из инструкции проекта падали с кодом 2.
MODE_CHOICES: dict[str, tuple[str, ...]] = {
    "impact": ("full", "hard"),
    "table_usage": ("all", "read", "write"),
    "class_usage": ("all", "missing", "unused"),
}
# Операции без своего набора (search, symbol, callers, ...) принимают только
# значение по умолчанию: режим у них не меняет смысл ответа.
MODE_DEFAULT_BY_OP: dict[str, str] = {
    "impact": "full",
    "table_usage": "all",
    "class_usage": "all",
}
MODE_DEFAULT_ANY = "full"
# Для argparse: объединение всех значений, чтобы верная команда не
# отклонялась до запуска. Точная проверка по операции — в resolve_mode.
MODE_ALL = tuple(dict.fromkeys(
    (MODE_DEFAULT_ANY,) + tuple(v for c in MODE_CHOICES.values() for v in c)
))


def resolve_mode(op: str, mode: str | None) -> str:
    """Значение --mode для конкретной операции.

    Нужно потому, что одно имя аргумента обслуживает три разных понятия.
    Если значение не задано — берётся значение по умолчанию для операции;
    если задано значение чужого набора — ошибка использования, а не молчаливая
    подстановка (иначе агент получил бы ответ не на свой вопрос).
    """
    allowed = MODE_CHOICES.get(op)
    if allowed is None:
        if mode in (None, MODE_DEFAULT_ANY):
            return MODE_DEFAULT_ANY
        raise ValueError(
            f"операция {op} не принимает --mode; допустимо только "
            f"{MODE_DEFAULT_ANY!r}, получено {mode!r}"
        )
    if mode is None:
        return MODE_DEFAULT_BY_OP.get(op, MODE_DEFAULT_ANY)
    if mode not in allowed:
        raise ValueError(
            f"операция {op} принимает --mode из {', '.join(allowed)}; "
            f"получено {mode!r}"
        )
    return mode


def _print(data, as_json: bool) -> None:
    if as_json:
        print(util.dumps(data, indent=2))
        return
    if isinstance(data, dict):
        _print_human(data)
    else:
        print(data)


def _print_human(data: dict) -> None:
    op = data.get("op")
    print(f"[{op}] rev={data.get('index_rev')} capability={data.get('capability')} "
          f"provenance={data.get('provenance')} found={data.get('found')}")
    if data.get("error"):
        print(f"  ОШИБКА: {data['error']}")
    drift = data.get("drift") or {}
    if drift.get("entries"):
        print(f"  drift: файлов={drift.get('files')} символов={drift.get('symbols')} "
              f"записей={drift.get('entries')}")
    for w in data.get("stale_warnings") or []:
        print(f"  ! устарело: {w.get('path')} ({w.get('reason')}) → {w.get('suggest')}")
    for lim in data.get("limits") or []:
        print(f"  · ограничение: {lim}")

    res = data.get("result")
    if res is None:
        return
    # Режим `answer=true` (P2-2): результат — готовый текст ответа и ссылки, а не
    # список структурных полей. Печатается как есть — это и есть ответ человеку.
    # Без этой ветви `res[:20]` падал с `KeyError: slice(None, 20, None)`, потому
    # что словарь срезом не режется (измерено 14.10.2026 на `search --answer`).
    if isinstance(res, dict) and "answer" in res:
        print()
        print(res["answer"])
        return
    # Отчёт по задаче — тоже готовый текст, а не структурный результат.
    if isinstance(res, dict) and res.get("markdown") and op == "task_stats":
        print()
        print(res["markdown"])
        return
    if op == "index_status":
        r = res.get("rev") or {}
        print(f"  проект: {res.get('project')}  корень: {res.get('root')}")
        print(f"  создана: {r.get('created_at')}  состояние: {r.get('state')}  "
              f"CPG: {'да' if r.get('joern_used') else 'нет'}")
        print(f"  файлов={r.get('file_count')} узлов={r.get('node_count')} рёбер={r.get('edge_count')}")
        print(f"  вектор: {'доступен' if res.get('vector_available') else 'НЕДОСТУПЕН'} "
              f"({res.get('embedding_model')})")
        cov = res.get("coverage") or {}
        interesting = ["files_ok", "files_partial", "files_failed", "edges_stated", "edges_derived",
                       "symbols", "chunks", "secrets_skipped"]
        print("  покрытие: " + " ".join(f"{k}={int(cov[k])}" for k in interesting if k in cov))
        for st in res.get("stages") or []:
            print(f"    {st['stage']:<8} {st['seconds']:>8.3f}s  items={st['items']}"
                  + (f"  {st['note']}" if st.get("note") else ""))
    elif op == "symbol":
        for r in res:
            print(f"  {r.get('kind')} {r.get('fqn')}  {r.get('path')}:{r.get('start_line')}-{r.get('end_line')}")
    elif op in ("callers", "callees"):
        print(f"  цель {res.get('target')} ({res.get('direction')}, глубина {res.get('depth')}): "
              f"stated={res.get('stated')} derived={res.get('derived')}")
        for it in (res.get("items") or [])[:15]:
            mark = "=" if it.get("provenance") == "stated" else "~"
            print(f"    {mark} [{it.get('level')}] {it.get('fqn')}  {it.get('path')}:{it.get('start_line')}"
                  f"  ({it.get('edge_kind')})")
    elif op == "impact":
        print(f"  цель {res.get('target')} режим={res.get('mode')} "
              f"затронуто: {res.get('affected_count')}")
        for f in (res.get("affected_files") or [])[:20]:
            print(f"    {f['path']}  stated={f['stated']} derived={f['derived']}")
    elif op == "search":
        for h in res[:20]:
            stale = " [STALE]" if h.get("stale") else ""
            print(f"  {h.get('score')}  {h.get('path')}:{h.get('start_line')}-{h.get('end_line')}{stale}")
            print(f"      {h.get('why')}")
            # Фрагмент печатается, когда он есть: без него текстовый режим
            # сообщал только адрес, и за содержимым приходилось идти вторым
            # вызовом — то есть экономия от `--with-text` в текстовом режиме
            # не была видна вообще.
            if h.get("snippet"):
                print(f"      {h['snippet']}")
    elif op == "repo_map":
        print(f"  область: {res.get('scope')}  файлов: {res.get('files_included')}/{res.get('files_total')}"
              f"  символов бюджета: {res.get('budget_tokens')}")
        for f in (res.get("files") or [])[:25]:
            names = ", ".join(f"{t['name']}[{t['start_line']}]" for t in f.get("top") or [])
            print(f"    {f['path']} ({f['symbols']}) {names}")
    elif op == "code":
        # Общий фолбэк режет вывод до 4000 символов, и раньше это выглядело как
        # конец ответа: агент не мог отличить «текст кончился» от «вывод обрезан».
        # Теперь печатается начало каждого спана и явная строка-предупреждение.
        for it in res or []:
            if it.get("error"):
                print(f"  {it.get('path')}: ОШИБКА {it['error']}")
                continue
            print(f"  [{it.get('index')}] {it.get('symbol') or it.get('path')}  "
                  f"{it.get('path')}:{it.get('window', {}).get('start')}-"
                  f"{it.get('window', {}).get('end')}  источник={it.get('text_source')}"
                  + ("  (в индексе нет части строк)" if it.get("gaps") else ""))
            head = (it.get("text") or "").splitlines()
            for line in head[:6]:
                print(f"      {line}")
            if len(head) > 6:
                print(f"      … ещё {len(head) - 6} строк; полный текст — с --json")
    elif op == "drift_report":
        if not res:
            print("  журнал пуст")
        for e in res[:20]:
            print(f"  #{e['id']} {e['ts']} {e['kind']:<15} {e['path']}  by={e['detected_by']}")
    elif op == "live_read":
        print(f"  {res.get('path')} строк={res.get('total_lines')} "
              f"изменён={res.get('changed_since_index')}")
    else:
        dumped = util.dumps(res, indent=2)
        # Обрезка помечается явно. Прежде вывод обрывался на 4000 символах
        # молча, и причина была неотличима от конца данных.
        if len(dumped) > 4000:
            print(dumped[:4000])
            print(f"  · вывод обрезан для чтения: показано 4000 из {len(dumped)} символов; "
                  "полный ответ — с --json")
        else:
            print(dumped)


# --------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------


def cmd_index(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    from .indexer import Indexer

    idx = Indexer(cfg)
    try:
        report = idx.run(with_vectors=not args.no_vectors, limit_files=args.limit_files)
    finally:
        idx.close()
    data = report.to_dict()

    # Ручная индексация обязана обновлять реестр разработок.
    # Дефект: раньше реестр обновлял только сторож (авто-реиндексация), поэтому
    # после ручного `index` панель и операция catalog показывали старую ревизию,
    # а признак «сумма совпадает» считался по устаревшей сумме. Найдено
    # проверкой: граф показывал rev-0061, а реестр — rev-0060 и degraded.
    if report.ok:
        try:
            _record_index_run(cfg, report)
        except Exception as exc:
            # Неудача учёта не должна отменять уже выполненную индексацию:
            # о ней сообщается отдельной строкой, а код возврата остаётся успешным.
            print(f"  · реестр не обновлён: {type(exc).__name__}: {exc}")

    if args.json:
        _print(data, True)
    else:
        print(f"ревизия {data['rev_id']}: {'ГОТОВО' if data['ok'] else 'ОШИБКА'} "
              f"за {data['seconds']}s")
        print(f"  файлов={data['files']} узлов={data['nodes']} рёбер={data['edges']} "
              f"чанков={data['chunks']} векторов={data['vectors']} "
              f"(переиспользовано {data['vectors_reused']})")
        print(f"  разбор: {data['parse_status']}")
        print(f"  capability={data['capability']}")
        for st in data["stages"]:
            print(f"    {st['stage']:<8} {st['seconds']:>8.3f}s items={st['items']}"
                  + (f"  {st['note']}" if st.get("note") else ""))
        for note in data["notes"]:
            print(f"  · {note}")
        top = sorted(data["counts_by_edge"].items(), key=lambda kv: -kv[1])[:8]
        print("  рёбра: " + " ".join(f"{k}={v}" for k, v in top))
    return EXIT_OK if report.ok else EXIT_ERROR


def _record_index_run(cfg: ProjectConfig, report) -> None:
    """Записать результат ручной индексации в реестр разработок.

    Общая точка записи одна — `watch.record_index_run`: реестр не должен
    зависеть от того, какой путь запустил индексатор. Здесь остаётся только
    указание триггера.
    """
    from .watch import record_index_run

    record_index_run(cfg, report, trigger="cli:index")

def cmd_reindex(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Переиндексация: новый снимок + архивация журнала расхождений (ФТ-32..ФТ-34)."""
    from .drift import archive_and_reset
    from .indexer import Indexer

    idx = Indexer(cfg)
    try:
        report = idx.run(with_vectors=not args.no_vectors, limit_files=args.limit_files)
    finally:
        idx.close()
    if report.ok:
        result = archive_and_reset(cfg, report.rev_id)
        report.notes.append(
            f"архивировано записей журнала: {result['entries']} → {result['report_path']}"
        )
        # Ручная переиндексация — такой же путь, запускающий индексатор, как
        # `index`, и реестр обязан получить результат прогона тем же способом.
        # Измеренный дефект (13.10.2026): `reindex` этой записи НЕ делал, и
        # реестр расходился с индексом — замерено на собственном корпусе:
        # в индексе rev-0046, в реестре rev-0045 и старая сумма, и тогда
        # проверка свежести честно реиндексировала корпус заново, а панель и
        # `catalog` показывали прошедшее состояние. Тот же путь есть у
        # `cmd_index`, поэтому вызов общий — `record_index_run`.
        try:
            _record_index_run(cfg, report)
        except Exception as exc:
            # Неудача учёта не отменяет выполненную индексацию: о ней
            # сообщается отдельной строкой, код возврата остаётся успешным.
            report.notes.append(f"реестр не обновлён: {type(exc).__name__}: {exc}")
    data = report.to_dict()
    if args.json:
        _print(data, True)
    else:
        print(f"переиндексация → {report.rev_id}: {'ГОТОВО' if report.ok else 'ОШИБКА'}")
        for note in data["notes"]:
            print(f"  · {note}")
    return EXIT_OK if report.ok else EXIT_ERROR


def cmd_drift(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Обнаружение различий (индекс НЕ обновляется) — требование заказчика."""
    from .drift import scan_drift

    res = scan_drift(cfg, detected_by=args.detected_by, note=args.note)
    if args.json:
        _print(res, True)
    else:
        print(f"сканирование различий: новых записей {res['new_entries']} "
              f"(всего {res['total_entries']}) за {res['seconds']}s")
        for e in res["entries"][:30]:
            sym = ""
            if e.get("symbols_added") or e.get("symbols_changed"):
                sym = f"  символов: +{len(e.get('symbols_added') or [])}/~{len(e.get('symbols_changed') or [])}"
            print(f"  {e['kind']:<15} {e['path']}{sym}")
        if res.get("ledger_path"):
            print(f"  журнал: {res['ledger_path']}")
    return EXIT_OK


def cmd_query(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    from .facade import Facade

    op = args.op
    if op not in OPS:
        print(f"неизвестная операция: {op}. Доступно: {', '.join(OPS)}", file=sys.stderr)
        return EXIT_USAGE
    # ФТ-15: свободный язык запросов не принимается.
    if args.op in {"cypher", "cpgql", "gremlin", "sql"} or (args.text and _looks_like_graph_query(args.text)):
        print("ОТКАЗ: язык запросов к графу не поддерживается. Используйте фиксированные операции.",
              file=sys.stderr)
        return EXIT_USAGE

    fc = Facade(cfg, tool="cli", agent=_agent_from_env()[0],
                agent_kind=_agent_from_env()[1], task=_task_from_env())
    try:
        data = dispatch_op(fc, args)
    except ValueError as exc:
        # Неверное значение параметра для этой операции (--mode, --depth и т. п.).
        # Это ошибка вызова, а не поломка: отвечаем кодом использования и
        # человеческим текстом без трассировки, чтобы вызывающий агент
        # исправил аргумент, а не считал инструмент сломанным.
        print(f"неверный параметр: {exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        fc.close()
    _print(data, args.json)
    return _exit_code(op, data)


def _exit_code(op: str, data: dict) -> int:
    """Код возврата CLI по ответу операции.

    Правило одно и оно вытекает из контракта фасада: отсутствие данных —
    это НОРМАЛЬНЫЙ ответ (`ok`-эквивалент: found=false без error), а не
    поломка. Ненулевой код остаётся только у настоящего отказа слоя.

    Почему так. Раньше любой ответ `found=false` с пояснением отдавался
    кодом 3. Функция RAGRAF в SagaAI на ненулевой код возвращает
    `ok:false`, то есть «инструмент сломан», — и агент, получив честное
    «в индексе этого нет», начинал чинить несуществующую поломку.
    """
    if not data.get("error"):
        return EXIT_OK
    # Отказ слоя: операция есть, но выполнить её сейчас нельзя.
    if op in OP_REQUIRES_CPG and data.get("capability") != "full":
        return EXIT_UNAVAILABLE
    # Индекс не создан — это состояние окружения, а не отсутствие данных.
    if not data.get("found", True) and "индекс не создан" in str(data.get("error")):
        return EXIT_UNAVAILABLE
    return EXIT_OK


def _task_from_env() -> str:
    """Метка задачи, к которой относится вызов.

    Читается из переменной окружения `RAGRAF_TASK`. Нужна команде `/RAGRAF`
    в SagaAI: там каждый вызов инструмента запускает ОТДЕЛЬНЫЙ процесс,
    поэтому «сессия RAGRAF» (один запуск) с задачей пользователя
    не совпадает. Одна метка на всю задачу собирает все вызовы вместе.

    Пустая строка означает «метка не задана» — это нормальный случай
    для обычной работы из командной строки.
    """
    import os as _os

    return (_os.environ.get("RAGRAF_TASK") or "").strip()


def _agent_from_env() -> tuple[str, str]:
    """Кто вызвал RAGRAF: имя агента и его семейство из окружения.

    Зачем именно так. Консольный вызов `ragraf.py query ...` не имеет
    параметров «кто меня вызвал». Но его запускает инструмент агента,
    и этот агент может передать себя через переменные окружения
    дочернему процессу — это единственный способ узнать агента, не меняя
    синтаксис командной строки.

    Без этого вызовы внешних агентов попадали в статистику как `unknown`,
    и владелец не мог отфильтровать данные по агенту.

    Семейство выводится из имени, если не задано явно: имя, содержащее
    `autoclaw`, считается семейством AutoClaw, всё прочее — внешним агентом.
    """
    import os as _os

    agent = (_os.environ.get("RAGRAF_AGENT") or "").strip() or "unknown"
    kind = (_os.environ.get("RAGRAF_AGENT_KIND") or "").strip()
    if not kind:
        low = agent.lower().replace("-", "").replace("_", "")
        if "autoclaw" in low:
            kind = "autoclaw"
        elif agent == "unknown":
            kind = "unknown"
        else:
            kind = "external"
    return agent, kind


def _looks_like_graph_query(text: str) -> bool:
    t = text.strip().lower()
    markers = ("match ", "return ", "g.v(", "g.e(", "cpg.", "create (", "merge (")
    return any(t.startswith(m) for m in markers)


def dispatch_op(fc, args: argparse.Namespace) -> dict:
    op = args.op
    # P2-2: `--answer` доступен всем операциям, которые отвечают на вопрос
    # словами. Берётся один раз, чтобы ветви не расходились.
    answer = bool(getattr(args, "answer", False))
    if op == "search":
        return fc.op_search(args.text or "", limit=args.limit,
                            with_text=bool(getattr(args, "with_text", False)),
                            text_width=int(getattr(args, "text_width", 160)),
                            answer=answer)
    if op == "search_multi":
        # Несколько запросов одним вызовом: разделитель `|`, чтобы не
        # добавлять в CLI формат, зависящий от оболочки (кавычки, запятые).
        raw = getattr(args, "queries", None) or args.text or ""
        queries = [q.strip() for q in str(raw).split("|") if q.strip()]
        return fc.op_search_multi(
            queries, limit=args.limit, scope=args.scope,
            regex=bool(getattr(args, "regex", False)), answer=answer,
        )
    if op == "symbol":
        return fc.op_symbol(args.name or args.text or "", path=args.path,
                            limit=args.limit, answer=answer)
    if op == "references":
        return fc.op_references(args.fqn or args.name or args.text or "", limit=args.limit)
    if op == "callers":
        return fc.op_callers(args.fqn or args.name or args.text or "", depth=args.depth,
                             hard_only=args.hard, limit=args.limit, answer=answer)
    if op == "callees":
        return fc.op_callees(args.fqn or args.name or args.text or "", depth=args.depth,
                             hard_only=args.hard, limit=args.limit)
    if op == "impact":
        return fc.op_impact(args.target or args.name or args.text or "",
                            mode=resolve_mode(op, args.mode),
                            depth=args.depth, limit=args.limit, answer=answer)
    if op == "slice":
        return fc.op_slice(args.fqn or args.name or args.text or "", direction=args.direction,
                           limit=args.limit)
    if op == "code":
        return fc.op_code(fqn=args.fqn or args.name or args.text, path=args.path,
                          style=args.style, limit=args.limit,
                          entry_ids=getattr(args, "entry_ids", None),
                          start=getattr(args, "start", None),
                          end=getattr(args, "end", None),
                          source=getattr(args, "source", "auto"),
                          include_containers=bool(getattr(args, "include_containers", False)))
    if op == "repo_map":
        return fc.op_repo_map(scope=args.scope, budget_tokens=args.budget, detail=args.detail)
    if op == "schema":
        return fc.op_schema(table=args.table, limit=args.limit)
    if op == "table_usage":
        return fc.op_table_usage(args.table or args.name or args.text or "", limit=args.limit,
                                  mode=resolve_mode(op, args.mode))
    if op == "routes":
        return fc.op_routes(path=args.path, limit=args.limit)
    # --- структура корпуса (P1-2) ---
    if op == "duplicates":
        return fc.op_duplicates(scope=args.scope or args.path, limit=args.limit)
    if op == "entrypoints":
        return fc.op_entrypoints(path=args.path, scope=args.scope, limit=args.limit)
    if op == "dead_code":
        return fc.op_dead_code(scope=args.scope or args.path, limit=args.limit)
    # --- P2: ревизии и заметки ---
    if op == "diff":
        return fc.op_diff(since=getattr(args, "since_rev", None), limit=args.limit)
    if op == "note":
        return fc.op_note(
            args.text or args.name or "",
            task=getattr(args, "task", "") or "",
            tags=[t.strip() for t in (getattr(args, "tags", "") or "").split(",") if t.strip()],
            path=getattr(args, "path", "") or "",
        )
    if op == "notes":
        return fc.op_notes(task=getattr(args, "task", None),
                           query=args.text or args.name,
                           limit=args.limit)
    # --- P3 (§3.2/§3.4): гейт и отчёт по задаче ---
    if op == "gate":
        raw = getattr(args, "payload", None)
        if raw == "-":
            raw = sys.stdin.read()
        elif raw and Path(raw).exists():
            raw = Path(raw).read_text(encoding="utf-8")
        return fc.op_gate(raw, mode=getattr(args, "gate_mode", None))
    if op == "task_stats":
        return fc.op_task_stats(task=getattr(args, "task", None),
                                fmt=getattr(args, "report_format", "markdown"),
                                limit=args.limit)
    if op == "task_set":
        # Назвать текущую задачу: так работает RAGRAF_TASK без клиента.
        return fc.op_task_set(args.name or args.text or "",
                              clear=bool(getattr(args, "clear", False)))
    if op == "write_check":
        # Сверка плана записи со схемой (U1): закрывает дефект вроде 42S22
        # до выполнения запроса. Колонки передаются строкой через запятую.
        cols: list[str] = []
        if args.columns:
            cols = [c.strip() for c in str(args.columns).split(",") if c.strip()]
        return fc.op_write_check(
            args.table or args.name or args.text or "",
            cols,
            operation=(args.operation or "insert"),
            values=(int(args.values) if getattr(args, "values", None) else None),
        )
    if op == "usage":
        # Статистика обращений (для сводного отчёта об эффекте внедрения).
        return fc.usage_summary(since_session=False)
    if op == "freshness":
        # Свежесть индекса: совпадает ли контрольная сумма корпуса с кодом.
        # При --force проверка идёт минуя кэш — сразу после правки кода.
        return fc.op_freshness(force=bool(getattr(args, "force_check", False)))
    if op == "catalog":
        # Реестр разработок: что индексировано, когда, с какими суммами.
        return fc.op_catalog()
    if op == "index_status":
        return fc.op_index_status()
    if op == "drift_report":
        return fc.op_drift_report(since=args.since, limit=args.limit)
    if op == "drift_ack":
        ids = [int(x) for x in (args.entry_ids or "").split(",") if x.strip().isdigit()]
        return fc.op_drift_ack(ids, note=args.note)
    if op == "live_read":
        return fc.op_live_read(args.path or "", start=args.start, end=args.end)
    if op == "calibrate":
        return fc.op_calibrate(kind=args.kind or "confidence")
    # Эти две операции были объявлены в OPS, реализованы в фасаде и доступны
    # через MCP, но в диспетчере CLI их ветвей не было: вызов доходил сюда
    # и падал в SystemExit(EXIT_USAGE) — то есть верная операция выглядела
    # как неверный аргумент командной строки. Дефект найден прогоном
    # каждой операции из OPS, а не чтением.
    if op == "policy_trace":
        return fc.op_policy_trace(args.name or args.text or "",
                                  action=args.action, depth=args.depth,
                                  limit=args.limit)
    # ---------------- слой живых баз данных ----------------
    if op == "db_schema":
        return fc.op_db_schema(table=args.table, limit=args.limit)
    if op == "db_table":
        return fc.op_db_table(args.table or args.name or args.text or "")
    if op == "db_relations":
        return fc.op_db_relations(args.table or args.name or args.text or "")
    if op == "db_sample":
        return fc.op_db_sample(args.table or args.name or args.text or "")
    if op == "db_drift":
        return fc.op_db_drift(previous_rev=getattr(args, "previous_rev", None))
    if op == "db_cross":
        return fc.op_db_cross()
    if op == "class_usage":
        return fc.op_class_usage(name=args.name, limit=args.limit,
                                 mode=resolve_mode(op, args.mode))
    raise SystemExit(EXIT_USAGE)


def cmd_db(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Сбор структуры живых баз и примеров строк (ФТ слоя баз).

    Отдельная команда: база меняется независимо от кода, и индексировать её при
    каждом прогоне корпуса значило бы платить временем за неизменное. Настройки —
    в `catalog/db_access.json`, который исключён из корпуса (значение доступа не
    попадает в индекс).
    """
    from . import db_access as access_mod
    from . import db_collect as collect_mod
    from .embeddings import Embedder
    from .lexvec import LexVecStore
    from .revisions import GraphWriter

    access = access_mod.DbAccessStore().load()
    if args.dialect:
        access.dialect = args.dialect
    if args.enabled is not None:
        access.enabled = args.enabled
    if args.samples is not None:
        access.samples_enabled = args.samples

    if args.probe_only:
        result = collect_mod.probe(access)
        # Вывод пробы печатается отдельно, а не через `_print`: тот рассчитан на
        # конверт операции (ревизия, режим, происхождение), а проба возвращает
        # простой ответ сервера. Первая редакция передавала его в `_print` и
        # печатала строку «[None] rev=None capability=None» — измерено.
        if args.json:
            _print(result, True)
        else:
            if result.get("ok"):
                print(f"подключение работает: {result.get('dialect')}, "
                      f"сервер {result.get('server')}")
                print(f"  баз видно: {result.get('databases')}; "
                      f"источник доступа: {result.get('source')}; "
                      f"клиент: {result.get('client')}")
            else:
                print(f"подключение не работает: {result.get('reason')}")
                if result.get("action"):
                    print(f"  что сделать: {result.get('action')}")
        return EXIT_OK if result.get("ok") else EXIT_ERROR

    if not access.enabled and args.enabled is None:
        print("сбор баз выключен: включите его в панели (раздел «Базы данных») "
              "или флагом --enabled")
        return EXIT_USAGE

    collected = collect_mod.collect(access, with_samples=args.samples)
    if collected.get("result") is None:
        # Сбор не состоялся: причина печатается словами, а не конвертом.
        if args.json:
            _print(collected, True)
        else:
            print(f"сбор не выполнен: {collected.get('reason')}")
            if collected.get("action"):
                print(f"  что сделать: {collected.get('action')}")
        return EXIT_ERROR

    result = collected["result"]
    if args.json:
        _print(result.to_dict(), True)
    else:
        data = result.to_dict()
        print(f"сбор структуры: баз {data['databases']}, таблиц {data['tables']}, "
              f"колонок {data['columns']}, представлений {data['views']}, "
              f"связей {data['relations']}")
        print(f"отпечаток структуры: {collected['fingerprint'][:16]}…")
        print(f"примеров собрано: {len(collected.get('samples') or {})}"
              + ("" if collected.get("samples_wanted") else " (сбор примеров выключен)"))
        for note in (collected.get("notes") or [])[:5]:
            print(f"  · {note}")
        for problem in (collected.get("errors") or [])[:5]:
            print(f"  ! {problem}")

    # Запись идёт в АКТИВНУЮ ревизию: слой баз — часть того же индекса, а не
    # отдельное хранилище. Иначе операции не увидели бы структуру.
    from .facade import Facade

    facade = Facade(cfg, tool="cli", telemetry=False, watch=False)
    try:
        rev = facade.current_rev()
    finally:
        facade.close()
    if not rev:
        print("в корпусе нет ревизии: сначала выполните index")
        return EXIT_ERROR

    graph = GraphWriter(cfg)
    lex = LexVecStore(cfg)
    embedder = Embedder(cfg, allow_download=False)
    stats = collect_mod.write(access, rev, collected, graph, lex, embedder)
    print(f"записано в индекс под ревизию {rev}: таблиц {stats['graph']['tables']}, "
          f"связей {stats['graph']['relations']}, закрытых колонок "
          f"{stats['graph']['sensitive_columns']}, чанков {stats['lexvec']['chunks']}")
    if not args.json:
        print("подсказка: структура смотрится операциями db_schema, db_table, "
              "db_relations, db_sample, db_drift, db_cross")
    return EXIT_OK


def cmd_usage(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Статистика использования RAGRAF: журнал обращений и оценка эффекта.

    Отвечает на вопрос заказчика «сколько было обращений и какую пользу они дали».
    Все величины по токенам и строкам — ОЦЕНКА (помечается в выводе), числа
    обращений и фактов — измеренные.
    """
    from .facade import Facade

    facade = Facade(cfg, tool="cli", telemetry=True)
    try:
        if args.reset:
            n = facade.telemetry.reset()
            print(f"журнал обращений очищен: удалено записей {n}")
            return 0

        summary = facade.usage_summary(
            agent=getattr(args, "agent", None),
            agent_kind=getattr(args, "agent_kind", None),
            task=getattr(args, "task", None),
        )
        rev = facade.current_rev()
        print(f"проект: {cfg.project} | ревизия индекса: {rev}")
        # Фильтр печатается явно: без этого читатель не отличит общую
        # статистику от отфильтрованной по конкретному агенту.
        flt = summary.get("filter") or {}
        if flt.get("agent") or flt.get("agent_kind") or flt.get("task"):
            parts = []
            if flt.get("agent"):
                parts.append(f"агент={flt['agent']}")
            if flt.get("agent_kind"):
                parts.append(f"семейство={flt['agent_kind']}")
            if flt.get("task"):
                parts.append(f"задача={flt['task']}")
            print(f"фильтр: {', '.join(parts)}")
        else:
            print("фильтр: нет (все агенты, все задачи)")
        print(f"сессий использования: {summary['sessions_total']}"
              + (f" из {summary['sessions_total_all']} всего"
                 if summary.get("sessions_total_all", 0) != summary["sessions_total"]
                 else ""))
        print(f"всего обращений к операциям: {summary['calls_total']}")
        print(f"  ошибок: {summary['errors']}")
        print(f"фактов получено: {summary['facts_total']}, из них со ссылкой path:line: "
              f"{summary['facts_with_span']} ({summary['verifiable_ratio']:.1%})")
        print(f"объём ответов: {summary['result_chars_total']} символов "
              f"(~{summary['est_tokens_total']} токенов, ОЦЕНКА)")
        print(f"  из них заменяют чтение файлов: {summary['result_chars_replacing_reading']} символов "
              f"(~{summary['est_tokens_replacing_reading']} токенов, ~{summary['est_lines_not_read']} строк кода)")
        # Метрики эффекта (P2-4). Печатаются отдельной строкой, потому что по
        # ним принимают решение «индекс окупается»: доля вызовов с фактами,
        # честные «не найдено» и ответы с пометкой устаревания.
        print(f"эффект: попаданий {summary.get('hit_rate', 0):.1%} "
              f"({summary.get('calls_with_facts', 0)} из {summary['calls_total']} вызовов), "
              f"среднее время {summary.get('avg_ms', 0)} мс, "
              f"«не найдено» {summary.get('misses', 0)}, "
              f"устаревших ответов {summary.get('stale_served', 0)}")

        # --- разбивка по агентам ---
        # Ответ на вопрос «какой агент использовал RAGRAF»: без этой
        # таблицы видна только общая цифра, а она не различает агентов.
        by_agent = summary.get("by_agent") or []
        if by_agent:
            print()
            print("по агентам:")
            print(f"  {'агент':<16} {'семейство':<11} {'сессий':>7} "
                  f"{'вызовов':>8} {'фактов':>7} {'символов':>9} "
                  f"{'ошибок':>7}")
            for row in by_agent:
                print(f"  {str(row['agent'])[:16]:<16} "
                      f"{str(row['agent_kind'])[:11]:<11} "
                      f"{row['sessions']:>7} {row['calls']:>8} "
                      f"{row['facts']:>7} {row['chars']:>9} "
                      f"{row['errors']:>7}")
        # ACCESS-VIEW-PATCH
        # Разрез по выданному доступу: владелец спрашивает не только «какой
        # агент», но и «по какому праву доступа работали». Записи без доступа —
        # отдельная строка: это не потеря данных, а вызовы без выданного доступа
        # (CLI, прежние журналы, тесты).
        by_access = summary.get("by_access") or []
        if by_access:
            print()
            print("по выданному доступу:")
            print("  доступ                    вызовов"
                  "  успешных    с ошибкой   фактов")
            for row in by_access[:12]:
                label = str(row.get('label') or '(без доступа)')
                calls = row.get('calls', 0)
                good = row.get('ok', 0)
                bad = row.get('failed', 0)
                facts = row.get('facts', 0)
                print("  %-24s %7d %10d %11d %8d"
                      % (label[:24], calls, good, bad, facts))

        by_kind = summary.get("by_agent_kind") or []
        if by_kind:
            print()
            print("по семействам агентов:")
            for row in by_kind:
                print(f"  {row['agent_kind']:<11} агентов {row['agents']}, "
                      f"сессий {row['sessions']}, вызовов {row['calls']}, "
                      f"~{row['est_lines_not_read']} строк не читалось")

        # --- разбивка по задачам ---
        # Это ответ на вопрос «сколько RAGRAF сделал для этой задачи»:
        # в SagaAI один вызов инструмента = отдельный процесс, поэтому
        # по сессиям задачу не собрать — нужна общая метка.
        by_task = summary.get("by_task") or []
        if by_task:
            print()
            print("по задачам:")
            print(f"  {'задача':<28} {'вызовов':>8} {'процессов':>10} "
                  f"{'фактов':>7} {'символов':>9} {'строк':>7}")
            for row in by_task:
                name = str(row["task"])[:28]
                print(f"  {name:<28} {row['calls']:>8} "
                      f"{row['processes']:>10} {row['facts']:>7} "
                      f"{row['chars']:>9} "
                      f"{row['est_lines_not_read']:>7}")

        print()
        print("по операциям:")
        print(f"  {'операция':<14} {'вызовов':>8} {'найдено':>8} {'фактов':>7} {'со строкой':>11} "
              f"{'символов':>9} {'сред.,мс':>9}")
        for row in summary["by_op"]:
            print(f"  {row['op']:<14} {row['calls']:>8} {row['found_calls']:>8} "
                  f"{row['facts']:>7} {row['with_span']:>11} {row['chars']:>9} "
                  f"{(row['avg_ms'] or 0):>9.1f}")
        print()
        print("запросы по объектам (аргументы вызовов):")
        for row in summary.get("by_arg", []):
            if not row["arg_digest"]:
                continue
            print(f"  {row['op']:<14} {row['calls']:>4}×  {row['arg_digest']}")
        print()
        print(summary["estimate_note"])
        return 0
    finally:
        facade.close()


def cmd_init(args: argparse.Namespace) -> int:
    """Создать конфигурацию корпуса для нового проекта.

    Зачем эта команда. До неё подключить новый проект было НЕЛЬЗЯ:
    `register` требует готовый конфиг (аргумент `--project`), а создать конфиг
    было нечем. На этом споткнулся агент MD.Viewer: ему сказано
    «зарегистрировать разработку», а выполнить это нечем.

    Команда работает БЕЗ загруженного конфига: она его и создаёт.
    """
    from . import init_corpus

    root = Path(args.root)
    project = args.name or root.name
    target = Path(args.config) if args.config else None
    try:
        path, scan = init_corpus.create_config(
            root, project=project, config_path=target,
            overwrite=bool(args.force),
        )
    except FileExistsError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:
        print(f"не удалось собрать корпус: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"конфигурация создана: {path}")
    print(f"  проект:  {project}")
    print(f"  корень:  {util.to_posix(scan.root)}")
    print(f"  файлов:  {scan.total_files}")
    print(f"  объём:   {scan.total_bytes / 1024:.0f} КБ")
    print("  языки:")
    for lang, count in sorted(scan.languages.items(), key=lambda kv: -kv[1]):
        print(f"    {lang:<10} {count}")
    if scan.exclude_dirs:
        print(f"  исключено каталогов: {len(scan.exclude_dirs)} "
              f"({', '.join(sorted(scan.exclude_dirs)[:6])}"
              + ("…" if len(scan.exclude_dirs) > 6 else ")"))
    if scan.secret_patterns:
        print(f"  секретных файлов по шаблонам: {len(scan.secret_patterns)}")
    print()
    print("Дальше — проверить список include/exclude в файле, затем зарегистрировать")
    print(f"и проиндексировать:")
    print(f"  python tools/ragraf.py -p {util.to_posix(path)} register")
    print(f"  python tools/ragraf.py -p {util.to_posix(path)} index")
    return EXIT_OK


def cmd_register(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Зарегистрировать разработку в реестре: отдельный учёт с контрольной суммой.

    Регистрация НЕ индексирует: она фиксирует, где лежит разработка и какие
    у неё суммы СЕЙЧАС. Это позволяет отличить «заведена в реестр» от
    «проиндексирована» (у второй есть ревизия).
    """
    from . import catalog as catalog_mod
    from . import watch as watch_mod

    category = args.category or cfg.project
    entry = watch_mod.register_project(cfg, args.project, category=category)
    catalog = catalog_mod.Catalog()
    summary = catalog.summary()
    print(f"разработка зарегистрирована: {entry.category}")
    print(f"  корень:    {entry.root}")
    print(f"  конфиг:    {entry.config}")
    print(f"  сумма:     {entry.corpus_hash}")
    print(f"  файлов:    {entry.file_count}")
    print(f"  ревизия:   {entry.rev_id or '(ещё не индексировалась)'}")
    print(f"в реестре разработок: {summary['count']}")
    return 0


def cmd_check(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Сверить контрольную сумму и при расхождении переиндексировать.

    Это та самая проверка, которую выполняет сторож до работы агента.
    Возвращает код 0, если индекс актуален, и 0 с пометкой, если он был
    обновлён: расхождение — не ошибка, а штатная ситуация, требующая работы.
    """
    from . import watch as watch_mod

    fresh = watch_mod.check(
        cfg,
        args.category,
        auto_reindex=not args.no_reindex,
        force=True,
        use_cache=False,
    )
    print(f"разработка: {fresh.category}")
    print(f"  ревизия индекса:        {fresh.rev_id or '(нет)'}")
    print(f"  сумма корпуса сейчас:   {fresh.corpus_hash}")
    print(f"  сумма при индексации:   {fresh.expected_hash or '(нет)'}")
    changed_any = fresh.changed or bool(
        fresh.expected_config_hash and fresh.config_hash != fresh.expected_config_hash
    )
    print(f"  изменилось после индексации: {'да' if changed_any else 'нет'}")
    if fresh.changes_threshold:
        # Накопление показывается всегда: это главный показатель того,
        # насколько индекс отстал на длинной сессии.
        print(f"  правок с последней индексации: {fresh.changes_since_index} "
              f"(порог быстрой {fresh.changes_threshold}, полного пересбора {fresh.full_threshold})")
        print(f"  возраст индекса: {fresh.last_index_age_min} мин"
              + (" — ДОСТИГНУТ предел интервала" if fresh.interval_reached else ""))
    if fresh.threshold_reached:
        print(f"  достигнут порог: {fresh.threshold_reached}")
    if fresh.expected_config_hash and fresh.config_hash != fresh.expected_config_hash:
        print("  конфигурация корпуса изменилась: ревизия несопоставима с новой")
    if fresh.changed:
        print(f"  файлов: изменено {fresh.changed_files}, добавлено {fresh.added_files}, "
              f"удалено {fresh.removed_files} (всего сейчас {fresh.files_now})")
    if fresh.reindexed:
        print(f"  реиндексация выполнена: ревизия {fresh.reindex_rev} "
              f"за {fresh.reindex_seconds:.1f} с"
              + (f" (режим: {fresh.reindex_mode})" if fresh.reindex_mode else ""))
    elif changed_any and not args.no_reindex:
        print(f"  реиндексация не выполнена: {fresh.note or 'причина не указана'}")
    else:
        print("  реиндексация не требуется")
    if fresh.stale:
        print(f"  ВНИМАНИЕ: индекс устарел — {fresh.note or 'факты могут не соответствовать коду'}")
    return 0


def cmd_catalog(args: argparse.Namespace, cfg: ProjectConfig | None = None) -> int:
    """Показать реестр разработок: раздельный учёт и состояние сумм."""
    from . import catalog as catalog_mod
    from .config import load as load_config

    summary = catalog_mod.Catalog().summary()
    print(f"разработок в реестре: {summary['count']}")
    if not summary["entries"]:
        print("  (реестр пуст; зарегистрируйте разработку командой: ragraf register)")
        return 0
    for row in summary["entries"]:
        print(f"\n  {row['category']}  ({row['project']})")
        print(f"    корень:   {row['root']}")
        print(f"    конфиг:   {row['config']}")
        # Состояние называет РАЗНИЦУ между «заведена» и «проиндексирована».
        # Без этого строка «файлов 54 235, узлов 0» читается как сломанный
        # индекс, хотя означает «индекс ещё не строился».
        state = row.get("state") or ("indexed" if row["rev_id"] else "registered")
        label = {"indexed": "проиндексирована",
                 "registered": "заведена, индекс не строился"}.get(state, state)
        print(f"    состояние реестра: {label}")
        print(f"    ревизия:  {row['rev_id'] or '(нет)'}  capability={row['capability'] or '—'}")
        print(f"    индексация: {row['indexed_at'] or '(не выполнялась)'}")
        print(f"    сумма:    {row['corpus_hash'] or '(нет)'}")
        print(f"    размер:   файлов {row['files']}, узлов {row['nodes']}, рёбер {row['edges']}")
        if row.get("files_now") is not None:
            print(f"    файлов сейчас: {row['files_now']}")
        # Сверка суммы по каждой разработке отдельно: она отвечает, актуален
        # ли её индекс, не запуская индексацию.
        if row.get("hash_matches") is True:
            print("    состояние: индекс актуален (сумма совпадает)")
        elif row.get("hash_matches") is False:
            print("    состояние: ТРЕБУЕТСЯ РЕИНДЕКСАЦИЯ (код изменился)")
        elif row.get("check_error"):
            print(f"    состояние: проверить не удалось ({row['check_error']})")
        if row.get("config_matches") is False:
            print("    конфигурация изменилась: ревизия несопоставима")
        print(f"    прогонов записано: {row['runs']}")
    return 0


def cmd_ensure(args: argparse.Namespace) -> int:
    """Привести RAGRAF в соответствие проекту, из которого идёт запрос.

    Зачем отдельная команда. Сторож контрольных сумм уже работает, но только
    для выбранного корпуса: конфиг надо знать заранее. Агент, впервые
    обратившийся к новому проекту, корпуса не знал и RAGRAF не использовал.
    Эта команда закрывает разрыв: по каталогу проекта она сама находит корпус,
    актуализирует отставший индекс или создаёт новый.

    Работает с одним аргументом — каталогом, поэтому её можно вызывать
    первым шагом любой задачи, не выбирая конфиг заранее.
    """
    from . import ensure as ensure_mod

    outcome = ensure_mod.ensure(
        Path(args.root),
        create=not args.no_create,
        index=not args.no_index,
        force_reindex=args.force,
    )
    data = outcome.to_dict()

    if args.json:
        _print(data, True)
        return EXIT_OK if data["status"] != "error" else EXIT_ERROR

    labels = {
        "actual": "индекс актуален",
        "reindexed": "индекс отставал и пересобран",
        "created": "корпус создан и проиндексирован",
        "not_a_project": "каталог не является проектом: корпус не создавался",
        "error": "ОШИБКА",
    }
    print(f"[ensure] {labels.get(data['status'], data['status'])}")
    print(f"  каталог:    {data['root']}")
    if data["category"]:
        print(f"  разработка: {data['category']}")
    if data["config"]:
        print(f"  конфиг:     {data['config']}")
    if data["rev_id"]:
        print(f"  ревизия:    {data['rev_id']}")
    if data["files"]:
        print(f"  файлов:     {data['files']}")
    if data["reindex_mode"]:
        print(f"  режим пересбора: {data['reindex_mode']} "
              f"({data['reindex_seconds']:.1f} с)")
    if data["reason"]:
        print(f"  причина:    {data['reason']}")
    for note in data["notes"]:
        print(f"  примечание: {note}")
    print(f"  время:      {data['seconds']:.2f} с")
    return EXIT_OK if data["status"] != "error" else EXIT_ERROR

def cmd_status(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    from .facade import Facade

    fc = Facade(cfg)
    try:
        data = fc.op_index_status()
    finally:
        fc.close()
    _print(data, args.json)
    return EXIT_OK


def cmd_drop(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Удалить данные текущей ревизии из хранилищ (история сохраняется)."""
    from .revisions import ControlStore

    cs = ControlStore(cfg)
    try:
        rev = cs.current_rev()
        if not rev:
            print("активной ревизии нет")
            return EXIT_OK
        cs._purge_rev_data(rev)
        cs.conn.execute("UPDATE rev_history SET state = 'rotated' WHERE rev_id = ?", (rev,))
        cs.conn.commit()
        if cfg.rev_file.exists():
            cfg.rev_file.unlink()
        print(f"данные ревизии {rev} удалены (запись в истории сохранена)")
    finally:
        cs.close()
    return EXIT_OK


def cmd_selftest(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Самопроверка окружения: зависимости, парсер, вектор, инструменты."""
    from .pipeline import collect_extractor_versions

    versions = collect_extractor_versions(cfg)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    add("python", True, versions.get("python", "?"))
    add("tree_sitter", versions.get("tree_sitter") != "MISSING", versions.get("tree_sitter", "?"))
    add("php_grammar", versions.get("tree_sitter_php_grammar") == "available",
        versions.get("tree_sitter_php_grammar", "?"))
    add("sqlite_vec", versions.get("sqlite_vec") != "MISSING", versions.get("sqlite_vec", "?"))
    add("fastembed", versions.get("fastembed") != "MISSING", versions.get("fastembed", "?"))
    add("tiktoken", versions.get("tiktoken") != "MISSING", versions.get("tiktoken", "?"))

    # FTS5 и загрузка расширений
    import sqlite3

    conn = sqlite3.connect(":memory:")
    fts = conn.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]
    add("fts5", bool(fts), "ENABLE_FTS5=1" if fts else "FTS5 недоступен")
    conn.close()

    # vec0 в реальном соединении
    from . import store as st

    probe = st.connect(cfg.ragraf_dir / "selftest.sqlite")
    ok_vec = st.try_load_extension(probe, "vec0")
    add("vec0_loadable", ok_vec, "sqlite-vec загружен" if ok_vec else "расширение не загружено")
    probe.close()
    try:
        (cfg.ragraf_dir / "selftest.sqlite").unlink(missing_ok=True)
    except OSError:
        pass

    add("joern", versions.get("joern") != "MISSING", versions.get("joern", "?"))
    add("java", versions.get("java") != "MISSING", versions.get("java", "?"))

    from .embeddings import Embedder

    emb = Embedder(cfg)
    status = emb.status()
    add("embedding_model", status.available, status.reason or f"{status.model} доступна")

    data = {"op": "selftest", "version": __version__, "checks": checks,
            "versions": versions, "all_ok": all(c["ok"] for c in checks)}
    if args.json:
        print(util.dumps(data, indent=2))
    else:
        print(f"RAGRAF {__version__} — самопроверка")
        for c in checks:
            mark = "OK  " if c["ok"] else "НЕТ "
            print(f"  [{mark}] {c['check']:<18} {c['detail']}")
        print(f"  версии: {util.dumps({k: v for k, v in versions.items() if k not in ('python',)})}")
        print(f"  итог: {'всё доступно' if data['all_ok'] else 'есть недоступные компоненты (не критично для базового слоя)'}")
    return EXIT_OK


# --------------------------------------------------------------------------
# Разбор аргументов
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ragraf",
        description="RAGRAF — вспомогательная база знаний о коде для LLM-агентов",
    )
    # --project необязателен: команда init работает без конфига.
    # Для остальных команд он требуется, и это проверяется в main().
    p.add_argument("--project", "-p", required=False, default=None,
                   help="путь к конфигурации корпуса (config/revoap.yaml)")
    p.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    p.add_argument("--version", action="version", version=f"ragraf {__version__}")

    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="полная индексация корпуса")
    pi.add_argument("--no-vectors", action="store_true", help="без векторного слоя (быстрее)")
    pi.add_argument("--limit-files", type=int, default=None, help="ограничить число файлов (пилотная ревизия)")
    pi.set_defaults(func=cmd_index)

    pr = sub.add_parser("reindex", help="переиндексация + архивация журнала расхождений")
    pr.add_argument("--no-vectors", action="store_true")
    pr.add_argument("--limit-files", type=int, default=None)
    pr.set_defaults(func=cmd_reindex)

    pd = sub.add_parser("drift", help="обнаружить различия индекса и кода (индекс не меняется)")
    pd.add_argument("--scan", action="store_true", help="полное сравнение по хэшам")
    pd.add_argument("--detected-by", default="compare",
                    choices=["hook:session-start", "compare", "git-diff", "db-schema-diff", "manual"])
    pd.add_argument("--note", default=None)
    pd.set_defaults(func=cmd_drift)

    pq = sub.add_parser("query", help="операции фасада (фиксированный набор)")
    pq.add_argument("op", choices=OPS)
    pq.add_argument("text", nargs="?", default=None)
    pq.add_argument("--name")
    pq.add_argument("--fqn")
    pq.add_argument("--target")
    pq.add_argument("--path")
    pq.add_argument("--table")
    pq.add_argument("--scope")
    pq.add_argument("--depth", type=int, default=1)
    # Допустимые значения --mode зависят от операции (MODE_CHOICES). Общий
    # фиксированный список не годился: `table_usage --mode write` и
    # `class_usage --mode missing` из инструкции проекта отклонялись
    # argparse ещё до запуска операции.
    pq.add_argument("--mode", default=None, choices=MODE_ALL)
    pq.add_argument("--action", default=None,
                    help="policy_trace: имя действия, решение о котором нужно проследить")
    pq.add_argument("--direction", default="back", choices=["back", "forward"])
    pq.add_argument("--style", default="compact", choices=["compact", "signatures"])
    pq.add_argument("--detail", default="normal", choices=["normal", "deep"])
    pq.add_argument("--hard", action="store_true", help="только доказанные связи")
    pq.add_argument("--budget", type=int, default=2000, help="бюджет токенов для repo_map")
    pq.add_argument("--limit", type=int, default=50)
    pq.add_argument("--since", type=int, default=None)
    pq.add_argument("--since-rev", default=None,
                    help="diff: ревизия, с которой сравнивать (по умолчанию предыдущая)")
    pq.add_argument("--tags", default="", help="note: метки через запятую")
    pq.add_argument("--task", default=None, help="note/notes: метка задачи")
    # P2-2: готовый ответ вместо структурного результата. Оболочка (op, ревизия,
    # capability, found) остаётся — это контракт, по которому клиент понимает,
    # на какую ревизию опирается ответ.
    pq.add_argument("--answer", action="store_true",
                    help="вернуть сформулированный ответ со ссылками (P2-2)")
    pq.add_argument("--entry-ids", default=None)
    pq.add_argument("--start", type=int, default=None)
    pq.add_argument("--end", type=int, default=None)
    pq.add_argument("--kind", default=None)
    pq.add_argument("--note", default=None)
    # `search --with-text`: текст хитов из индекса одним вызовом. Замер rev-0031:
    # без него за 20 хитов приходилось читать 20 файлов вторым шагом.
    pq.add_argument("--with-text", action="store_true",
                    help="добавить к каждому хиту поиска фрагмент текста из индекса")
    pq.add_argument("--queries", default=None,
                    help="search_multi: несколько запросов через `|`")
    pq.add_argument("--regex", action="store_true",
                    help="search_multi: считать запрос регулярным выражением")
    pq.add_argument("--text-width", type=int, default=160,
                    help="длина фрагмента в символах (с --with-text)")
    # `code --source`: по умолчанию текст берётся из индекса, `disk` — прежнее
    # поведение (чтение рабочего дерева).
    pq.add_argument("--source", default="auto", choices=["auto", "index", "disk"])
    pq.add_argument("--include-containers", action="store_true",
                    help="вернуть вложенные спаны (класс-контейнер отдаётся шапкой)")
    pq.add_argument("--columns", default=None,
                    help="write_check: список колонок через запятую")
    pq.add_argument("--values", default=None, help="write_check: сколько значений в запросе")
    pq.add_argument("--operation", default="insert", choices=["insert", "update", "delete"],
                    help="write_check: вид записи")
    pq.add_argument("--force", dest="force_check", action="store_true",
                    help="freshness: проверить минуя кэш")
    # P3 (§3.2/§3.4): гейт и отчёт по задаче.
    pq.add_argument("--payload", default=None,
                    help="gate: JSON вызова (файл, '-' для stdin или строка)")
    pq.add_argument("--gate-mode", dest="gate_mode", default=None,
                    choices=["warn", "ask", "deny", "off"],
                    help="gate: режим решения (по умолчанию из RAGRAF_GATE_MODE или ask)")
    pq.add_argument("--hook", action="store_true",
                    help="gate: ответ в формате хука DSH (stdout JSON, отказ — код 2)")
    pq.add_argument("--format", dest="report_format", default="markdown",
                    choices=["markdown", "json"],
                    help="task_stats: вид отчёта")
    pq.add_argument("--clear", action="store_true",
                    help="task_set: снять метку задачи")
    pq.set_defaults(func=cmd_query)

    ps = sub.add_parser("status", help="состояние индекса")
    ps.set_defaults(func=cmd_status)

    pdr = sub.add_parser("drop", help="удалить данные текущей ревизии (история сохраняется)")
    pdr.set_defaults(func=cmd_drop)

    pst = sub.add_parser("selftest", help="самопроверка окружения")
    pst.set_defaults(func=cmd_selftest)

    pu = sub.add_parser("usage", help="статистика обращений и оценка эффекта внедрения")
    pu.add_argument("--reset", action="store_true",
                    help="очистить журнал обращений (для чистого замера «до/после»)")
    # Фильтры по агенту: владелец хочет видеть как каждого агента отдельно,
    # так и все агенты AutoClaw вместе, и при этом общую картину.
    pu.add_argument("--agent", default=None,
                    help="только этот агент (например AutoClaw)")
    pu.add_argument("--agent-kind", default=None,
                    help="только это семейство: autoclaw, external, internal")
    # Метка задачи: собирает статистику по ОДНОЙ задаче, хотя каждый вызов
    # инструмента запускает отдельный процесс. Значение по умолчанию пустое —
    # тогда показываются все задачи.
    pu.add_argument("--task", default=None,
                    help="только эта задача (метка RAGRAF_TASK)")
    pu.set_defaults(func=cmd_usage)

    pdb = sub.add_parser("db", help="сбор структуры живых баз и примеров строк")
    pdb.add_argument("--probe-only", action="store_true",
                     help="только проверить подключение, ничего не собирать")
    pdb.add_argument("--dialect", choices=["mysql", "postgresql"],
                     help="перекрыть диалект из настроек")
    pdb.add_argument("--enabled", dest="enabled", action="store_true", default=None,
                     help="включить сбор на этот запуск")
    pdb.add_argument("--no-enabled", dest="enabled", action="store_false",
                     help="выключить сбор на этот запуск")
    pdb.add_argument("--samples", dest="samples", action="store_true", default=None,
                     help="собрать примеры строк (по умолчанию — как в настройках)")
    pdb.add_argument("--no-samples", dest="samples", action="store_false",
                     help="не собирать примеры строк")
    pdb.add_argument("--json", action="store_true")
    pdb.set_defaults(func=cmd_db, needs_config=True)

    preg = sub.add_parser("register", help="зарегистрировать разработку в реестре")
    preg.add_argument("--category", default=None, help="имя разработки (по умолчанию имя проекта)")
    preg.set_defaults(func=cmd_register)

    # init — ЕДИНСТВЕННАЯ команда без --project: она его и создаёт.
    pinit = sub.add_parser(
        "init",
        help="создать конфигурацию корпуса для нового проекта",
        description=("Осматривает каталог проекта и собирает конфигурацию корпуса: "
                     "языки, включения, исключения, пороги. Работает без готового "
                     "конфига — этим и отличается от остальных команд."),
    )
    pinit.add_argument("root", help="каталог проекта")
    pinit.add_argument("--name", default=None,
                       help="имя разработки (по умолчанию имя каталога)")
    pinit.add_argument("--config", default=None,
                       help="куда записать конфиг (по умолчанию в config/)")
    pinit.add_argument("--force", action="store_true",
                       help="перезаписать существующий конфиг")
    pinit.set_defaults(func=cmd_init, needs_config=False)

    pchk = sub.add_parser("check", help="сверить контрольную сумму и реиндексировать при изменениях")
    pchk.add_argument("--category", default=None, help="имя разработки")
    pchk.add_argument("--no-reindex", action="store_true", help="только проверить, не индексировать")
    pchk.set_defaults(func=cmd_check)

    pens = sub.add_parser(
        "ensure",
        help="найти корпус по каталогу проекта, актуализировать или создать его",
    )
    pens.add_argument("root", nargs="?", default=".",
                      help="каталог проекта (по умолчанию текущий)")
    pens.add_argument("--no-create", action="store_true",
                      help="не создавать корпус для незнакомого каталога")
    pens.add_argument("--no-index", action="store_true",
                      help="только проверить состояние, не индексировать")
    pens.add_argument("--force", action="store_true",
                      help="проверить свежесть минуя кэш")
    pens.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    pens.set_defaults(func=cmd_ensure, needs_config=False)

    pcat = sub.add_parser("catalog", help="реестр разработок: что индексировано и с какими суммами")
    pcat.set_defaults(func=cmd_catalog, needs_config=False)

    return p




def _run_without_config(args: argparse.Namespace, func, on_success=None) -> int:
    """Выполнить команду, которой конфигурация корпуса не требуется.

    Таких команд четыре, и у каждой своя причина: `init` конфиг создаёт,
    `catalog` читает реестр целиком, `selftest` проверяет окружение,
    `ensure` сам находит корпус по каталогу. Раньше их общий случай был
    написан только для `init`, а остальные требовали `--project` — из-за
    этого документированный первый шаг `ragraf.py catalog` не работал.

    Про `selftest` отдельно. Он назван в этом списке, но `needs_config=False`
    для него НЕ выставлен (см. разбор аргументов), и это не опечатка: сам
    тест требует конфигурацию корпуса — ему нужны пути к хранилищам и
    параметры эмбеддингов, которые берутся только из неё. Поэтому вызов
    `ragraf selftest` без `--project` отвергается кодом 2 с подсказкой,
    а не падает внутри. Формулировка в этом списке сохранена как описание
    общего механизма: команд без конфига — три.
    """
    try:
        code = int(func(args))
    except KeyboardInterrupt:
        print("прервано", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:
        print(f"ошибка выполнения: {type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("RAGRAF_DEBUG"):
            raise
        return EXIT_ERROR
    if on_success is not None:
        on_success()
    return code


def _force_utf8_output() -> None:
    """Сделать вывод UTF-8, не полагаясь на локаль процесса.

    Зачем. На этой машине дочерний процесс, запущенный из Python без
    явной кодировки, получает cp1252. Весь текст RAGRAF — русский, поэтому
    `--help` и любой ответ с русским текстом падали в argparse с
    `UnicodeEncodeError: 'charmap' codec can't encode characters` и кодом 1:
    агент видел трассировку вместо справки и считал инструмент сломанным.

    `PYTHONIOENCODING` спасает только при непустом окружении, но внешний
    вызов может его и не поставить, поэтому потоки перенастраиваются явно.
    Ошибка замены не глушится: если потока нет (например вывод перенаправлен
    в объект без `reconfigure`), это нормальный случай и он игнорируется.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _remember_interpreter() -> None:
    """Запомнить интерпретатор, на котором RAGRAF УСПЕШНО выполнился.

    Зачем. Внешние инструменты (например функция RAGRAF в SagaAI) должны
    запускать дочерний процесс тем же интерпретатором, иначе не найдут
    зависимости RAGRAF. В проекте SagaAI это давало ошибку
    «не установлен PyYAML»: там свой `.venv` без нужных пакетов.

    Путь записывается в файл `.interpreter` рядом с конфигом, а не
    в переменную окружения: переменную процесса не видно из другого
    процесса, а файл читается всегда.

    Почему запись только при успехе. Раньше файл перезаписывался в самом
    начале `main`, ДО разбора аргументов и загрузки конфига. Тогда неудачный
    запуск чужим интерпретатором (без PyYAML) записывал в маркер путь, который
    не работает, и этот маркер оставался таким навсегда: каждый следующий
    вызов инструмента падал с «ошибка конфигурации», хотя рабочий Python
    на машине есть. Теперь маркер — свидетельство работающего интерпретатора,
    а не след последней попытки.
    """
    import pathlib as _pathlib
    import sys as _sys

    try:
        target = _pathlib.Path(__file__).resolve().parents[2] / ".interpreter"
        current = (target.read_text(encoding="utf-8").strip()
                   if target.is_file() else "")
        me = _sys.executable or ""
        # Лишняя запись не нужна: файл не должен дёргать сторожей свежести
        # и обновлять время изменения без причины.
        if current == me:
            return
        target.write_text(me, encoding="utf-8", newline="\n")
    except Exception:
        # Неудача записи не должна мешать работе RAGRAF: это вспомогательные
        # сведения, а не часть индексации.
        pass


def main(argv: list[str] | None = None) -> int:
    # UTF-8 на вывод ставится ДО разбора аргументов: иначе справка и любой
    # русский текст падают с UnicodeEncodeError при не-UTF8 локали процесса.
    _force_utf8_output()

    parser = build_parser()
    args = parser.parse_args(argv)

    # Команды, которым конфигурация корпуса не нужна, выполняются без неё:
    # у `init` её ещё нет, `catalog` и `selftest` работают с реестром и
    # окружением целиком, `ensure` сам находит корпус по каталогу.
    # Признак ставится в разборе аргументов (`needs_config=False`).
    if getattr(args, "needs_config", True) is False:
        func = getattr(args, "func", None)
        # Маркер интерпретатора пишется только после успешного выполнения:
        # иначе один неудачный запуск чужим Python ломал бы все следующие.
        if func is cmd_init:
            return _run_without_config(args, func, _remember_interpreter)
        return _run_without_config(args, func)

    try:
        cfg = load_config(args.project)
    except ConfigError as exc:
        print(f"ошибка конфигурации: {exc}", file=sys.stderr)
        if args.project is None:
            print("подсказка: конфиг корпуса не указан. Для известного проекта "
                  "конфиг берётся из реестра командой:\n"
                  "  python tools/ragraf.py ensure <каталог проекта>\n"
                  "для незнакомого каталога — тем же ensure, он создаст корпус;\n"
                  "вручную конфиг создаётся так:\n"
                  "  python tools/ragraf.py init <каталог проекта>",
                  file=sys.stderr)
        return EXIT_USAGE
    except (TypeError, OSError) as exc:
        # Аргумент --project не указан: load_config получает None и падает
        # внутри. Это должно быть ПОНЯТНОЙ подсказкой, а не трассировкой:
        # агент видит трассировку как поломку инструмента и отказывается
        # от RAGRAF вместо того, чтобы создать конфигурацию.
        print("не указан проект: нужен аргумент --project.\n"
              "Для известного проекта конфиг находится по каталогу, а отставший\n"
              "индекс актуализируется — одной командой:\n"
              "  python tools/ragraf.py ensure <каталог проекта>\n"
              "Она же создаст корпус, если каталог ещё не заведён.\n"
              "Вручную конфигурация создаётся командой init:\n"
              "  python tools/ragraf.py init <каталог проекта>",
              file=sys.stderr)
        if os.environ.get("RAGRAF_DEBUG"):
            raise
        return EXIT_USAGE
    try:
        code = int(args.func(args, cfg))
    except KeyboardInterrupt:
        print("прервано", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:
        print(f"ошибка выполнения: {type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("RAGRAF_DEBUG"):
            raise
        return EXIT_ERROR
    # Маркер интерпретатора обновляется только успешным запуском — см.
    # _remember_interpreter: иначе один неудачный запуск чужим Python
    # ломал бы все следующие вызовы инструмента.
    _remember_interpreter()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
