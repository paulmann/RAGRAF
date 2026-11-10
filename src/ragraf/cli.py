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
# Перечни значений параметров живут в фасаде: оболочка берёт их оттуда, чтобы
# перечень не разошёлся с проверкой (одно свойство — одна точка).
from .facade import OP_CHOICES, IndexNotBuilt

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
    # Этап 8: доказательство у каждого ребра — отдельной операцией.
    "edge_evidence",
    "code", "repo_map", "schema", "table_usage", "routes", "index_status",
    "drift_report", "drift_ack", "live_read", "calibrate", "write_check", "usage",
    "freshness", "catalog", "policy_trace", "class_usage",
    # Маршрут к локальному индексу (G1): где лежит и что отвечает.
    "route",
    # Соседние контуры: готовая реализация того же стека в других корпусах.
    "neighbors",
    # Структура корпуса: копии, точки входа, мёртвый код (P1-2).
    "duplicates", "entrypoints", "dead_code",
    # Слой живых баз данных: структура, связи, примеры, сверки.
    "db_schema", "db_table", "db_relations", "db_sample", "db_drift", "db_cross",
    # Сверка замысла и кода по FQN (Enterprise): внешний YAML-манифест проекта
    # против вычисленного графа. Операция только читает манифест и индекс.
    "contract_drift",
    # P2: сравнение ревизий и память о решениях.
    "diff", "note", "notes",
    # Управление списком исключений (уровень pro): что не считать в корпус.
    "excludes",
    # P3 (§3.2/§3.4): гейт использования индекса и отчёт по задаче.
    "gate", "task_stats", "task_set",
    # Внешний доступ (ПРОМТ-И7): единый вход и выгрузка журналов в SIEM.
    # Объявлены в данных слоя изданий (издание enterprise), поэтому на
    # Community отвечают отказом с причиной, а не отсутствуют.
    "sso_login", "sso_map_roles", "siem_export", "siem_test",
    # Закрытый контур (ПРОМТ-И8): офлайн-лицензия и аттестация поставки.
    "license_offline", "attest_bundle",
    # Контур качества (ПРОМТ-И9): набор проверок индекса и разбор прогонов.
    # Прореживание журнала (ПРОМТ-И10) — там же: это тот же контур и тот же
    # журнал, у него нет отдельной возможности издания.
    "quality_suite", "quality_diff", "quality_prune",
    # Подготовка корпуса к индексации (X5): осмотр ДО создания индекса.
    "preflight",
    # Пиннинг подписи операций (X6): состояние пинов и подтверждение человеком.
    # Сводка изменений между выпусками (X10) — та же возможность.
    "contract_pin_status", "contract_pin_approve", "contract_pin_history",
    "contract_pin_sample", "contract_diff",
    # Парный прогон по фиксированным кейсам (X4, блоки 4+7): та же возможность
    # `quality-loop`, отдельная операция — чтобы вердикт по кейсам не смешивался
    # с `quality_diff`, который между ревизиями вердикт не выносит.
    "quality_pair",
    # Трасса подтверждённого сбоя (X7): запись, список и повтор с вердиктом.
    # Возможность `quality-loop` (Pro): журнал сбоев — часть контура.
    "quality_trace",
    # Документный слой (этап «документы», фаза A): адрес, структура, статус.
    "doc_search", "doc_get", "doc_status", "doc_validate", "doc_ocr",
    # Провенанс документов и выводов (X1): редакция на дату и зависимые факты.
    # Обе объявлены возможностью `provenance` (издание Pro), поэтому на Community
    # отвечают отказом с названием издания, а не отсутствуют.
    "doc_asof", "dependents",
    # Каскад устаревания (X7): что пересчитать, если изменился набор
    # входов. Возможность `provenance` (Pro): отчёт и пометки идут парой.
    "stale_cascade",
    # Документный граф (этап 3 плана «Агентские интеграции»): две операции
    # чтения. Обе объявлены возможностью `doc-graph` (издание Pro), поэтому на
    # Community отвечают отказом с причиной, а не отсутствуют: «не положено» и
    # «не написано» — разные ответы.
    "entity_neighbors", "doc_relations",
    # Dual-level поиск (этап 4): два уровня одним ответом. Операция объявлена
    # возможностью `search` (community) — локальный уровень бесплатен, а
    # глобальный закрыт возможностью `doc-graph` ВНУТРИ операции.
    "doc_search_levels",
    # Этап 5: темы и карта документного графа. Обе — возможности `doc-graph`
    # (издание pro): темы строятся по слою, карта считает его состав.
    "themes", "global_overview",
]

# Операции, которые в релизе 1 требуют слоя, добавляемого в M2/M3.
OP_REQUIRES_CPG = {"slice"}

# Значения --mode зависят от операции: у одних это режим обхода графа
# (hard/full), у других — вид обращений к таблице (all/read/write) или вид
# расхождения классов оформления (all/missing/unused). Один общий choices
# отвергал верные команды до запуска: `table_usage --mode write` и
# `class_usage --mode missing` из инструкции проекта падали с кодом 2.
MODE_CHOICES: dict[str, tuple[str, ...]] = {
    "impact": ("full", "hard", "all"),
    # Этап 4: режим поиска. `auto` — прежнее поведение (лексика+вектор), `code` —
    # только материал кода, `docs`/`themes` — dual-level (уровень «тема»).
    "search": ("auto", "code", "docs", "themes"),
    # Уровни поиска: `local` — чанки с адресами, `global` — сущности и связи
    # документного графа, `both` — оба уровня со связями между ними.
    "doc_search_levels": ("local", "global", "both"),
    "table_usage": ("all", "read", "write"),
    "class_usage": ("all", "missing", "unused"),
    "license_offline": ("request", "issue", "accept", "transfer"),
}
# Операции без своего набора (search, symbol, callers, ...) принимают только
# значение по умолчанию: режим у них не меняет смысл ответа.
MODE_DEFAULT_BY_OP: dict[str, str] = {
    "impact": "full",
    "search": "auto",
    "doc_search_levels": "both",
    "table_usage": "all",
    "class_usage": "all",
    "license_offline": "accept",
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
    # Сверка замысла и кода по FQN: готовый текст отчёта с адресами находок.
    # Текст строит модуль возможности (contracts.render), а не CLI: второй
    # редактор отчёта разошёлся бы с машинным ответом.
    if op == "contract_drift" and isinstance(res, dict) and res.get("report"):
        print()
        print(res["report"])
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
        # Доказательность графа (этап 8): виды рёбер и доля эвристики. Ранее
        # печаталось только `edges_stated`/`edges_derived`, и агент не мог отличить
        # 57 406 эвристических вызовов от 11 372 подтверждённых.
        ek = res.get("edge_kinds") or {}
        if ek:
            kinds = ek.get("kinds") or []
            top = ", ".join(f"{k['kind']}={k['count']} "
                            f"({k['share'] * 100:.1f} %)" for k in kinds[:4])
            print(f"  рёбра по видам: {top}")
            print(f"  эвристика: {ek.get('heuristic_count')} из {ek.get('total')} "
                  f"({(ek.get('heuristic_share') or 0) * 100:.1f} %) при бюджете "
                  f"{ek.get('budget_percent')} % — "
                  f"{'ВЫШЕ БЮДЖЕТА' if ek.get('budget_exceeded') else 'в пределах бюджета'}")
            if ek.get("note"):
                print(f"  · доказательность: {ek['note']}")
            # Этап 8: виды ДОКАЗАТЕЛЬСТВА (а не виды рёбер). Числа берутся из
            # того же ответа, что и JSON, поэтому текстовый и машинный вывод
            # не могут разойтись.
            evd = ek.get("evidence") or {}
            if evd.get("kinds"):
                print("  доказательство рёбер: " + ", ".join(
                    f"{item['evidence_kind']}={item['count']}"
                    for item in evd["kinds"][:5]))
                print(f"  рёбра вызова: подтверждено CPG {evd.get('cpg_confirmed', 0)}, "
                      f"выведено {evd.get('derived_call_edges', 0)} из "
                      f"{evd.get('call_edges', 0)} "
                      f"({(evd.get('derived_share') or 0) * 100:.1f} %)")
        # Блок качества (ПРОМТ-И12). JSON и панель несли его с самого начала, а
        # текстовый вывод — нет: наблюдаемость предела журнала, сделанная на
        # этапе И11, до него не дошла, и `query index_status` печатал ревизию,
        # покрытие и стадии без единой строки о качестве. Печатается ТОЛЬКО
        # здесь: формат JSON не меняется, иначе сломались бы читатели, которые
        # разбирают ответ программно.
        quality = res.get("quality") or {}
        journal = quality.get("journal") or {}
        if journal:
            line = (f"  журнал прогонов: {journal.get('runs')} при пределе "
                    f"{journal.get('soft_limit')}")
            if journal.get("over_limit"):
                line += (f" — ВЫШЕ ПРЕДЕЛА на {journal.get('excess')}, "
                         f"quality_prune оставит {journal.get('keep_default')}")
            else:
                line += " — превышения нет"
            if journal.get("error"):
                line += f"  (журнал не прочитан: {journal['error']})"
            print(line)
        chunks = quality.get("chunks") or {}
        if chunks and not chunks.get("error"):
            print(f"  чанки: {chunks.get('total')}  средний {chunks.get('avg_tokens')} токенов"
                  f"  максимум {chunks.get('max_tokens')} при лимите {chunks.get('budget_tokens')}"
                  f"  сверх лимита {chunks.get('over_budget')}")
        elif chunks:
            print(f"  чанки: не прочитаны ({chunks['error']})")
        signatures = quality.get("signatures") or {}
        if signatures and not signatures.get("error"):
            print(f"  сигнатуры: функций {signatures.get('functions')}"
                  f"  обрезано {signatures.get('truncated')}")
        elif signatures:
            print(f"  сигнатуры: не прочитаны ({signatures['error']})")
        for problem in quality.get("problems") or []:
            print(f"  · качество: {problem}")
        # Хранилище (этап 9): объём, свободные страницы и хвосты ротации.
        storage = res.get("storage") or {}
        if storage:
            stores = storage.get("stores") or {}
            parts = []
            for label, data in stores.items():
                if data.get("missing"):
                    parts.append(f"{label}=НЕТ")
                else:
                    parts.append(f"{label}={data.get('mb')} МБ")
            verdict = "ВЫШЕ ПОРОГА" if storage.get("over_budget") else "в пределах"
            joined = ", ".join(parts)
            print(f"  хранилище: {storage.get('total_gb')} ГБ при пороге "
                  f"{storage.get('budget_gb')} ГБ — {verdict} ({joined})")
            if storage.get("orphan_revisions"):
                print(f"  · хвост ротации: {storage.get('orphan_document_rows')} строк "
                      f"документного слоя от {storage.get('orphan_revisions')} ревизий, "
                      f"которых нет в истории")
            if storage.get("note"):
                print(f"  · хранилище: {storage['note']}")
        for st in res.get("stages") or []:
            print(f"    {st['stage']:<8} {st['seconds']:>8.3f}s  items={st['items']}"
                  + (f"  {st['note']}" if st.get("note") else ""))
    elif op == "symbol":
        for r in res:
            print(f"  {r.get('kind')} {r.get('fqn')}  {r.get('path')}:{r.get('start_line')}-{r.get('end_line')}")
    elif op in ("callers", "callees"):
        print(f"  цель {res.get('target')} ({res.get('direction')}, глубина {res.get('depth')}): "
              f"stated={res.get('stated')} derived={res.get('derived')}")
        ev = res.get("evidence") or {}
        if ev.get("total"):
            print(f"  доказательство: " + ", ".join(
                f"{c['evidence_kind']}={c['count']}" for c in (ev.get("counts") or [])[:5])
                + f"; выведено {ev.get('derived', 0)} из {ev.get('total', 0)} "
                  f"({(ev.get('derived_share') or 0) * 100:.1f} %)")
        for it in (res.get("items") or [])[:15]:
            # Этап 8: знак «=» больше не означает «доказано»: ребро может быть
            # `stated`, но выведенным по имени. Поэтому вид назван явно.
            mark = "=" if it.get("evidence_kind") == "cpg_call" else "~"
            print(f"    {mark} [{it.get('level')}] {it.get('fqn')}  {it.get('path')}:{it.get('start_line')}"
                  f"  ({it.get('edge_kind')}, {it.get('evidence_kind')})")
    elif op == "impact":
        print(f"  цель {res.get('target')} режим={res.get('mode')} "
              f"затронуто: {res.get('affected_count')}")
        # Этап 8: чем именно затронуто. Без этой строки `derived: 12` читается как
        # «12 связей», хотя это 12 выводов по имени.
        ekinds = res.get("edge_kinds") or []
        if ekinds:
            share = (res.get("heuristic_share") or 0) * 100
            listed = ", ".join(f"{k['kind']}={k['count']}" for k in ekinds[:5])
            print(f"  связи по видам: {listed}  эвристика: {share:.1f} % "
                  f"при бюджете {res.get('heuristic_budget_percent')} %")
        ev = res.get("evidence") or {}
        if ev.get("total"):
            print("  доказательство связей: " + ", ".join(
                f"{c['evidence_kind']}={c['count']}" for c in (ev.get("counts") or [])[:5])
                + f"; выведено {ev.get('derived', 0)} из {ev.get('total', 0)} "
                  f"({(ev.get('derived_share') or 0) * 100:.1f} %)")
        for f in (res.get("affected_files") or [])[:20]:
            print(f"    {f['path']}  stated={f['stated']} derived={f['derived']}")
    elif op == "edge_evidence":
        _print_edges(res or {}, True, data.get("limits"))
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
    from .catalog import Catalog
    from .indexer import Indexer
    from .watch import category_for

    category = category_for(cfg)
    # Замок ПО КОРПУСУ на всю индексацию. Проверка 06.11.2026: без него два
    # одновременных `index` одного корпуса работали параллельно и оба брали
    # один и тот же `rev-0001`, то есть писали одну ревизию вдвоём.
    with Catalog().corpus_lock(category) as got:
        if not got:
            print(f"  · индексация корпуса «{category}» уже выполняется другим "
                  "процессом: результат не получен, прежний индекс не тронут")
            return 1
        return _run_index_locked(args, cfg, category)


def _run_index_locked(args: argparse.Namespace, cfg: ProjectConfig,
                      category: str) -> int:
    """Тело `index` под взятым замком корпуса."""
    from .catalog import Catalog
    from .indexer import Indexer

    catalog = Catalog()
    idx = Indexer(cfg, heartbeat=lambda: catalog.touch_corpus_lock(category))
    try:
        report = idx.run(with_vectors=not args.no_vectors,
                         limit_files=args.limit_files,
                         doc_graph=bool(getattr(args, "doc_graph", False)))
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
    from .catalog import Catalog
    from .watch import category_for

    category = category_for(cfg)
    # Тот же замок, что у `index`: это второй путь CLI, запускающий индексатор.
    with Catalog().corpus_lock(category) as got:
        if not got:
            print(f"  · индексация корпуса «{category}» уже выполняется другим "
                  "процессом: переиндексация не запущена")
            return 1
        return _run_reindex_locked(args, cfg, category)


def _run_reindex_locked(args: argparse.Namespace, cfg: ProjectConfig,
                        category: str) -> int:
    """Тело `reindex` под взятым замком корпуса."""
    from .catalog import Catalog
    from .drift import archive_and_reset
    from .indexer import Indexer

    catalog = Catalog()
    idx = Indexer(cfg, heartbeat=lambda: catalog.touch_corpus_lock(category))
    try:
        report = idx.run(with_vectors=not args.no_vectors,
                         limit_files=args.limit_files,
                         doc_graph=bool(getattr(args, "doc_graph", False)))
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


def _note_pin_break(cfg: ProjectConfig, name: str, *,
                    reason: str = "unknown_name",
                    detail: str = "имени нет в описании операций") -> None:
    """Отметить неуспешный вызов после расхождения описания.

    Зачем не всегда: до расхождения такая ошибка — обычная ошибка вызова, и
    шуметь о ней в журнале контракта нечего (пин ещё ничего не обещал). Запись
    связывается с последним событием расхождения, чтобы причина читалась вместе
    со следствием. Сбой записи не влияет на код возврата: журнал вспомогателен.

    Причин две и обе из закрытого списка `pin.BREAK_REASONS`: имени нет
    (`unknown_name`) и аргументы не разобраны (`bad_arguments`). Второй случай
    раньше не писался вовсе, хотя объявлен: клиент, живущий по старому
    контракту, чаще падает именно на аргументах, а не на имени.
    """
    conn = None
    try:
        from . import pin as pin_mod
        from . import store as store_mod

        conn = store_mod.connect(cfg.control_db)
        pin_mod.note_break(conn, name, reason=reason, detail=detail)
    except Exception:  # noqa: BLE001 — журнал не имеет права ломать вызов
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — закрытие уже закрытого
                pass


def _attempted_op(argv: list[str]) -> str:
    """Имя операции из аргументов, даже если разбор не удался.

    Нужно журналу контракта: ошибка аргументов сама по себе не говорит, ЧЕЙ
    контракт нарушен. Берётся второе слово после `query`; для прочих команд —
    первое. Служебные слова (`--help`) операцией не считаются.
    """
    words = [item for item in argv if item and not item.startswith("-")]
    if not words:
        return ""
    if words[0] == "query":
        return words[1] if len(words) > 1 else ""
    return words[0]


def _config_from_argv(argv: list[str]) -> str | None:
    """Путь конфига из argv: разбор не удался, а журнал найти где-то надо."""
    for index, item in enumerate(argv):
        if item in ("-p", "--project", "--config") and index + 1 < len(argv):
            return argv[index + 1]
        for prefix in ("--project=", "--config="):
            if item.startswith(prefix):
                return item.split("=", 1)[1]
    return None


def _note_bad_arguments(argv: list[str]) -> None:
    """Отметить `pin_break(bad_arguments)` после расхождения описания.

    Место одно — разбор командной строки: здесь вызов уже отклонён по
    аргументам, и имя операции ещё можно достать из argv. Внутри операции
    такой отказ неотличим от её собственного «вход неверен», а это разные
    вещи: одно — разошедшийся контракт, другое — ошибка в данных.
    """
    name = _attempted_op(argv)
    if not name:
        return
    try:
        cfg = load_config(_config_from_argv(argv))
    except Exception:  # noqa: BLE001 — без конфига журнала нет, вызов не ломаем
        return
    _note_pin_break(cfg, name, reason="bad_arguments",
                    detail="аргументы вызова не разобраны")


def cmd_query(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    from .facade import Facade

    op = args.op
    if op not in OPS:
        print(f"неизвестная операция: {op}. Доступно: {', '.join(OPS)}", file=sys.stderr)
        _note_pin_break(cfg, op)
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


def _index_not_built(exc: Exception) -> int:
    """Ответ на несобранный индекс: назвать состояние и что сделать.

    Код возврата — `EXIT_UNAVAILABLE` (отказ слоя), а не 1: индекс можно собрать,
    и вызывающий должен это видеть. До 04.11.2026 исключение вылетало из
    `Facade.__init__`, то есть до выбора кода, и команда кончалась ошибкой 1.
    """
    print(f"{exc}. Соберите индекс: python tools/ragraf.py -p <конфиг> index "
          f"(либо python tools/ragraf.py ensure <каталог проекта>)", file=sys.stderr)
    return EXIT_UNAVAILABLE


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

    # Пустое окружение — это НЕ «неизвестно»: вызов пришёл из командной
    # строки, и назвать его `ragraf-cli` честнее, чем `unknown`. Замер
    # 23.10.2026: команда `status` писала 42 записи `unknown` за три часа,
    # потому что этот путь не называл себя вовсе.
    agent = (_os.environ.get("RAGRAF_AGENT") or "").strip() or "ragraf-cli"
    kind = (_os.environ.get("RAGRAF_AGENT_KIND") or "").strip()
    if not kind:
        low = agent.lower().replace("-", "").replace("_", "")
        if "autoclaw" in low:
            kind = "autoclaw"
        elif low in ("ragrafcli", "ragrafadmin", "phase0measure"):
            kind = "internal"
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
                            kinds=([str(args.kind)] if getattr(args, "kind", None) else None),
                            with_text=bool(getattr(args, "with_text", False)),
                            text_width=int(getattr(args, "text_width", 160)),
                            answer=answer, mode=resolve_mode(op, args.mode))
    if op == "search_multi":
        # Несколько запросов одним вызовом: разделитель `|`, чтобы не
        # добавлять в CLI формат, зависящий от оболочки (кавычки, запятые).
        raw = getattr(args, "queries", None) or args.text or ""
        queries = [q.strip() for q in str(raw).split("|") if q.strip()]
        return fc.op_search_multi(
            queries, limit=args.limit, scope=args.scope,
            regex=bool(getattr(args, "regex", False)), answer=answer,
        )
    if op == "doc_search":
        return fc.op_doc_search(args.text or "", limit=args.limit,
                                kind=getattr(args, "kind", None),
                                doc=getattr(args, "doc", None),
                                asof=getattr(args, "asof", None),
                                include_superseded=bool(
                                    getattr(args, "include_superseded", False)))
    if op == "doc_asof":
        return fc.op_doc_asof(args.text or "", getattr(args, "date", None) or "",
                              limit=args.limit, kind=getattr(args, "kind", None))
    if op == "dependents":
        return fc.op_dependents(fact=getattr(args, "fact", None) or "",
                                locator=getattr(args, "locator", None) or args.text or "",
                                depth=int(getattr(args, "depth", 3) or 3),
                                include_stated=bool(
                                    getattr(args, "include_stated", True)))
    if op == "stale_cascade":
        return fc.op_stale_cascade(changed=getattr(args, "changed", None)
                                   or args.text or "",
                                   depth=int(getattr(args, "depth", 3) or 3),
                                   apply=bool(getattr(args, "apply",
                                                      False)),
                                   clear=bool(getattr(args, "clear_marks",
                                                      False)),
                                   limit=int(getattr(args, "limit", 50) or 50))
    if op == "doc_get":
        return fc.op_doc_get(getattr(args, "locator", None) or args.text or "",
                             asof=getattr(args, "asof", None))
    if op == "doc_status":
        return fc.op_doc_status(getattr(args, "doc", None))
    if op == "doc_validate":
        return fc.op_doc_validate(getattr(args, "doc", None))
    if op == "doc_ocr":
        return fc.op_doc_ocr(getattr(args, "path", None)
                             or getattr(args, "doc", None) or "")
    if op == "entity_neighbors":
        return fc.op_entity_neighbors(getattr(args, "name", None) or args.text or "",
                                      depth=int(getattr(args, "depth", 1) or 1),
                                      kind=str(getattr(args, "kind", None) or "entity"),
                                      limit=int(args.limit or 50),
                                      asof=getattr(args, "asof", None))
    if op == "doc_relations":
        return fc.op_doc_relations(doc=getattr(args, "doc", None),
                                   entity=getattr(args, "name", None),
                                   limit=int(args.limit or 50),
                                   asof=getattr(args, "asof", None))
    if op == "themes":
        return fc.op_themes(scope=getattr(args, "scope", None),
                            limit=int(args.limit or 20))
    if op == "global_overview":
        return fc.op_global_overview(limit=int(args.limit or 20))
    if op == "doc_search_levels":
        return fc.op_doc_search_levels(args.text or "",
                                      limit=int(args.limit or 20),
                                      mode=resolve_mode(op, args.mode))
    if op == "symbol":
        return fc.op_symbol(args.name or args.text or "", path=args.path,
                            limit=args.limit, answer=answer)
    if op == "references":
        return fc.op_references(args.fqn or args.name or args.text or "", limit=args.limit,
                                min_confidence=getattr(args, "min_confidence", None))
    if op == "callers":
        return fc.op_callers(args.fqn or args.name or args.text or "",
                             depth=int(args.depth or 1),
                             hard_only=args.hard, limit=args.limit, answer=answer,
                             min_confidence=getattr(args, "min_confidence", None))
    if op == "callees":
        return fc.op_callees(args.fqn or args.name or args.text or "",
                             depth=int(args.depth or 1),
                             hard_only=args.hard, limit=args.limit,
                             min_confidence=getattr(args, "min_confidence", None))
    if op == "impact":
        return fc.op_impact(args.target or args.name or args.text or "",
                            mode=resolve_mode(op, args.mode),
                            depth=int(args.depth or 1), limit=args.limit, answer=answer,
                            min_confidence=getattr(args, "min_confidence", None))
    if op == "slice":
        return fc.op_slice(args.fqn or args.name or args.text or "", direction=args.direction,
                           limit=args.limit,
                           min_confidence=getattr(args, "min_confidence", None))
    if op == "edge_evidence":
        return fc.op_edge_evidence(args.fqn or args.name or args.text or "",
                                   kind=args.kind, limit=args.limit)
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
    if op == "excludes":
        # Профиль «шумных» каталогов: show/impact/apply/disable. Действие
        # передаётся --action, список имён — --target, осознанное решение —
        # --force.
        return fc.op_excludes(
            action=getattr(args, "action", "show") or "show",
            dirs=getattr(args, "target", "") or "",
            force=bool(getattr(args, "force", False)),
        )
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
    if op == "route":
        return fc.op_route(args.text or args.name or "", limit=args.limit,
                           verify=bool(getattr(args, "verify", True)))
    if op == "neighbors":
        # Кросс-корпусный поиск: чужие индексы открываются только на чтение.
        return fc.op_neighbors(
            query=args.text or "",
            name=args.name or "",
            limit=args.limit,
            per_corpus=int(getattr(args, "per_corpus", 5) or 5),
            stack=getattr(args, "stack", None),
            min_shared=int(getattr(args, "min_shared", 1) or 1),
            with_text=bool(getattr(args, "nb_text", True)),
            kinds=([str(args.kind)] if getattr(args, "kind", None) else None),
            answer=answer,
        )
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
                                  action=args.action, depth=int(args.depth or 1),
                                  limit=args.limit,
                                  min_confidence=getattr(args, "min_confidence", None))
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
    if op == "contract_drift":
        # Замысел против кода: манифест пишет проект, операция только читает.
        # Нет манифеста — found=false и код 0: это состояние проекта, не сбой.
        # С X3 второй вход — якоря в комментариях (`--source anchors|both`).
        return fc.op_contract_drift(manifest_path=getattr(args, "manifest", None),
                                    source=str(getattr(args, "source", None)
                                               or "manifest"))
    if op == "class_usage":
        return fc.op_class_usage(name=args.name, limit=args.limit,
                                 mode=resolve_mode(op, args.mode))
    # ---------------- внешний доступ (ПРОМТ-И7) ----------------
    if op == "sso_login":
        return fc.op_sso_login(code=getattr(args, "code", "") or "",
                               state=getattr(args, "state", "") or "",
                               provider=getattr(args, "provider", "") or "",
                               redirect_uri=getattr(args, "redirect_uri", "") or "")
    if op == "sso_map_roles":
        raw_groups = getattr(args, "groups", "") or ""
        groups = [item.strip() for item in str(raw_groups).split(",") if item.strip()]
        return fc.op_sso_map_roles(groups=groups)
    if op == "siem_export":
        return fc.op_siem_export(
            source=getattr(args, "source_kind", "all") or "all",
            limit=int(getattr(args, "limit", 0) or 0),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    if op == "siem_test":
        return fc.op_siem_test()
    # ---------------- офлайн-лицензия и аттестация (ПРОМТ-И8) ----------------
    if op == "license_offline":
        return fc.op_license_offline(
            mode=resolve_mode(op, getattr(args, "mode", None)),
            request=getattr(args, "request", "") or "",
            license=getattr(args, "license", "") or "",
            out=getattr(args, "out", "") or "",
            edition_name=getattr(args, "edition_name", "") or "",
            licensee=getattr(args, "licensee", "") or "",
            days=int(getattr(args, "days", 365) or 365),
            note=getattr(args, "note", "") or "",
            public_key=getattr(args, "public_key", "") or "",
        )
    if op == "attest_bundle":
        return fc.op_attest_bundle(
            verify=bool(getattr(args, "attest_verify", False)),
            out=getattr(args, "out", "") or "",
            bundle=getattr(args, "bundle", "") or "",
            tree=getattr(args, "tree", "") or "",
            with_sbom=bool(getattr(args, "with_sbom", False)),
            with_editions=bool(getattr(args, "with_editions", True)),
            gate_report=getattr(args, "gate_report", "") or "",
            sign=bool(getattr(args, "sign", True)),
        )
    # ---------------- контур качества (ПРОМТ-И9) ----------------
    if op == "contract_pin_sample":
        return fc.op_contract_pin_sample(
            mode=str(getattr(args, "sample_mode", "") or "compare"),
            op=str(getattr(args, "op_name", "") or ""),
            limit=int(getattr(args, "limit", 0) or 50),
            by=str(getattr(args, "approved_by", "") or ""),
            note=str(getattr(args, "pin_note", "") or ""),
            only_changed=bool(getattr(args, "only_changed", False)),
            except_ops=str(getattr(args, "except_ops", "") or ""),
        )
    if op == "contract_pin_history":
        return fc.op_contract_pin_history(
            kind=str(getattr(args, "contract_kind", "") or ""),
            op=str(getattr(args, "op_name", "") or ""),
            limit=int(getattr(args, "limit", 0) or 50),
        )
    if op == "contract_pin_status":
        return fc.op_contract_pin_status(limit=int(getattr(args, "limit", 0) or 50))
    if op == "contract_pin_approve":
        return fc.op_contract_pin_approve(
            op=str(getattr(args, "op_name", "") or ""),
            everything=bool(getattr(args, "all_ops", False)),
            by=str(getattr(args, "approved_by", "") or ""),
            note=str(getattr(args, "pin_note", "") or ""),
            kind=str(getattr(args, "contract_kind", "") or "op"),
        )
    if op == "contract_diff":
        return fc.op_contract_diff(
            before=str(getattr(args, "before", "") or ""),
            after=str(getattr(args, "after", "") or ""),
        )
    if op == "preflight":
        # Ничего не создаёт: осмотр каталога до индексации (X5).
        return fc.op_preflight(root=str(getattr(args, "root", "") or ""),
                               sample=int(getattr(args, "sample", 0) or 50))
    if op == "quality_suite":
        return fc.op_quality_suite(
            label=getattr(args, "label", "") or "",
            record=not bool(getattr(args, "no_record", False)),
            session=str(getattr(args, "session", "") or ""),
        )
    if op == "quality_diff":
        return fc.op_quality_diff(
            from_run=int(getattr(args, "from_run", 0) or 0),
            to_run=int(getattr(args, "to_run", 0) or 0),
        )
    if op == "quality_pair":
        # Парный прогон по ФИКСИРОВАННЫМ кейсам (X4). От `quality_diff` отличается
        # предметом: там показатели индекса и отказ от вердикта между ревизиями,
        # здесь те же вопросы дважды и вердикт по кейсам.
        return fc.op_quality_pair(
            from_run=int(getattr(args, "from_run", 0) or 0),
            to_run=int(getattr(args, "to_run", 0) or 0),
        )
    if op == "quality_trace":
        return fc.op_quality_trace(
            action=getattr(args, "trace_action", None) or "list",
            case_id=getattr(args, "case_id", None) or "",
            trace_id=int(getattr(args, "trace_id", 0) or 0),
            session=getattr(args, "session", None) or "",
            label=getattr(args, "label", None) or "")
    if op == "quality_prune":
        # Умолчание — сухой прогон: удаление журнала необратимо, и «нажал
        # enter» не должно его выполнять. Удаляет только вместе с --confirm.
        raw_dry = getattr(args, "dry_run", None)
        raw_keep = getattr(args, "keep", None)
        return fc.op_quality_prune(
            # `None` — «сколько оставить, решает предел журнала»: умолчание
            # выводится из `QUALITY_RUN_SOFT_LIMIT`, а не задано вторым числом.
            keep=None if raw_keep is None else int(raw_keep),
            # None — «флаг не указан»: у прореживания это сухой прогон.
            dry_run=True if raw_dry is None else bool(raw_dry),
            confirm=bool(getattr(args, "confirm", False)),
        )
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
    from .revisions import ControlStore, GraphWriter

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
    control = ControlStore(cfg)
    lex = LexVecStore(cfg)
    embedder = Embedder(cfg, allow_download=False)
    try:
        stats = collect_mod.write(access, rev, collected, graph, lex, embedder,
                                  control=control.conn)
    finally:
        try:
            lex.close()
        except Exception:
            pass
        control.close()
    print(f"записано в индекс под ревизию {rev}: таблиц {stats['graph']['tables']}, "
          f"связей {stats['graph']['relations']}, закрытых колонок "
          f"{stats['graph']['sensitive_columns']}, чанков {stats['lexvec']['chunks']}")
    # Факт отпечатка печатается отдельно: «структура записана» и «отпечаток
    # записан» — разные записи в разные хранилища, и расходились они молча.
    if stats["graph"].get("fingerprint_stored"):
        print(f"отпечаток структуры записан: db_fingerprint:{rev} = "
              f"{collected['fingerprint'][:16]}…")
    else:
        print("ВНИМАНИЕ: отпечаток структуры НЕ записан — сторож свежести "
              "не увидит изменения структуры")
    if not args.json:
        print("подсказка: структура смотрится операциями db_schema, db_table, "
              "db_relations, db_sample, db_drift, db_cross; замысел — "
              "query contract_drift")
    return EXIT_OK


def cmd_usage(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Статистика использования RAGRAF: журнал обращений и оценка эффекта.

    Отвечает на вопрос заказчика «сколько было обращений и какую пользу они дали».
    Все величины по токенам и строкам — ОЦЕНКА (помечается в выводе), числа
    обращений и фактов — измеренные.
    """
    from .facade import Facade

    facade = Facade(cfg, tool="cli", agent=_agent_from_env()[0],
                    agent_kind=_agent_from_env()[1], task=_task_from_env())
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
        last30 = summary.get("last30") or {}
        if last30 and not last30.get("error"):
            print(f"за 30 дней: обращений {last30.get('calls', 0)}, "
                  f"фактов {last30.get('facts', 0)}, "
                  f"строк не читалось ~{last30.get('lines', 0)} (ОЦЕНКА)")

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
                 "registered": "заведена, индекс не строился",
                 "aborted": "индекс есть, последний прогон прерван "
                            "(действует прежняя ревизия)"}.get(state, state)
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


def cmd_locks(args: argparse.Namespace) -> int:
    """Замки индексации и реестра: состояние, уборка брошенных.

    Зачем отдельная команда. Брошенный замок проявляется как «индексация уже
    выполняется другим процессом» у живого, но чужого держателя, и искать его
    глазами в `catalog/locks/` — работа без признака: имя файла — это имя
    корпуса, а не вердикт. Здесь вердикт называется: `alive` (держится живым),
    `silent` (процесс жив, но молчит дольше порога — обычно завис),
    `dead` (процесса нет), `unknown` (файл не читается).

    Уборка без `--force` трогает только `dead` и `unknown`: снять замок
    работающего прогона значит запустить вторую индексацию того же корпуса.
    """
    from . import catalog as catalog_mod

    cat = catalog_mod.Catalog()
    report = cat.lock_report()
    removed: list[dict] = []
    if args.clear:
        if args.force and not (args.reason or "").strip():
            print("[locks] --force требует --reason: принудительное снятие замка "
                  "работающего прогона — это вторжение, а не уборка", file=sys.stderr)
            return EXIT_USAGE
        removed = cat.clear_abandoned_locks(force=bool(args.force),
                                            older_than=args.older_than)
        if removed:
            left = [row for row in report if row["path"] not in {r["path"] for r in removed}]
            report = left

    if args.json:
        _print({"locks": report, "removed": removed}, True)
    else:
        if not report and not removed:
            print("[locks] замков нет: ни индексация, ни запись реестра не идут")
        for row in report:
            idle = row.get("idle_seconds")
            print(f"  {row['kind']:<8} {row['category']:<24} {row['verdict']:<7} "
                  f"pid={row['pid'] or '—'} "
                  f"молчит={'—' if idle is None else f'{idle:.0f} с'}")
            if row["verdict"] != "alive":
                print(f"    {row['reason']}")
        for row in removed:
            mark = "принудительно" if row.get("forced") else "брошенный"
            print(f"  снят ({mark}): {row['file']} — {row['reason']}")
        if args.clear and not removed:
            print("[locks] брошенных замков нет: снимать нечего")
        if any(row["verdict"] in ("silent",) for row in report):
            print("  · живой, но молчащий замок не снимается сам: проверьте процесс "
                  "и при необходимости снимите вручную с причиной (--force --reason)")
    return EXIT_OK


def cmd_server(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Живые MCP-серверы: статус и штатный перезапуск после правки кода.

    Зачем команда. Сервер — долгоживущий процесс, и правка исходников его не
    меняет: ответ несёт `code_stale`, но без способа перезапуска это отказ без
    выхода. Здесь штатный способ — по реестру, который сервер пишет при старте
    (`server_control.register`): `status` называет расхождение сборки с диском,
    `restart` завершает процесс, а клиент (AutoClaw, HARNESS) поднимает сервер
    заново. Сервер не перезапускает себя: его stdout — протокол MCP.
    """
    from . import server_control

    action = getattr(args, "server_action", "status") or "status"
    as_json = bool(getattr(args, "json", False))
    if action == "restart":
        result = server_control.restart(cfg, reason=getattr(args, "reason", "") or "")
        if as_json:
            _print({"op": "server", "action": "restart", **result}, True)
        else:
            killed = result["killed"]
            failed = result["failed"]
            print(f"[server] перезапуск: завершено {len(killed)}, ошибок {len(failed)}")
            for entry in killed:
                print(f"  завершён pid={entry['pid']} "
                      f"build={str(entry.get('build') or '')[:12]} "
                      f"corpus={entry.get('corpus')}")
            for entry in failed:
                print(f"  НЕ завершён pid={entry['pid']}: нет прав? "
                      f"повторите в консоли администратора")
            print(f"  {result['note']}")
        return EXIT_OK

    entries = server_control.scan(cfg)
    if as_json:
        _print({"op": "server", "action": "status", "servers": entries}, True)
        return EXIT_OK
    if not entries:
        print("[server] живых MCP-серверов RAGRAF не зарегистрировано.")
        print("  Если сервер запущен сборкой без реестра — перезапустите клиент "
              "(AutoClaw / HARNESS) один раз; дальше работает ragraf server restart.")
        return EXIT_OK
    stale = 0
    print(f"[server] реестр: {server_control.state_dir(cfg)}")
    for entry in entries:
        state = "жив" if entry["alive"] else "брошен"
        tail = "  ← код на диске новее: нужен перезапуск" if entry.get("stale") else ""
        if entry.get("stale"):
            stale += 1
        print(f"  pid={entry['pid']} {state} "
              f"build={str(entry.get('build') or '')[:12]} "
              f"corpus={entry.get('corpus')}{tail}")
    if stale:
        print("  штатный перезапуск: ragraf server restart  "
              "(клиент поднимет сервер заново на новом коде)")
    return EXIT_OK


def cmd_sources(args: argparse.Namespace) -> int:
    """Разрешённые корни режима «Я это ты»: список, добавление, права, секреты."""
    from .sources import SourceRegistry, SourcesError

    action = getattr(args, "action", "list")
    reg = SourceRegistry()
    try:
        if action == "add":
            root = getattr(args, "root", None) or getattr(args, "ident", None)
            if not root:
                print("[sources] не указан каталог: sources add <путь>", file=sys.stderr)
                return EXIT_USAGE
            src = reg.add(
                root,
                kind=getattr(args, "kind", None),
                priority=int(getattr(args, "priority", 0) or 0),
                note=getattr(args, "note", "") or "",
                network=bool(getattr(args, "network", False)),
            )
            if args.json:
                _print(src.to_dict(), True)
            else:
                print(f"[sources] добавлен источник id={src.id} ({src.kind})")
                print(f"  корень:    {src.root}")
                print(f"  приоритет: {src.priority}")
                print("  содержимое не читается: карта хранит только метаданные")
            return EXIT_OK
        if action in ("remove", "enable", "disable"):
            target = getattr(args, "ident", None)
            if action == "remove":
                src = reg.remove(target)
                # Строки карты удаляются вместе с доступом: держать метаданные
                # каталога, разрешение на который отозвано, нельзя.
                try:
                    from .global_map import GlobalMap
                    from .sources import CATALOG_DIR

                    if (CATALOG_DIR / "global_map.sqlite").exists():
                        gm = GlobalMap()
                        try:
                            gm.sync_sources(reg)
                        finally:
                            gm.close()
                except Exception:  # noqa: BLE001 — удаление доступа важнее чистки карты
                    pass
                if args.json:
                    _print(src.to_dict(), True)
                else:
                    print(f"[sources] удалён источник id={src.id}: {src.root}")
                    print("  файлы на диске не тронуты")
                return EXIT_OK
            src = reg.set_enabled(target, action == "enable")
            if args.json:
                _print(src.to_dict(), True)
            else:
                state = "включён" if src.enabled else "выключен"
                print(f"[sources] источник id={src.id} {state}: {src.root}")
            return EXIT_OK
        if action == "priority":
            src = reg.set_priority(getattr(args, "ident", None),
                                   int(getattr(args, "value", 0) or 0))
            if args.json:
                _print(src.to_dict(), True)
            else:
                print(f"[sources] приоритет id={src.id} = {src.priority}: {src.root}")
            return EXIT_OK
        if action == "secrets":
            from .global_map import GlobalMap
            from .sources import CATALOG_DIR

            if not (CATALOG_DIR / "global_map.sqlite").exists():
                print("карта ещё не построена: сначала выполните `map scan`")
                return EXIT_OK
            gm = GlobalMap()
            try:
                rows = gm.secret_places(getattr(args, "source", None))
            finally:
                gm.close()
            if args.json:
                _print({"count": len(rows), "places": rows}, True)
                return EXIT_OK
            print(f"[secrets] мест, исключённых по правилам: {len(rows)}")
            print("  значения не читаются и не хранятся: только путь, правило, размер")
            for row in rows:
                print(f"  {row['rel_path']}  ({row['rule']}, {row['size']} б)")
            return EXIT_OK
        # list
        rows = reg.sources()
        counts: dict[int, dict] = {}
        try:
            from .global_map import GlobalMap
            from .sources import CATALOG_DIR

            if (CATALOG_DIR / "global_map.sqlite").exists():
                gm = GlobalMap()
                try:
                    gm.sync_sources(reg)
                    counts = {int(r["id"]): r for r in gm.status()["sources"]}
                finally:
                    gm.close()
        except Exception:  # noqa: BLE001 — список не должен падать из-за карты
            counts = {}
        payload = []
        for src in rows:
            row = counts.get(src.id) or {}
            item = src.to_dict()
            item["files"] = row.get("files")
            item["secrets"] = row.get("secrets")
            item["last_scan_at"] = row.get("last_scan_at")
            item["scan_status"] = row.get("scan_status")
            payload.append(item)
        if args.json:
            _print({"count": len(rows), "sources": payload}, True)
            return EXIT_OK
        print(f"[sources] разрешённых корней: {len(rows)}")
        if not rows:
            print("  (пусто; добавьте корень командой: sources add <каталог>)")
        for item in payload:
            mark = "вкл" if item["enabled"] else "выкл"
            counts_text = (f"файлов={item['files']} секретов={item['secrets']}"
                           if item.get("files") is not None else "карта не построена")
            print(f"  id={item['id']:<3} {mark:>3}  {item['kind']:<7} приоритет={item['priority']:<4} {item['root']}")
            print(f"        {counts_text}")
            if item.get("note"):
                print(f"        {item['note']}")
        return EXIT_OK
    except SourcesError as exc:
        print(f"[sources] {exc}", file=sys.stderr)
        return EXIT_USAGE


def cmd_map(args: argparse.Namespace) -> int:
    """Карта метаданных: скан разрешённых корней и состояние хранилища."""
    from .global_map import GlobalMap
    from .sources import SourceRegistry, SourcesError

    action = getattr(args, "action", "status")
    reg = SourceRegistry()
    gm = GlobalMap()
    try:
        if action == "scan":
            gm.sync_sources(reg)
            wanted: list = []
            ident = getattr(args, "source", None)
            if ident is not None:
                src = reg.get(ident)
                if src is None:
                    print(f"[map] источник не найден: {ident}", file=sys.stderr)
                    return EXIT_USAGE
                wanted = [src]
            else:
                wanted = reg.sources(enabled_only=True)
            if not wanted:
                print("[map] нет включённых источников: добавьте командой sources add")
                return EXIT_OK
            limits = {}
            if getattr(args, "max_files", None):
                limits["max_files"] = int(args.max_files)
            if getattr(args, "max_seconds", None):
                limits["max_seconds"] = float(args.max_seconds)
            results = [gm.scan(src, limits=limits or None) for src in wanted]
            if args.json:
                _print({"scans": [r.to_dict() for r in results]}, True)
                return EXIT_OK
            for res in results:
                print(f"[map] {res.root}")
                print(f"  статус:   {res.status}")
                print(f"  файлов:   {res.files}  секретных мест: {res.secrets}  пропущено: {res.skipped}")
                print(f"  отпечаток: {res.fingerprint[:16] if res.fingerprint else '(нет)'}")
                if res.reason:
                    print(f"  причина:  {res.reason}")
                for err in res.errors[:5]:
                    print(f"  ошибка:   {err}")
            return EXIT_OK
        if action == "find":
            tokens = [t for t in str(getattr(args, "text", "") or "").split() if t]
            rows = gm.find(tokens, limit=int(getattr(args, "limit", 50) or 50))
            if args.json:
                _print({"query": tokens, "count": len(rows), "files": rows}, True)
                return EXIT_OK
            print(f"[map] найдено файлов по имени/пути: {len(rows)}")
            for row in rows:
                print(f"  {row['root']}/{row['rel_path']}  ({row['size']} б)")
            return EXIT_OK
        # status
        gm.sync_sources(reg)
        data = gm.status()
        if args.json:
            _print(data, True)
            return EXIT_OK
        print(f"[map] хранилище: {data['storage']}")
        print(f"  источников: {data['count']}, включено: {data['enabled']}, файлов: {data['files']}, секретных мест: {data['secrets']}")
        for row in data["sources"]:
            mark = "вкл" if row["enabled"] else "выкл"
            print(f"  id={row['id']:<3} {mark:>3}  файлов={row['files']:<7} секретов={row['secrets']:<4} {row['root']}")
            if row.get("last_scan_at"):
                print(f"        скан: {row['last_scan_at']}  статус: {row.get('scan_status') or '—'}")
        return EXIT_OK
    except SourcesError as exc:
        print(f"[map] {exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        gm.close()


def cmd_route(args: argparse.Namespace) -> int:
    """Маршрут без конфига корпуса: где лежит и что отвечает.

    Отдельная команда нужна потому, что вопрос «какой индекс использовать»
    задаётся ДО выбора корпуса. `query route` остаётся для тех, у кого корпус
    уже выбран: там ответ идёт в общем конверте с ревизией и свежестью.
    """
    from .route import route

    data = route(
        getattr(args, "text", "") or "",
        limit=int(getattr(args, "limit", 10) or 10),
        verify=bool(getattr(args, "verify", True)),
    )
    if args.json:
        _print(data, True)
        return EXIT_OK
    print(f"[route] запрос: {data['query']!r}")
    print(f"  слов: {', '.join(data['tokens']) or '(нет)'}")
    print(f"  совпадений в карте: {data['map_hits']}, разработок в реестре: {data['corpora']}")
    if not data["candidates"]:
        print("  кандидатов нет")
    for cand in data["candidates"]:
        kind = "корпус" if cand["type"] == "corpus" else "источник"
        name = cand.get("category") or cand.get("root")
        print(f"\n  [{kind}] {name}  score={cand['score']:.2f}")
        if cand.get("root"):
            print(f"    корень: {cand['root']}")
        if cand.get("file_hits"):
            print(f"    файлов по запросу: {cand['file_hits']}")
        local = cand.get("local_index")
        if local:
            age = local.get("age_seconds")
            age_text = f"{(age or 0) / 3600:.1f} ч назад" if age is not None else "время неизвестно"
            print(f"    индекс: {local.get('rev_id')}  режим: {local.get('capability') or '—'}  обновлён: {age_text}")
            if local.get("fingerprint_match") is True:
                print("    отпечаток: совпадает (факты актуальны)")
            elif local.get("fingerprint_match") is False:
                print("    отпечаток: РАЗОШЁЛСЯ (факты могут быть устаревшими)")
        else:
            print("    локального индекса нет")
        for reason in cand.get("why") or []:
            print(f"    причина: {reason}")
    recommended = data.get("recommended")
    if recommended:
        print(f"\n  рекомендовано: {recommended.get('category')}  ({recommended.get('root')})")
        print("  дальше: ensure этого каталога, затем search/symbol по нему")
    for note in data.get("notes") or []:
        print(f"  примечание: {note}")
    return EXIT_OK


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
        with_vectors=not getattr(args, "no_vectors", False),
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

def cmd_feedback(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Локальный черновик отчёта о пользе. Сеть не используется."""
    from . import feedback

    data = feedback.build(cfg, days=int(getattr(args, "days", 30) or 30))
    if args.json:
        _print(data, True)
        return EXIT_OK
    print(f"[feedback] черновик сохранён: {data['path']}")
    print("  ничего не отправлено: fork, ветку и PR создаёт владелец\n")
    print(data["report"])
    return EXIT_OK


def cmd_analytics(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Экспорт и удаление локальной аналитики. Сеть не используется."""
    from . import analytics

    action = str(getattr(args, "action", "export") or "export")
    if action == "delete":
        if not getattr(args, "yes", False):
            print("удаление журнала требует подтверждения: добавьте --yes\n"
                  "перед удалением создаётся резервная копия в catalog/analytics/",
                  file=sys.stderr)
            return EXIT_USAGE
        data = analytics.delete(cfg)
        if args.json:
            _print(data, True)
        else:
            print(f"[analytics] удалено вызовов: {data['deleted_calls']}, "
                  f"сессий: {data['deleted_sessions']}")
            print(f"  резервная копия: {data['backup']}")
        return EXIT_OK
    data = analytics.export(cfg, days=int(getattr(args, "days", 30) or 30),
                            out=getattr(args, "out", None))
    if args.json:
        _print(data, True)
    else:
        print(f"[analytics] экспорт сохранён: {data['path']}")
        print("  ничего не отправлено: файл локальный")
    return EXIT_OK


def cmd_status(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    from .facade import Facade

    # Идентичность обязательна: без неё каждая проверка состояния добавляла в
    # журнал запись `tool=unknown, agent=unknown` (измерено: 42 за три часа).
    fc = Facade(cfg, tool="cli", agent=_agent_from_env()[0],
                agent_kind=_agent_from_env()[1], task=_task_from_env())
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


def cmd_compact(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Политика хранения `.ragraf`: план (по умолчанию) или выполнение очистки.

    Зачем команда, а не только настройка. Политика «что удаляется вместе с
    ревизией» до этого жила в коде ротации и не имела ни отчёта, ни способа
    посмотреть числа до удаления. Замер 30.10.2026: `.ragraf` занимал 26 829 МБ,
    из них 17 831 МБ — артефакты CPG ревизий, данных которых в хранилищах уже
    нет; увидеть это можно было только обходом диска.

    Порядок, который команда НЕ даёт нарушить: `--apply` без `--yes` — отказ.
    Отказ печатает, что нужно раньше: план, бэкап и согласие владельца.
    """
    from .facade import Facade

    apply = bool(getattr(args, "apply", False))
    if apply and not bool(getattr(args, "yes", False)):
        print("очистка меняет диск необратимо и требует подтверждения:\n"
              "  1) `ragraf compact --plan` — числа до;\n"
              "  2) копия .ragraf вне дерева (бэкап);\n"
              "  3) `ragraf compact --apply --yes`.", file=sys.stderr)
        return EXIT_USAGE
    vacuum: bool | None = None
    if bool(getattr(args, "vacuum", False)):
        vacuum = True
    if bool(getattr(args, "no_vacuum", False)):
        vacuum = False
    fc = Facade(cfg, tool="cli", agent=_agent_from_env()[0],
                agent_kind=_agent_from_env()[1], task=_task_from_env())
    try:
        data = fc.op_compact(apply=apply, vacuum=vacuum,
                             retain=getattr(args, "retain", None))
    finally:
        fc.close()
    if args.json:
        _print(data, True)
        return EXIT_OK if data.get("found") else EXIT_ERROR
    _print_compact(data, apply)
    return EXIT_OK if data.get("found") else EXIT_ERROR


def _print_compact(answer: dict, applied: bool) -> None:
    """Печать ответа `compact`: числа до/после и что именно удалено."""
    if answer.get("error"):
        print(f"[compact] ОТКАЗ: {answer['error']}", file=sys.stderr)
        return
    result = answer.get("result") or {}
    if applied:
        before = result.get("before") or {}
        after = result.get("after") or {}
        print(f"[compact] очистка выполнена за {result.get('seconds')} с")
        for label in ("graph", "lexvec", "control", "cpg"):
            one = before.get(label) or {}
            two = after.get(label) or {}
            freed = (result.get("freed_mb") or {}).get(label)
            print(f"  {label:8s} {one.get('mb')} МБ → {two.get('mb')} МБ "
                  f"(освобождено {freed} МБ)")
        print(f"  всего освобождено: {result.get('freed_total_mb')} МБ")
        active = result.get("active") or {}
        print(f"  активная ревизия: {active.get('rev')} — числа до {active.get('before')}, "
              f"после {active.get('after')}")
        rows = result.get("removed_rows") or {}
        for label, counters in rows.items():
            total = sum(int(v) for v in counters.values())
            print(f"  строк удалено из {label}: {total} ({len(counters)} таблиц)")
        cpg = result.get("cpg_removed") or {}
        print(f"  артефактов CPG удалено: файлов {cpg.get('bins')}, каталогов Joern "
              f"{cpg.get('workspaces')}, выгрузок {cpg.get('exports')}")
        temp = result.get("temp_removed") or {}
        print(f"  временных файлов удалено: {temp.get('files')}")
        for label, item in (result.get("vacuum") or {}).items():
            if item.get("ran"):
                print(f"  VACUUM {label}: {item.get('before_mb')} → {item.get('after_mb')} МБ "
                      f"за {item.get('seconds')} с")
            else:
                print(f"  VACUUM {label}: не выполнялся — {item.get('reason')}")
        left = result.get("orphans_left") or []
        print(f"  хвоста ротации осталось: {len(left)} ревизий"
              + (f" ({', '.join(left[:5])})" if left else ""))
        return

    policy = result.get("policy") or {}
    print(f"[compact] план, ничего не изменено — {result.get('project')} "
          f"({result.get('root')})")
    print(f"  политика: хранить ревизий {policy.get('retain_revs')}, артефактов CPG "
          f"{policy.get('storage_keep_cpg_revs')} ревизий, предел tmp "
          f"{policy.get('storage_tmp_max_mb')} МБ, возраст {policy.get('storage_tmp_age_hours')} ч, "
          f"порог VACUUM {policy.get('storage_vacuum_free_percent')} %")
    print(f"  ревизий в хранилищах: {result.get('revisions_in_stores')}; "
          f"хранятся: {', '.join(result.get('revisions_kept') or [])}")
    for label, entry in (result.get("stores") or {}).items():
        print(f"  {label:8s} {entry.get('mb')} МБ "
              f"(свободных страниц {entry.get('free_mb')} МБ)")
    cpg = result.get("cpg") or {}
    print(f"  cpg      {cpg.get('mb')} МБ: файлов CPG {cpg.get('bins')}, каталогов Joern "
          f"{cpg.get('workspaces')}, выгрузок {cpg.get('exports')}, tmp "
          f"{round(int(cpg.get('tmp_bytes') or 0) / 1_048_576, 1)} МБ")
    orphans = result.get("orphans") or {}
    print(f"  хвост ротации: {orphans.get('revisions')} ревизий, "
          f"{orphans.get('rows_total')} строк — {orphans.get('rows')}")
    temp = result.get("temp") or {}
    print(f"  временные выгрузки старше {temp.get('age_hours')} ч: {temp.get('files')} файлов; "
          f"временные каталоги сверх предела: "
          f"{round(int(temp.get('overflow_bytes') or 0) / 1_048_576, 1)} МБ")
    print(f"  артефактов CPG к удалению: {cpg.get('remove_revs')} ревизий, "
          f"{cpg.get('remove_mb')} МБ")
    for label, needed in (result.get("vacuum") or {}).items():
        reason = (result.get("vacuum_reason") or {}).get(label, "")
        print(f"  VACUUM {label}: {'да' if needed else 'нет'} — {reason}")
    print(f"  предполагается освободить: "
          f"{round(int(result.get('delete_bytes') or 0) / 1_048_576, 1)} МБ "
          f"(строки вернёт только VACUUM)")
    print("  выполнение: `ragraf compact --apply --yes` после бэкапа")


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



def cmd_secrets(args: argparse.Namespace) -> int:
    """Хранилище секретов: значения, права агентов, аудит.

    Значение НИКОГДА не берётся из аргумента командной строки: аргумент виден
    в истории оболочки и в списке процессов. Источники — stdin (по умолчанию),
    переменная окружения или файл.
    """
    import json as _json

    from . import vault as vault_mod
    from . import vault_ops

    action = str(getattr(args, "action", None) or "list")
    actor = vault_ops.cli_actor()
    box = vault_mod.Vault()

    def emit(data) -> int:
        print(_json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0

    def agents_list() -> list[str]:
        raw = str(getattr(args, "agents", "") or "")
        return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]

    try:
        if action == "init":
            return emit(vault_mod.init_master_key(force=bool(args.force)))
        if action == "policy":
            return emit(box.policy())
        if action == "list":
            data = box.list(actor=actor, query=str(args.query or ""),
                            kind=str(args.kind or ""), tag=str(args.tag or ""),
                            limit=int(args.limit), include_hidden=bool(actor.admin))
            if args.json:
                return emit(data)
            print(f"секретов: {data['count']}"
                  f" (скрыто от вас: {data['hidden']})")
            for item in data["items"]:
                flags = []
                if item["expired"]:
                    flags.append("просрочен")
                elif item["expiring_soon"]:
                    flags.append(f"истекает через {item['days_left']} дн")
                mode = item["visibility"]
                if mode == "listed":
                    mode = "только " + ", ".join(item["agents"])
                elif mode == "owner_only":
                    mode = "только автор"
                print(f"  {item['name']:<34} {item['kind']:<18} "
                      f"{item['masked']:<6} {mode:<22} "
                      f"добавил: {item['created_by'] or '—'} "
                      f"{item['created_at']}")
                if flags:
                    print(f"      ! {', '.join(flags)}")
            return 0
        if action == "show":
            data = box.show(str(args.name or ""), actor=actor)
            return emit(data)
        if action == "reveal":
            data = box.reveal(str(args.name or ""), actor=actor,
                              reason=str(args.reason or ""))
            if args.json:
                return emit(data)
            # Значение — единственное, что идёт в stdout без обёртки: команду
            # используют в конвейере, и обёртка там была бы мусором.
            print(data["value"])
            print(f"получено: {data['name']} · причина: {data['reason'] or '—'}",
                  file=sys.stderr)
            return 0
        if action == "set":
            value = _secret_value_from(args)
            meta = box.set(
                str(args.name or ""), value, actor=actor,
                kind=str(args.kind or "password"),
                service=str(args.service or ""), label=str(args.label or ""),
                owner=str(args.owner or ""), note=str(args.note or ""),
                tags=[t for t in str(args.tags or "").split(",") if t.strip()],
                project=str(args.project or ""), visibility=args.visibility,
                agents=agents_list(), expires_at=str(args.expires or ""),
                captured=bool(args.captured),
            )
            return emit(meta)
        if action == "rm":
            return emit(box.delete(str(args.name or ""), actor=actor))
        if action == "grant":
            return emit(box.grant(str(args.name or ""), agents_list(),
                                  mode=args.visibility, actor=actor))
        if action == "revoke":
            return emit(box.revoke(str(args.name or ""), agents_list(),
                                   actor=actor))
        if action == "check":
            report = box.check()
            print(_json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report.get("ok") else EXIT_ERROR
        if action == "rotate":
            return emit(box.rotate_key(actor=actor))
        if action == "export":
            phrase = _phrase_from(args)
            return emit(box.export(str(args.out or "secrets.export.json"),
                                   phrase, actor=actor))
        if action == "import":
            phrase = _phrase_from(args)
            return emit(box.import_(str(args.out or ""), phrase, actor=actor))
        if action == "audit":
            # Проверка целостности и подписанная выгрузка — то, что спрашивает
            # внешний аудитор. Обе команды работают без конфигурации корпуса.
            if getattr(args, "verify", False):
                report = box.verify_audit()
                print(_json.dumps(report, ensure_ascii=False, indent=2))
                if not report.get("ok"):
                    for item in report.get("broken", []):
                        print(f"  · запись {item.get('id')}: {item.get('problem')}",
                              file=sys.stderr)
                return 0 if report.get("ok") else EXIT_ERROR
            if getattr(args, "export", None):
                result = box.export_audit(
                    str(args.export), actor=actor,
                    sign=not bool(getattr(args, "no_sign", False)))
                print(_json.dumps(result, ensure_ascii=False, indent=2))
                if result.get("signature_error"):
                    print(f"подпись не поставлена: {result['signature_error']}",
                          file=sys.stderr)
                    return EXIT_ERROR
                return 0 if result.get("ok") else EXIT_ERROR
            return emit(box.audit_rows(limit=int(args.limit),
                                       name=str(args.name or "")))
        print(f"неизвестное действие: {action}", file=sys.stderr)
        return EXIT_USAGE
    except vault_mod.VaultError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return EXIT_ERROR


def _secret_value_from(args: argparse.Namespace) -> str:
    """Значение для `secrets set`: env, файл или stdin. Не аргумент."""
    import os as _os
    from pathlib import Path as _Path

    var = getattr(args, "from_env", None)
    if var:
        value = _os.environ.get(str(var))
        if value is None:
            raise RuntimeError(f"переменная окружения {var} не задана")
        return value
    path = getattr(args, "from_file", None)
    if path:
        return _Path(str(path)).read_text(encoding="utf-8").rstrip("\r\n")
    return sys.stdin.read().rstrip("\r\n")


def _phrase_from(args: argparse.Namespace) -> str:
    """Парольная фраза снимка — тоже только из окружения или stdin."""
    import os as _os

    var = getattr(args, "passphrase_env", None)
    if var:
        value = _os.environ.get(str(var))
        if value:
            return value
        raise RuntimeError(f"переменная окружения {var} не задана")
    return sys.stdin.readline().rstrip("\r\n")

def cmd_edition(args: argparse.Namespace) -> int:
    """Показать действующее издание и что ему разрешено.

    Зачем команда. Слой изданий отвечает на вопрос, который задаётся ДО работы —
    «что этой сборке разрешено», — и по конверту ответа это видно только
    косвенно. Здесь показывается ПОЛНОЕ состояние: заявленное издание, чем оно
    ограничено, состояние лицензии по подписи, действующие возможности и то, что
    объявлено, но в сборке не написано.

    Ответ собирается тем же кодом, что и поле `edition` в конверте
    (`src/ragraf/edition.py`), поэтому показанное здесь не может разойтись с
    ответом агенту.

    Код возврата: 0 — слой разрешён; 1 — не разрешён (причина названа);
    2 — недопустимые аргументы.
    """
    from . import edition as edition_mod

    declared = (args.declared or "").strip() or None
    state = edition_mod.resolve(
        declared=declared,
        license_file=args.license or None,
        baseline_file=args.baseline or None,
        public_key=getattr(args, "public_key", None),
    )
    payload = state.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK if payload["resolved"] else EXIT_ERROR

    print(f"издание:       {payload['edition']}")
    print(f"заявлено:      {payload['declared']}")
    print(f"разрешено:     {'да' if payload['resolved'] else 'НЕТ — причина ниже'}")
    print(f"возможностей:  {len(payload['features'])}")
    for name in payload["features"]:
        print(f"  · {name}")
    lic = payload["license"]
    print(f"лицензия:      {lic['state']}")
    print(f"  причина:     {lic['reason']}")
    if lic.get("licensee"):
        print(f"  получатель:  {lic['licensee']}")
    if lic.get("expires_at"):
        print(f"  срок:        {lic['expires_at']}")
    if payload["missing"]:
        print(f"объявлено, но недоступно: {len(payload['missing'])}")
        for item in payload["missing"]:
            absent = item.get("absent_operations") or []
            tail = f"; нет операций: {', '.join(absent)}" if absent else ""
            print(f"  · {item['name']} ({item['edition']}): {item.get('reason', '')}{tail}")
    for note in payload["notes"]:
        print(f"примечание:    {note}")
    print(f"базовая линия: {payload['baseline']}")
    return EXIT_OK if payload["resolved"] else EXIT_ERROR


def _print_edges(res: dict, with_evidence: bool, limits: list | None = None) -> None:
    """Текстовый вывод рёбер символа: направления, доказательства, координаты.

    Вынесено отдельной функцией, потому что этим отвечают две команды:
    `query edge_evidence` и `edges --fqn … --evidence`. Два формата одного
    ответа разошлись бы так же, как когда-то разошлись два списка операций.
    """
    print(f"  цель {res.get('target')}  {res.get('path')}:{res.get('start_line')}")
    count = res.get("count") or {}
    ev = res.get("evidence") or {}
    print(f"  рёбер: исходящих {count.get('out', 0)}, входящих {count.get('in', 0)}; "
          f"подтверждено CPG {ev.get('confirmed', 0)}, выведено {ev.get('derived', 0)} "
          f"из {ev.get('total', 0)} ({(ev.get('derived_share') or 0) * 100:.1f} %)")
    for title, key, arrow in (("исходящие", "out", "->"), ("входящие", "in", "<-")):
        items = res.get(key) or []
        if not items:
            continue
        print(f"  {title}:")
        for it in items[:20]:
            tail = ""
            if with_evidence:
                how = f"/{it['evidence_how']}" if it.get("evidence_how") else ""
                tail = (f"  {it.get('evidence_kind')}{how}  {it.get('path')}:{it.get('line')}"
                        + ("" if it.get("evidence_ok") else "  [координата не сверена]"))
            print(f"    {arrow} {it.get('other')}  ({it.get('edge_kind')})"
                  f"  conf={it.get('confidence')}{tail}")
        if len(items) > 20:
            print(f"    … ещё {len(items) - 20} (полный список — с --json)")
    for line in (limits or []):
        print(f"  · {line}")


def cmd_edges(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Рёбра символа: `ragraf edges --fqn <имя> [--evidence] [--kind …]`.

    Зачем отдельная команда, если есть `query edge_evidence`. Вопрос «что за
    связи у этой функции и чем они подтверждены» задаётся чаще остальных, а
    `query` требует помнить имя операции. Команда — тонкая обёртка: вся работа
    в `Facade.op_edge_evidence`, чтобы ответ не разошёлся с MCP-инструментом.
    """
    from .facade import Facade

    fqn = args.fqn or args.name or ""
    if not fqn:
        print("нужен символ: --fqn <имя>", file=sys.stderr)
        return EXIT_USAGE
    fc = Facade(cfg, tool="cli", agent=_agent_from_env()[0],
                agent_kind=_agent_from_env()[1], task=_task_from_env())
    try:
        data = fc.op_edge_evidence(fqn, kind=args.kind, limit=args.limit)
    finally:
        fc.close()
    if args.json:
        _print(data, True)
        return _exit_code("edge_evidence", data)
    if not data.get("found"):
        _print(data, False)
        return _exit_code("edge_evidence", data)
    _print_edges(data.get("result") or {}, bool(args.evidence), data.get("limits"))
    return EXIT_OK


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
    # Документный граф: проход по документам через LLM. Флаг выключен по
    # умолчанию, потому что это ПЛАТНЫЕ обращения и первая стадия, которая
    # ходит в сеть. Без флага поведение индексации не меняется ни на байт.
    pi.add_argument("--doc-graph", dest="doc_graph", action="store_true",
                    help="собрать документный граф (извлечение фактов через LLM)")
    pi.set_defaults(func=cmd_index)

    pr = sub.add_parser("reindex", help="переиндексация + архивация журнала расхождений")
    pr.add_argument("--no-vectors", action="store_true")
    pr.add_argument("--limit-files", type=int, default=None)
    pr.add_argument("--doc-graph", dest="doc_graph", action="store_true",
                    help="собрать документный граф (извлечение фактов через LLM)")
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
    pq.add_argument("--doc", default=None,
                    help="doc_status/doc_validate: документ (путь или doc_id)")
    pq.add_argument("--locator", default=None,
                    help="doc_get: адрес чанка документа (path:12, path#anchor, …)")
    pq.add_argument("--table")
    pq.add_argument("--manifest", default=None,
                    help="contract_drift: путь к YAML-манифесту замысла "
                         "(по умолчанию contracts.yaml в корне корпуса)")
    pq.add_argument("--declared-source", dest="source", default=None,
                    choices=["manifest", "anchors", "both"],
                    help="contract_drift: откуда берётся замысел — манифест, "
                         "якоря в комментариях или оба (манифест главнее)")
    pq.add_argument("--scope")
    pq.add_argument("--depth", type=int, default=None)
    # Допустимые значения --mode зависят от операции (MODE_CHOICES). Общий
    # фиксированный список не годился: `table_usage --mode write` и
    # `class_usage --mode missing` из инструкции проекта отклонялись
    # argparse ещё до запуска операции.
    pq.add_argument("--mode", default=None, choices=MODE_ALL)
    pq.add_argument("--action", default=None,
                    help="policy_trace: имя действия, решение о котором нужно проследить")
    pq.add_argument("--direction", default="back",
                    choices=list(OP_CHOICES[("slice", "direction")]))
    pq.add_argument("--style", default="compact",
                    choices=list(OP_CHOICES[("code", "style")]))
    pq.add_argument("--detail", default="normal",
                    choices=list(OP_CHOICES[("repo_map", "detail")]))
    pq.add_argument("--hard", action="store_true", help="только доказанные связи")
    # Этап 10: порог достоверности — решение, а не тайна. Зашитое значение
    # остаётся по умолчанию, но ответ называет и значение, и правило отсечения.
    pq.add_argument("--min-confidence", dest="min_confidence", type=float, default=None,
                    help="порог достоверности связей 0..1 (по умолчанию 0.5; "
                         "доказанные связи не отсекаются)")
    pq.add_argument("--budget", type=int, default=2000, help="бюджет токенов для repo_map")
    pq.add_argument("--no-verify", dest="verify", action="store_false", default=True,
                    help="route: не сверять отпечаток рекомендованного корпуса")
    pq.add_argument("--limit", type=int, default=50)
    pq.add_argument("--since", type=int, default=None)
    pq.add_argument("--since-rev", default=None,
                    help="diff: ревизия, с которой сравнивать (по умолчанию предыдущая)")
    pq.add_argument("--date", default=None,
                    help="doc_asof: дата действия (ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)")
    pq.add_argument("--asof", default=None,
                    help="doc_search/doc_get/entity_neighbors/doc_relations: "
                         "редакция на дату (ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)")
    pq.add_argument("--fact", default=None, help="dependents: идентификатор факта")
    pq.add_argument("--changed", default=None,
                    help="stale_cascade: что изменилось — факты или адреса "
                         "через запятую")
    pq.add_argument("--apply", action="store_true",
                    help="stale_cascade: поставить пометки "
                         "(по умолчанию только отчёт)")
    pq.add_argument("--clear-marks", dest="clear_marks", action="store_true",
                    help="stale_cascade: закрыть пометки после пересчёта")
    pq.add_argument("--include-superseded", dest="include_superseded",
                    action="store_true",
                    help="doc_search: показать заменённые редакции")
    pq.add_argument("--no-stated", dest="include_stated", action="store_false",
                    default=True,
                    help="dependents: только выводы (derived), без утверждений человека")
    pq.add_argument("--tags", default="", help="note: метки через запятую")
    pq.add_argument("--task", default=None, help="note/notes: метка задачи")
    # P2-2: готовый ответ вместо структурного результата. Оболочка (op, ревизия,
    # capability, found) остаётся — это контракт, по которому клиент понимает,
    # на какую ревизию опирается ответ.
    pq.add_argument("--answer", action="store_true",
                    help="вернуть сформулированный ответ со ссылками (P2-2)")
    pq.add_argument("--entry-ids", default=None)
    # Внешний доступ (ПРОМТ-И7). Значений доступа среди аргументов нет
    # намеренно: код авторизации — одноразовое утверждение провайдера, а
    # значение доступа к приёмнику берётся из переменной окружения.
    pq.add_argument("--code", default="", help="sso_login: код авторизации")
    pq.add_argument("--state", default="", help="sso_login: state начатой попытки входа")
    pq.add_argument("--provider", default="", help="sso_login: вид провайдера")
    pq.add_argument("--redirect-uri", dest="redirect_uri", default="",
                    help="sso_login: адрес возврата")
    pq.add_argument("--groups", default="", help="sso_map_roles: группы через запятую")
    pq.add_argument("--source-kind", dest="source_kind", default="all",
                    help="siem_export: access | secrets | all")
    # `--dry-run` ОДИН на всю команду: у `siem_export` он означает «собрать, но
    # не отправлять», у `quality_prune` — «только перечень». Умолчание `None`
    # разводит смыслы: у отправки None читается как «нет», у прореживания —
    # как «да, сухой прогон». Второй аргумент с тем же именем ронял сборку
    # разбора аргументов целиком (argparse: conflicting option string).
    pq.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                    help="siem_export: собрать набор, но не отправлять; "
                         "quality_prune: только перечень (по умолчанию)")
    # Офлайн-лицензия и аттестация (ПРОМТ-И8). Значения доступа среди аргументов
    # нет и здесь: приёмка проверяет подпись и отпечаток установки, а ключ подписи
    # берётся из каталога состояния или из переменной окружения.
    pq.add_argument("--request", default="", help="license_offline: файл заявки")
    pq.add_argument("--out", default="", help="license_offline/attest_bundle: приёмник")
    pq.add_argument("--edition", dest="edition_name", default="",
                    help="license_offline: запрашиваемое издание")
    pq.add_argument("--licensee", default="", help="license_offline: лицензиат")
    pq.add_argument("--days", type=int, default=365, help="license_offline: срок в днях")
    pq.add_argument("--public-key", dest="public_key", default="",
                    help="license_offline: доверенный открытый ключ")
    pq.add_argument("--attest-verify", dest="attest_verify", action="store_true",
                    help="attest_bundle: проверить готовый набор, а не собрать")
    pq.add_argument("--bundle", default="", help="attest_bundle: каталог набора")
    pq.add_argument("--tree", default="", help="attest_bundle: дерево поставки")
    pq.add_argument("--with-sbom", dest="with_sbom", action="store_true",
                    help="attest_bundle: включить SBOM")
    pq.add_argument("--no-editions", dest="with_editions", action="store_false",
                    default=True, help="attest_bundle: не включать карту изданий")
    pq.add_argument("--gate-report", dest="gate_report", default="",
                    help="attest_bundle: отчёт гейта порч")
    pq.add_argument("--no-sign", dest="sign", action="store_false", default=True,
                    help="attest_bundle: не подписывать манифест")
    # Контур качества (ПРОМТ-И9). `--no-record` нужен затем, чтобы прогон
    # можно было повторить, не засоряя журнал: сравнение идёт по `digest`.
    pq.add_argument("--op-name", dest="op_name", default="",
                    help="contract_pin_approve: какую операцию подтвердить; "
                         "contract_pin_history: чью историю показать; "
                         "contract_pin_sample: какие операции пробовать "
                         "(через запятую, пусто — все из списка)")
    pq.add_argument("--all-ops", dest="all_ops", action="store_true",
                    help="contract_pin_approve: подтвердить описание всех операций")
    pq.add_argument("--approved-by", dest="approved_by", default="",
                    help="contract_pin_approve: кто подтверждает (обязательно); "
                         "contract_pin_sample: кто принимает новое поведение "
                         "(обязательно при mode=accept)")
    pq.add_argument("--pin-note", dest="pin_note", default="",
                    help="contract_pin_approve: пометка к пину "
                         "(имя отличается от --note других подкоманд)")
    pq.add_argument("--contract-kind", dest="contract_kind", default="",
                    choices=["", "op", "tool", "all"],
                    help="contract_pin_approve: какой контракт подтвердить "
                         "(op — операции, tool — инструменты MCP, all — оба; "
                         "по умолчанию op); contract_pin_history: за каким "
                         "контрактом следить (пусто или all — оба)")
    pq.add_argument("--sample-mode", dest="sample_mode", default="compare",
                    choices=["take", "compare", "accept"],
                    help="contract_pin_sample: take — записать пробу поведения, "
                         "compare — сверить с записанной (по умолчанию), "
                         "accept — принять новое поведение (требует --approved-by)")
    pq.add_argument("--only-changed", dest="only_changed", action="store_true",
                    help="contract_pin_sample: только пробы, разошедшиеся при "
                         "последней сверке")
    pq.add_argument("--except", dest="except_ops", default="",
                    help="contract_pin_sample: исключить операции (через запятую)")
    pq.add_argument("--before", default="",
                    help="contract_diff: манифест описаний ДО выпуска (ops_pins.json)")
    pq.add_argument("--after", default="",
                    help="contract_diff: манифест описаний ПОСЛЕ выпуска")
    pq.add_argument("--root", default="",
                    help="preflight: каталог для осмотра (по умолчанию корень корпуса)")
    pq.add_argument("--sample", type=int, default=50,
                    help="preflight: сколько файлов разобрать для замера времени")
    pq.add_argument("--label", default="", help="quality_suite: пометка прогона")
    pq.add_argument("--no-record", dest="no_record", action="store_true",
                    help="quality_suite: измерить, но не записывать прогон")
    pq.add_argument("--session", default="",
                    help="quality_suite/quality_trace: сеанс, чьи кейсы поведения проверяются")
    pq.add_argument("--trace-action", dest="trace_action", default="list",
                    choices=["save", "list", "replay"],
                    help="quality_trace: что сделать с трассой сбоя")
    pq.add_argument("--case-id", dest="case_id", default="",
                    help="quality_trace: кейс, чей сбой записывается")
    pq.add_argument("--trace-id", dest="trace_id", type=int, default=0,
                    help="quality_trace: трасса для повтора")
    pq.add_argument("--from-run", dest="from_run", type=int, default=0,
                    help="quality_diff/quality_pair: номер прогона «от»")
    pq.add_argument("--to-run", dest="to_run", type=int, default=0,
                    help="quality_diff/quality_pair: номер прогона «до»")
    # Прореживание журнала прогонов (ПРОМТ-И10). Три аргумента, и все три
    # названы явно: `--keep`, `--dry-run` (по умолчанию включён), `--confirm`.
    # Отдельный `--no-dry-run` нужен потому, что argparse не умеет «флаг,
    # включённый по умолчанию», и без него удаление требовало бы двух команд.
    pq.add_argument("--keep", type=int, default=None,
                    help="quality_prune: сколько последних прогонов оставить "
                         "(по умолчанию — из предела журнала)")
    pq.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="quality_prune: выполнять удаление (нужен ещё --confirm)")
    pq.add_argument("--confirm", dest="confirm", action="store_true",
                    help="quality_prune: подтверждение необратимого удаления")
    pq.add_argument("--start", type=int, default=None)
    pq.add_argument("--end", type=int, default=None)
    pq.add_argument("--kind", default=None,
                    help="search: вид чанка (file, symbol, table, route, doc); "
                         "edge_evidence: вид ребра (calls, calls_heuristic, …, all)")
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
    # neighbors: сколько хитов брать из каждого соседа, каким стеком сравнивать
    # и сколько общих языков достаточно. Разделены явно: у поиска по одному
    # корпусу этих понятий нет, а у кросс-корпусного — есть.
    pq.add_argument("--per-corpus", dest="per_corpus", type=int, default=5,
                    help="neighbors: сколько хитов брать из каждого соседа")
    pq.add_argument("--stack", default=None,
                    help="neighbors: стек через запятую вместо измеренного (php,sql)")
    pq.add_argument("--min-shared", dest="min_shared", type=int, default=1,
                    help="neighbors: сколько общих языков считается соседством")
    pq.add_argument("--no-text", dest="nb_text", action="store_false", default=True,
                    help="neighbors: не добавлять фрагмент текста к хитам")
    # `code --source`: по умолчанию текст берётся из индекса, `disk` — прежнее
    # поведение (чтение рабочего дерева).
    pq.add_argument("--source", default="auto",
                    choices=list(OP_CHOICES[("code", "source")]))
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

    pe = sub.add_parser("edges",
                        help="рёбра символа: направления и вид доказательства")
    pe.add_argument("--fqn", default=None, help="символ (или --name)")
    pe.add_argument("--name", default=None)
    pe.add_argument("--kind", default=None,
                    help="вид ребра (calls, calls_heuristic, …, all); по умолчанию "
                         "только рёбра вызовов")
    pe.add_argument("--evidence", action="store_true",
                    help="печатать вид доказательства и координату у каждого ребра")
    pe.add_argument("--limit", type=int, default=200)
    # `--json` объявлен и здесь: глобальный флаг разбирается ДО подкоманды, а
    # `ragraf edges --fqn X --json` — обычная форма записи, и отказ на неё
    # читался бы как «команда сломана».
    pe.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    pe.set_defaults(func=cmd_edges)

    pdr = sub.add_parser("drop", help="удалить данные текущей ревизии (история сохраняется)")
    pdr.set_defaults(func=cmd_drop)

    pco = sub.add_parser("compact", help="политика хранения: объём, хвост ротации, VACUUM")
    pco.add_argument("--plan", action="store_true",
                     help="только числа, ничего не менять (поведение по умолчанию)")
    pco.add_argument("--apply", action="store_true",
                     help="выполнить очистку; требует --yes")
    pco.add_argument("--yes", action="store_true",
                     help="подтверждение необратимой очистки")
    pco.add_argument("--vacuum", action="store_true",
                     help="выполнить VACUUM независимо от порога свободных страниц")
    pco.add_argument("--no-vacuum", action="store_true",
                     help="не выполнять VACUUM даже при превышении порога")
    pco.add_argument("--retain", type=int, default=None,
                     help="сколько последних ревизий хранить (по умолчанию retain_revs)")
    pco.set_defaults(func=cmd_compact)

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

    pfb = sub.add_parser("feedback", help="локальный отчёт о пользе (без сети)")
    pfb.add_argument("--days", type=int, default=30, help="период отчёта в днях")
    pfb.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    pfb.set_defaults(func=cmd_feedback)

    pan = sub.add_parser("analytics", help="экспорт и удаление локальной аналитики")
    pan.add_argument("action", choices=["export", "delete"], nargs="?", default="export")
    pan.add_argument("--days", type=int, default=30)
    pan.add_argument("--out", default=None, help="куда записать JSON (по умолчанию catalog/analytics/)")
    pan.add_argument("--yes", action="store_true",
                     help="подтвердить удаление журнала (копия создаётся обязательно)")
    pan.add_argument("--json", action="store_true")
    pan.set_defaults(func=cmd_analytics)

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
    pens.add_argument("--no-vectors", action="store_true",
                      help="индексировать без векторного слоя (нет загрузки модели)")
    pens.set_defaults(func=cmd_ensure, needs_config=False)

    pcat = sub.add_parser("catalog", help="реестр разработок: что индексировано и с какими суммами")
    pcat.set_defaults(func=cmd_catalog, needs_config=False)

    # Замки: состояние и уборка брошенных. Существует потому, что брошенный
    # замок — невидимый в работе сбой: корпус просто отвечает «уже идёт»,
    # и без отдельной команды это ищется глазами в `catalog/locks/`.
    plocks = sub.add_parser("locks", help="замки индексации и реестра: кто держит, что брошено")
    plocks.add_argument("--clear", action="store_true",
                        help="снять брошенные замки (держатель мёртв или замок не читается)")
    plocks.add_argument("--force", action="store_true",
                        help="снять и те, чей процесс жив, но молчит: нужна причина (--reason)")
    plocks.add_argument("--reason", default="", help="причина принудительного снятия")
    plocks.add_argument("--older-than", type=float, default=None,
                        help="порог молчания в секундах для --force (по умолчанию любой)")
    plocks.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    plocks.set_defaults(func=cmd_locks, needs_config=False)

    # --- живые MCP-серверы: штатный перезапуск после правки кода ---
    psrv = sub.add_parser(
        "server",
        help="живые MCP-серверы: статус и перезапуск после правки кода")
    ssub = psrv.add_subparsers(dest="server_action")
    sst = ssub.add_parser(
        "status",
        help="живые серверы, их отпечаток сборки и расхождение с диском")
    sst.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    sst.set_defaults(func=cmd_server)
    srs = ssub.add_parser(
        "restart",
        help="завершить серверы: клиент поднимет их на новом коде")
    srs.add_argument("--reason", default="", help="причина перезапуска (в отчёт)")
    srs.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    srs.set_defaults(func=cmd_server)
    psrv.set_defaults(func=cmd_server, server_action="status")

    # --- «Я это ты»: разрешённые корни и карта метаданных (G0) ---
    psrc = sub.add_parser("sources", help="разрешённые корни для карты метаданных")
    psrc.add_argument("action", choices=["list", "add", "remove", "enable", "disable",
                                         "priority", "secrets"], nargs="?", default="list")
    psrc.add_argument("ident", nargs="?", default=None,
                      help="id или путь источника (для remove/enable/disable/priority)")
    psrc.add_argument("value", nargs="?", default=None, help="значение приоритета")
    psrc.add_argument("--root", default=None, help="путь к каталогу (для add)")
    psrc.add_argument("--kind", choices=["project", "folder", "share"], default=None)
    psrc.add_argument("--priority", type=int, default=0)
    psrc.add_argument("--note", default="")
    psrc.add_argument("--network", action="store_true",
                      help="разрешить сетевой (UNC) корень: без флага такой путь отклоняется")
    psrc.add_argument("--source", type=int, default=None, help="id источника (для secrets)")
    psrc.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    psrc.set_defaults(func=cmd_sources, needs_config=False)

    pmap = sub.add_parser("map", help="карта метаданных разрешённых корней")
    pmap.add_argument("action", choices=["status", "scan", "find"], nargs="?", default="status")
    pmap.add_argument("text", nargs="?", default="", help="запрос для find")
    pmap.add_argument("--source", default=None, help="id или путь источника (для scan)")
    pmap.add_argument("--max-files", type=int, default=None)
    pmap.add_argument("--max-seconds", type=float, default=None)
    pmap.add_argument("--limit", type=int, default=50)
    pmap.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    pmap.set_defaults(func=cmd_map, needs_config=False)

    # Маршрут без конфига: вопрос «какой индекс использовать» задаётся до
    # выбора корпуса. С конфигом доступен и `query route`.
    proute = sub.add_parser("route", help="куда идти за ответом: карта и локальные индексы")
    proute.add_argument("text", nargs="?", default="", help="что ищем")
    proute.add_argument("--limit", type=int, default=10)
    proute.add_argument("--no-verify", dest="verify", action="store_false", default=True,
                        help="не сверять отпечаток рекомендованного корпуса")
    proute.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    proute.set_defaults(func=cmd_route, needs_config=False)

    # --- хранилище секретов (решение владельца 2026-10-24) ---
    psec = sub.add_parser(
        "secrets",
        help="хранилище секретов: значения, права агентов, аудит",
    )
    psec.add_argument("action", nargs="?", default="list",
                      choices=["init", "set", "list", "show", "reveal", "rm",
                               "grant", "revoke", "check", "rotate", "export",
                               "import", "policy", "audit"])
    psec.add_argument("name", nargs="?", default="", help="имя секрета")
    psec.add_argument("--kind", default=None,
                      help="тип: password, token, api_key, private_key, "
                           "certificate, connection_string, ssh_key, webhook, "
                           "note, other")
    psec.add_argument("--service", default="", help="к какому сервису относится")
    psec.add_argument("--label", default="", help="человеческое название")
    psec.add_argument("--owner", default="", help="кто отвечает за значение")
    psec.add_argument("--note", default="", help="заметка без значения")
    psec.add_argument("--tags", default="", help="метки через запятую")
    psec.add_argument("--project", default="", help="корпус или проект")
    psec.add_argument("--visibility", default=None,
                      choices=["all", "listed", "owner_only"],
                      help="кому доступна запись")
    psec.add_argument("--agents", default="",
                      help="имена агентов через запятую (для listed)")
    psec.add_argument("--expires", default="", help="срок: 2027-01-31T00:00:00Z")
    psec.add_argument("--reason", default="", help="причина выдачи (аудит)")
    psec.add_argument("--captured", action="store_true",
                      help="сохранено как встреченное (нужно включённое сохранение)")
    psec.add_argument("--from-env", default=None,
                      help="взять значение из переменной окружения")
    psec.add_argument("--from-file", default=None,
                      help="взять значение из файла")
    psec.add_argument("--passphrase-env", default=None,
                      help="переменная с парольной фразой для export/import")
    psec.add_argument("--out", default=None, help="файл снимка для export/import")
    psec.add_argument("--query", default="", help="поиск по имени и метаданным")
    psec.add_argument("--tag", default="", help="фильтр по метке")
    psec.add_argument("--limit", type=int, default=50)
    psec.add_argument("--force", action="store_true", help="перезаписать ключ")
    psec.add_argument("--verify", action="store_true",
                      help="audit: пересчитать цепочку хэшей журнала")
    psec.add_argument("--export", default=None,
                      help="audit: выгрузить журнал файлом JSONL с подписью")
    psec.add_argument("--no-sign", action="store_true",
                      help="audit --export: не подписывать выгрузку")
    psec.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    psec.set_defaults(func=cmd_secrets, needs_config=False)

    # --- издание продукта (слой изданий) ---
    ped = sub.add_parser(
        "edition",
        help="какое издание действует, что ему разрешено и чем это подтверждено",
    )
    ped.add_argument("--declared", default="",
                     help="проверить это заявленное издание вместо настройки")
    ped.add_argument("--license", default=None,
                     help="файл лицензии (по умолчанию catalog/edition.license)")
    ped.add_argument("--baseline", default=None,
                     help="файл базовой линии изданий (по умолчанию в пакете)")
    ped.add_argument("--public-key", default=None,
                     help="доверенный открытый ключ лицензии (base64); "
                          "\"-\" — не доверять ни одному ключу")
    # --json здесь НЕ объявляется: он уже есть у главного разбора, а значение
    # подпарсера перебило бы его, и машиночитаемый вывод молча не включался.
    ped.set_defaults(func=cmd_edition, needs_config=False)

    pdg = sub.add_parser("docgraph",
                         help="документный граф: состояние слоя и план прогона")
    dsub = pdg.add_subparsers(dest="docgraph_action", required=True)
    dstatus = dsub.add_parser("status", help="что настроено и что собрано")
    dstatus.set_defaults(func=cmd_docgraph, needs_config=True)
    dbuild = dsub.add_parser("build", help="прогон извлечения (этап 2)")
    dbuild.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="только план: сколько чанков и вызовов, без сети")
    dbuild.add_argument("--limit", type=int, default=None,
                        help="ограничить число чанков в прогоне")
    dbuild.set_defaults(func=cmd_docgraph, needs_config=True)
    dthemes = dsub.add_parser("themes",
                              help="построить темы слоя (бесплатно, без модели)")
    dthemes.add_argument("--summaries", action="store_true",
                         help="собрать и сводки тем (платно: обращение к LLM на тему)")
    dthemes.add_argument("--limit", type=int, default=None,
                         help="сколько тем сводить (по умолчанию все, до предела)")
    dthemes.set_defaults(func=cmd_docgraph, needs_config=True)

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
    except IndexNotBuilt as exc:
        return _index_not_built(exc)
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
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # Код 0 — это `--help`: вызов не отклонён, и шуметь не о чем.
        if int(getattr(exc, "code", 0) or 0) != 0:
            _note_bad_arguments(list(sys.argv[1:] if argv is None else argv))
        raise

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
    except IndexNotBuilt as exc:
        return _index_not_built(exc)
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


# ---------------------------------------------------------------------------
# Документный граф: команда стоит В КОНЦЕ файла намеренно. Вставка в
# середину сдвинула бы номера строк, на которые ссылаются документы и две
# копии навыка (`tools/check_line_refs.py` держит шесть таких ссылок).
# ---------------------------------------------------------------------------
def _docgraph_denial() -> str:
    """Причина отказа по возможности `doc-graph` или пустая строка.

    Общая для двух путей запуска прохода (`docgraph build` и `index --doc-graph`):
    гейт, реализованный в одном из них, был бы половиной гейта.
    """
    from . import edition as edition_mod

    return edition_mod.feature_denial("doc-graph")


def _run_docgraph_build(cfg: ProjectConfig, limit: int | None = None) -> dict:
    """Выполнить проход извлечения по активной ревизии.

    Проход — платная часть продукта, поэтому здесь стоит ТА ЖЕ проверка издания,
    что и у операций чтения. Без неё гейт закрывал бы чтение и открывал запись:
    `docgraph build` строил бы слой под Community, а `entity_neighbors` его не
    показывал.

    Ревизия берётся из паспорта (`rev.json`) — той же, по которой отвечают
    операции чтения. Проход пишет слой В АКТИВНУЮ ревизию: так же поступает
    живой слой баз (`cmd_db`), и второй ревизии ради фактов никто не заводит.
    """
    from . import docgraph as docgraph_mod
    from . import docgraph_extract as extract_mod
    from .facade import Facade

    denial = _docgraph_denial()
    if denial:
        return {
            "op": "docgraph_build", "index_rev": "", "collected": False,
            "skipped": False, "code": "edition_refused", "reason": denial,
            "llm": {"state": "unknown", "configured": False,
                    "reason": "проверка не выполнялась: проход не разрешён изданием"},
            "stats": {"chunks": 0, "calls": 0, "cache_hits": 0, "entities": 0,
                      "mentions": 0, "relations": 0,
                      "would_write": {"entities": 0, "mentions": 0, "relations": 0},
                      "rejected": {}},
            "notes": [denial], "seconds": 0.0,
            "fingerprint": {"current": "", "recorded": "", "matches": False},
        }

    facade = Facade(cfg, tool="cli", telemetry=False, watch=False)
    try:
        rev = facade.current_rev()
    finally:
        facade.close()
    if not rev:
        return {
            "op": "docgraph_build", "index_rev": "", "collected": False,
            "skipped": False, "code": "no_revision",
            "reason": "в корпусе нет активной ревизии: сначала `ragraf index`",
            "llm": {"state": "unknown", "configured": False,
                    "reason": "проверка не выполнялась"},
            "stats": {"chunks": 0, "calls": 0, "cache_hits": 0, "entities": 0,
                      "mentions": 0, "relations": 0,
                      "would_write": {"entities": 0, "mentions": 0, "relations": 0},
                      "rejected": {}},
            "notes": [], "seconds": 0.0,
            "fingerprint": docgraph_mod.current_fingerprint(),
        }
    return extract_mod.run(cfg, str(rev), limit=limit)


def _run_docgraph_themes(cfg: ProjectConfig, *, summaries: bool = False,
                         limit: int | None = None) -> dict:
    """Построить темы слоя: кластеры бесплатны, сводки — только по флагу.

    Гейт тот же, что у прохода извлечения: темы читают слой, который объявлен
    возможностью `doc-graph`. Разница только в цене: кластеры считаются без
    модели и без сети, поэтому их можно пересобирать сколько нужно.
    """
    from . import docgraph as docgraph_mod
    from . import llm as llm_mod
    from . import settings as settings_mod
    from . import store
    from . import themes as themes_mod
    from .facade import Facade

    denial = _docgraph_denial()
    result: dict = {"op": "docgraph_themes", "index_rev": "", "collected": False,
                    "code": "", "reason": "", "themes": 0, "summaries": 0,
                    "notes": [], "limits": {"min_weight": themes_mod.MIN_WEIGHT_DEFAULT,
                                            "max_size": themes_mod.MAX_SIZE}}
    if denial:
        result.update(code="edition_refused", reason=denial, notes=[denial])
        return result

    facade = Facade(cfg, tool="cli", telemetry=False, watch=False)
    try:
        rev = facade.current_rev()
    finally:
        facade.close()
    result["index_rev"] = str(rev or "")
    if not rev:
        result.update(code="no_revision",
                      reason="в корпусе нет активной ревизии: сначала `ragraf index`")
        return result

    conn = None
    try:
        conn = store.connect(cfg.lexvec_db)
        built = themes_mod.build(conn, rev)
        result["themes"] = int(built.get("stored") or 0)
        result["stats"] = {key: built.get(key) for key in
                           ("mentions", "edges", "kept_edges", "components")}
        if built.get("reason"):
            result["notes"].append(str(built["reason"]))
        result["collected"] = bool(built.get("themes"))
        if summaries and built.get("themes"):
            values = dict(settings_mod.DEFAULTS)
            try:
                values.update(dict(settings_mod.SettingsStore().load()))
            except Exception:  # noqa: BLE001 — настройки не имеют права ронять команду
                pass
            client = llm_mod.LLMClient(cfg, config=llm_mod.config_from_settings(values))
            state = client.status()
            if not state.get("configured"):
                result.update(code=themes_mod.DEGRADE_NO_LLM,
                              reason=str(state.get("reason") or "LLM не настроен"))
                result["notes"].append(themes_mod.describe_degraded(
                    themes_mod.DEGRADE_NO_LLM, result["reason"]))
            else:
                summary_stats = themes_mod.summaries(
                    conn, rev, client,
                    limit=int(limit) if limit else themes_mod.MAX_THEMES,
                    model=str(values.get("llm_model") or ""))
                result["summaries"] = int(summary_stats.get("written") or 0)
                result["notes"].extend(str(n) for n in summary_stats.get("notes") or [])
                if summary_stats.get("reason"):
                    result["notes"].append(str(summary_stats["reason"]))
        return result
    except Exception as exc:  # noqa: BLE001 — отказ обязан быть текстом
        result.update(code="themes_failed", reason=f"{type(exc).__name__}: {exc}")
        return result
    finally:
        if conn is not None:
            conn.close()


def cmd_docgraph(args: argparse.Namespace, cfg: ProjectConfig) -> int:
    """Документный граф: состояние слоя и план прогона.

    Две подкоманды, и обе без сети. `status` отвечает, что настроено и что
    собрано; `build --dry-run` считает вход будущего прогона — именно тогда он и
    нужен, когда LLM ещё не настроен.

    Извлечения на этом этапе нет, поэтому `build` без `--dry-run` отказывает
    кодом 2 с названной причиной. Молча ничего не сделать значило бы выдать
    несобранный слой за собранный — тот самый класс дефекта, ради которого
    заведено правило «нет данных и не собрано различаются полем и причиной».
    """
    from . import docgraph as docgraph_mod

    action = str(getattr(args, "docgraph_action", "") or "status")
    try:
        if action == "status":
            data = docgraph_mod.build_status(cfg)
        elif action == "themes":
            data = _run_docgraph_themes(cfg,
                                        summaries=bool(getattr(args, "summaries", False)),
                                        limit=getattr(args, "limit", None))
        elif getattr(args, "dry_run", False):
            data = docgraph_mod.build_plan(cfg, limit=getattr(args, "limit", None))
        else:
            data = _run_docgraph_build(cfg, limit=getattr(args, "limit", None))
    except Exception as exc:  # noqa: BLE001 — отказ обязан быть текстом, а не трассировкой
        print(f"docgraph: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        _print(data, True)
    else:
        _print_docgraph(data)
    # Код возврата называет ФАКТ, и только у сборки. У `status` и сухого
    # прогона он всегда успешный: это отчёты, а не работа. Молчаливый ноль при
    # отказе провайдера означал бы «всё хорошо» там, где не собрано ничего.
    if action == "build" and not getattr(args, "dry_run", False):
        return EXIT_OK if data.get("collected") else EXIT_ERROR
    return EXIT_OK


def _print_docgraph(data: dict) -> None:
    """Текстовый ответ `docgraph`: состояние, отпечаток, слой, план.

    Печатается то же, что отдаётся в JSON, и теми же словами: два формата одного
    ответа расходятся ровно так, как когда-то разошлись два списка операций.
    """
    op = data.get("op")
    llm = data.get("llm") or {}
    print(f"[{op}] ревизия {data.get('index_rev') or '—'} "
          f"состояние {data.get('capability')}")
    print(f"  LLM: {llm.get('state')} "
          f"({'настроен' if llm.get('configured') else 'НЕ настроен'}), "
          f"провайдер {llm.get('provider')}, модель {llm.get('model') or '—'}, "
          f"адрес {llm.get('base_url')}")
    if llm.get("reason"):
        print(f"    причина: {llm['reason']}")
    fp = data.get("fingerprint") or {}
    if isinstance(fp, dict):
        parts = fp.get("parts") or {}
        print(f"  отпечаток: {str(fp.get('current') or '')[:16]}… "
              f"записан: {'да' if fp.get('recorded') else 'нет'} "
              f"совпадает: {'да' if fp.get('matches') else 'нет'}")
        if parts:
            print(f"    вход: модель {parts.get('model') or '—'}, "
                  f"промпт {parts.get('prompt_version') or '—'}, "
                  f"температура {parts.get('temperature')}, "
                  f"извлечение {parts.get('extraction')}")
    layer = data.get("layer") or {}
    if layer:
        print(f"  слой: документов {layer.get('documents', 0)}, "
              f"чанков {layer.get('chunks', 0)}, "
              f"сущностей {layer.get('entities', 0)}, "
              f"упоминаний {layer.get('mentions', 0)}, "
              f"отношений {layer.get('relations', 0)}, "
              f"тем {layer.get('themes', 0)}, "
              f"сводок {layer.get('summaries', 0)}")
    stats = data.get("stats") or {}
    if stats:
        would = stats.get("would_write") or {}
        print(f"  проход: чанков {stats.get('chunks', 0)}, "
              f"отправлено {stats.get('chunks_sent', 0)}, "
              f"вызовов {stats.get('calls', 0)}, из кэша {stats.get('cache_hits', 0)}")
        print(f"  слой: сущностей {stats.get('entities', 0)}, "
              f"упоминаний {stats.get('mentions', 0)}, "
              f"отношений {stats.get('relations', 0)}"
              + ("" if not would.get("entities") else
                 f"; к записи было: сущностей {would.get('entities')}, "
                 f"упоминаний {would.get('mentions')}, "
                 f"отношений {would.get('relations')}")
              + (" (пропущено: вход не изменился)" if data.get("skipped") else ""))
        rejected = stats.get("rejected") or {}
        if rejected:
            print("  отказы разбора: " + ", ".join(
                f"{k}={v}" for k, v in sorted(rejected.items())))
    if (data.get("op") == "docgraph_build" and not data.get("collected")
            and data.get("reason")):
        print(f"  НЕ СОБРАНО ({data.get('code') or 'код не назван'}): {data['reason']}")
    plan = data.get("plan") or {}
    if plan:
        print(f"  план: чанков {plan.get('chunks')} "
              f"(в прогон {plan.get('chunks_selected')}), "
              f"вызовов {plan.get('calls_estimated')}, "
              f"знаков {plan.get('chars')} (~{plan.get('tokens_estimated')} токенов), "
              f"в кэше уже {plan.get('cached_rows')}")
    integrity = data.get("integrity") or {}
    if integrity:
        verdict = "норма" if integrity.get("ok") else "НАРУШЕНИЯ"
        print(f"  целостность слоя: {verdict}, "
              f"проблем {len(integrity.get('problems') or [])}")
        if integrity.get("reason"):
            print(f"    {integrity['reason']}")
    for note in data.get("notes") or []:
        print(f"  · {note}")


if __name__ == "__main__":
    raise SystemExit(main())
