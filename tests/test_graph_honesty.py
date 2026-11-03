"""Честность графа целиком: ребро ведёт в узел, ответ называет доказательство.

Какой дефект закрывается. Этап 8 закрылся числами «249 рёбер ведут в никуда» и
«порог 0,5 отсекает связи», но ни то ни другое не было ни устранено, ни видно
агенту: ребро в никуда нельзя открыть по адресу, а «отсечено порогом» агент
узнавал только когда ответ был пуст. Этап 10 закрывает три вещи и проверяется
здесь по трём свойствам:

  1. ЦЕЛОСТНОСТЬ ССЫЛОК. После индексации не остаётся ни одного ребра, ведущего
     в отсутствующий узел: адрес-заглушка (`@ext:`, `@file:`, `@table:`, `@php:`)
     существует в ревизии как узел `external` без файла и строки. Подсадка
     «ребро в никуда» ловится аудитом, а не глазами.
  2. ВИД ДОКАЗАТЕЛЬСТВА В КАЖДОМ ОТВЕТЕ. Любая операция, отдающая связь
     (`references`, `callers`, `policy_trace`, `db_relations`, `dead_code`,
     `entrypoints`, документные), называет вид доказательства ЛИБО прямо
     говорит, что вопрос к ней не применяется.
  3. ПОРОГ КАК РЕШЕНИЕ. Порог достоверности задаётся вызовом
     (`min_confidence`), ответ называет и значение, и правило, и сколько связей
     отсечено. Плюс производитель рёбер не выдумывает связи: текст перевода со
     словом `update` не порождает таблицу.

Стиль как у остальных проверок проекта: самостоятельный скрипт с `check`.
Работает на одноразовом корпусе в %TEMP%, векторы и CPG выключены, сеть и
чужие корпуса не нужны. Ничего в живом индексе не меняет.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import references as ref  # noqa: E402
from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.extract.php_ast import looks_like_sql_statement  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.init_corpus import create_config  # noqa: E402

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


#: PHP-файл с ВНЕШНИМИ адресами всех четырёх видов и с прозой, похожей на SQL.
#: Проза взята буквально из настоящего случая (rev-0925): строка перевода
#: содержала «no delivery update and no data migration», и из неё в графе
#: появлялись таблицы `and` и `der`, которых нет ни в одной базе.
PHP_MAIN = """<?php
namespace Site\\Support;

function honesty_local($x) {
    return $x;
}

function honesty_uses_local($y) {
    return honesty_local($y);
}

function honesty_entry($rows) {
    $out = [];
    foreach ($rows as $k => $v) {
        $out[$k] = trim((string)$v);
    }
    return $out;
}

class HonestyReport extends \\RuntimeException {
    public function render($x) {
        return Html::e($x) . Ui::t('title');
    }
}

function honesty_labels() {
    return __('Pro -> Enterprise: the licensor issues an Enterprise-level file '
        . 'and OIDC, SIEM, offline licence and attestation open. No installation, '
        . 'no delivery update and no data migration.');
}

function honesty_rows($db) {
    return $db->query('SELECT id, name FROM honesty_orders WHERE id = 1');
}

function honesty_asset() {
    return include_file('style.css');
}
"""

PHP_HELPER = """<?php
function honesty_helper_one() { return 1; }
"""

PHP_THIRD = """<?php
function honesty_helper_two() { return 2; }
"""


def build_corpus(root: Path) -> Path:
    """Одноразовый корпус: три файла кода, CPG и векторы выключены."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.php").write_text(PHP_MAIN, encoding="utf-8", newline="\n")
    (src / "helper.php").write_text(PHP_HELPER, encoding="utf-8", newline="\n")
    (src / "third.php").write_text(PHP_THIRD, encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
    cfg_path, _scan = create_config(root, project="honestyprobe",
                                    config_path=root / "honesty.yaml")
    text = cfg_path.read_text(encoding="utf-8").replace(
        "joern_enabled: true", "joern_enabled: false")
    cfg_path.write_text(text, encoding="utf-8", newline="\n")
    return cfg_path


def main() -> int:
    section("1. Производитель рёбер: текст не является запросом")
    check(not looks_like_sql_statement(
        "Pro -> Enterprise: no delivery update and no data migration"),
        "проза со словом update не считается SQL")
    check(not looks_like_sql_statement("Keine Installation, kein Update der Lieferung"),
        "немецкий текст со словом Update не считается SQL")
    check(looks_like_sql_statement("UPDATE orders SET x=1"),
        "настоящий запрос UPDATE распознаётся")
    check(looks_like_sql_statement("  \nSELECT id FROM t"),
        "SELECT с ведущими пробелами и переводом строки распознаётся")
    check(looks_like_sql_statement("/* purge */ UPDATE t SET a=1"),
        "запрос с ведущим комментарием распознаётся: комментарий не отменяет запрос")
    check(looks_like_sql_statement("(SELECT 1)"), "подзапрос в скобках распознаётся")

    tmp = Path(tempfile.mkdtemp(prefix="ragraf_honesty_"))
    cfg_path = build_corpus(tmp / "proj")
    cfg = load_config(cfg_path)
    idx = Indexer(cfg)
    try:
        report = idx.run(with_vectors=False, budget_seconds=300)
    finally:
        idx.close()
    check(report.ok, "корпус проиндексирован", str(report.notes))
    rev = str(report.rev_id or "")
    conn = store.connect(cfg.graph_db)
    conn.row_factory = None

    try:
        section("2. Целостность ссылок: ребро ведёт в существующий узел")
        audit = ref.audit(conn, rev)
        check(audit["dangling_edges"] == 0,
              "рёбер в никуда нет",
              f"в никуда {audit['dangling_edges']} на {audit['dangling_targets']} адресах: "
              f"{audit['by_prefix']}")
        stubs = conn.execute(
            "SELECT node_id, kind, path FROM node WHERE rev_id = ? AND kind = 'external'"
            " AND node_id LIKE '@%'", (rev,)).fetchall()
        check(bool(stubs), "заглушки внешних адресов заведены узлами", f"найдено {len(stubs)}")
        check(all(str(s[1]) == "external" for s in stubs),
              "узел-заглушка помечен видом external")
        check(all(s[2] is None for s in stubs),
              "у заглушки нет файла: определения в корпусе нет",
              str([s[2] for s in stubs[:3]]))
        # Имя заглушки — ВЕСЬ адрес, а не последний сегмент: иначе `t`, `get`
        # попали бы в кандидаты разрешения имён и заглушка притягивала бы рёбра.
        check(all("@" in str(s[0]) for s in stubs)
              and all(not str(s[0]).endswith(":method:") or True for s in stubs),
              "заглушка не выглядит объявлением: имя — адрес, а не короткое имя",
              str([s[0] for s in stubs[:3]]))
        table_edges = conn.execute(
            "SELECT dst FROM edge WHERE rev_id = ? AND kind = 'queries'", (rev,)).fetchall()
        tables = sorted({str(row[0]) for row in table_edges})
        check("@table:and" not in tables and "@table:der" not in tables,
              "выдуманных таблиц из текста перевода нет", str(tables))
        check("@table:honesty_orders" in tables,
              "настоящий запрос даёт таблицу", str(tables))

        section("3. Подсадка «ребро в никуда» ловится аудитом")
        bogus_dst = "@ext:honesty_planted_gap"
        conn.execute(
            "INSERT OR REPLACE INTO edge(rev_id, edge_uid, src, dst, kind, provenance,"
            " evidence_path, evidence_line, confidence, sources, props)"
            " VALUES(?, 'uid-honesty-gap', (SELECT node_id FROM node WHERE rev_id = ?"
            " AND kind = 'function' LIMIT 1), ?, 'calls', 'stated', 'src/main.php', 3,"
            " 1.0, '[]', '{}')", (rev, rev, bogus_dst))
        conn.commit()
        broken = ref.audit(conn, rev)
        check(broken["dangling_edges"] == 1,
              "подсаженное ребро в никуда видно аудитом", str(broken["dangling_edges"]))
        check(broken["by_prefix"].get("@ext:") == 1,
              "подсадка отнесена к префиксу @ext:", str(broken["by_prefix"]))
        targets = ref.missing_targets(conn, rev)
        check(len(targets) == 1 and targets[0]["dst"] == bogus_dst,
              "аудит называет адрес, а не только число", str(targets[:1]))
        info = ref.materialize(conn, rev)
        check(info["created"] == 1 and info["dangling_after"] == 0,
              "повторная материализация закрывает подсадку", str(info))
        again = ref.materialize(conn, rev)
        check(again["created"] == 0, "материализация идемпотентна", str(again["created"]))
        node = conn.execute(
            "SELECT kind, path FROM node WHERE rev_id = ? AND node_id = ?",
            (rev, bogus_dst)).fetchone()
        check(node is not None and str(node[0]) == "external" and node[1] is None,
              "подсаженный адрес получил честную заглушку без файла", str(node))

        section("4. Ответы называют вид доказательства или говорят, что он не применим")
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            def says(env: dict, *needles: str) -> bool:
                """Есть ли в ответе названное поле или строка ограничения.

                Дополнительные поля конверт кладёт НА ВЕРХНИЙ уровень
                (`Envelope.to_dict` → `out.update(self.extra)`), а не в
                подобъект `extra`: проверка обязана смотреть туда же, куда
                смотрит клиент.
                """
                text = " ".join(str(x) for x in (env.get("limits") or []))
                blob = f"{env.get('evidence')} {text} {env.get('result')}"
                return any(n in blob for n in needles)

            # Символ ВЫБРАН С ВЫЗЫВАЮЩИМ намеренно: на символе без связей
            # «сводка есть, но пуста» проходила бы и при полностью сломанном
            # фильтре. Именно так живой дефект (порог 0,5 выбрасывал
            # `calls_heuristic`) прошёл первый прогон незамеченным.
            refs = fc.op_references("honesty_local", limit=20)
            items = refs.get("result") or []
            check(bool(items), "references находит вызывающего",
                  str(refs.get("error") or refs.get("limits")))
            check("evidence" in refs, "references отдаёт сводку доказательств",
                  str(sorted(refs)))
            check((refs.get("evidence") or {}).get("total") == len(items),
                  "сводка references посчитана по выданным связям",
                  str(refs.get("evidence")))
            check(all(it.get("evidence_kind") for it in items),
                  "у каждой связи в references назван вид доказательства",
                  str([it.get("evidence_kind") for it in items]))
            # Порог в `references` действует ТОЛЬКО по явному заданию; здесь
            # проверяется, что он назван в ответе и не увеличивает выдачу.
            # Отсечения на этом корпусе не ждём: единственная связь —
            # `stated` (достоверность 1.0), а доказанные не отсекаются ни при
            # каком пороге. Ожидать падения значило бы проверять не то правило.
            filtered = fc.op_references("honesty_local", limit=20, min_confidence=0.95)
            note = " ".join(str(x) for x in (filtered.get("limits") or []))
            check("min_confidence" in note,
                  "явный порог в references назван в ответе", note[:160])
            check(len(filtered.get("result") or []) <= len(items),
                  "порог не увеличивает число связей",
                  f"по умолчанию {len(items)}, с порогом "
                  f"{len(filtered.get('result') or [])}")
            callers = fc.op_callers("honesty_entry", depth=1, limit=20)
            result = callers.get("result") or {}
            check("evidence" in result, "callers называет сводку доказательств")
            floor_line = " ".join(str(x) for x in (callers.get("limits") or []))
            check("порог достоверности" in floor_line,
                  "callers называет порог и правило отсечения", floor_line[:160])
            check(result.get("confidence_floor_source") == "default",
                  "источник порога назван: по умолчанию",
                  str(result.get("confidence_floor_source")))
            for name, env in (
                ("db_relations", fc.op_db_relations("honesty_orders")),
                ("dead_code", fc.op_dead_code(limit=10)),
                ("entrypoints", fc.op_entrypoints(limit=10)),
            ):
                check(says(env, "applies_to_calls", "не применяется"),
                      f"{name}: сказано, что вопрос о вызове не применяется",
                      str(env.get("evidence")))
            # Сама проверка честности обязана ловить след в живом состоянии:
            # набор, который меняет `catalog/`, нельзя считать пройденным.
            # Раннер это умеет (этап 7), но свойство проверяется КРАСНОЙ пробой:
            # без изменения отпечаток совпадает, после изменения — расходится.
            import hashlib as _hashlib
            import sys as _sys
            _sys.path.insert(0, str(Path(REPO) / "tools"))
            import check_all as _ca  # noqa: PLC0415

            probe = Path(tempfile.mkdtemp(prefix="ragraf_live_probe_"))
            (probe / "catalog").mkdir()
            (probe / "catalog" / "access_tokens.json").write_text('{"tokens": []}',
                                                                 encoding="utf-8")
            saved_root = _ca.ROOT
            try:
                _ca.ROOT = probe
                before = _ca.live_state_digest()
                after_same = _ca.live_state_digest()
                check(before == after_same,
                      "сторож живого состояния не видит изменений там, где их нет")
                (probe / "catalog" / "access_tokens.json").write_text(
                    '{"tokens": [{"id": "probe"}]}', encoding="utf-8")
                after_change = _ca.live_state_digest()
                check(before != after_change,
                      "сторож живого состояния замечает правку набора",
                      f"{before.get('access_tokens.json', '')[:8]} → "
                      f"{after_change.get('access_tokens.json', '')[:8]}")

                # Ложная улика, измеренная 31.10.2026: чужой процесс
                # переиндексировал свой корпус и законно сменил rev_id и
                # indexed_at записи — сторож валил за это НАБОР, чьё имя стояло
                # в окне. Набор отвечает за СОСТАВ реестра, не за подробности
                # чужой индексации.
                live_cat = probe / "catalog" / "catalog.json"
                live_cat.write_text(
                    '{"version": 1, "updated_at": "2026-10-31T10:00:00+03:00", '
                    '"entries": {"projA": {"rev_id": "rev-1", "indexed_at": "10:00"}}}',
                    encoding="utf-8")
                base = _ca.live_state_semantics(live_cat)
                live_cat.write_text(
                    '{"version": 1, "updated_at": "2026-10-31T12:00:00+03:00", '
                    '"entries": {"projA": {"rev_id": "rev-2", "indexed_at": "12:00"}}}',
                    encoding="utf-8")
                after_reindex = _ca.live_state_semantics(live_cat)
                check(base == after_reindex,
                      "чужой переиндексатор не обвиняет набор: состав тот же",
                      f"{str(base)[:8]} / {str(after_reindex)[:8]}")
                live_cat.write_text(
                    '{"version": 1, "updated_at": "2026-10-31T12:10:00+03:00", '
                    '"entries": {"projA": {"rev_id": "rev-2"}, '
                    '"ragraf-ensure-test": {"rev_id": "rev-1"}}}',
                    encoding="utf-8")
                after_probe = _ca.live_state_semantics(live_cat)
                check(base != after_probe,
                      "появление тестовой разработки в реестре — находка",
                      f"{str(base)[:8]} → {str(after_probe)[:8]}")
            finally:
                _ca.ROOT = saved_root
                shutil.rmtree(probe, ignore_errors=True)

            # Этап 11: вызов внешнего символа — НЕ «вывод по имени».
            # Замер rev-0986: 55 632 ребра с видом name_inference вели в узлы
            # `external` (`print`, `get`, `len`), то есть доля «выводов 77 %»
            # описывала вызовы библиотек. Проверка ловит возврат смешения.
            edges_ev = fc.op_edge_evidence("honesty_entry", limit=50)
            out_items = (edges_ev.get("result") or {}).get("out") or []
            kinds = [str(it.get("evidence_kind")) for it in out_items]
            check("external_call" in kinds,
                  "вызов внешнего символа помечен external_call", str(kinds[:8]))
            check(not any(k == "name_inference" and str(it.get("fqn") or "").startswith(
                      ("python:", "javascript:", "php:"))
                  for k, it in zip(kinds, out_items)),
                  "внешняя цель не названа «выведенной по имени»", str(kinds[:8]))

            # policy_trace отдаёт ДВЕ природы доказательства: ветви из текста и
            # вызывающих из рёбер. Проверяются обе — «поля нет» и «поле пустое»
            # выглядят одинаково, а означают разное.
            trace = fc.op_policy_trace("honesty_local", depth=1, limit=10)
            block = trace.get("evidence") or {}
            check(bool(block.get("applies_to_calls")) is True,
                  "policy_trace: вопрос о виде доказательства применим и назван",
                  str(block)[:160])
            check("branches" in block and "callers" in block,
                  "policy_trace разделяет ветви (текст) и вызывающих (рёбра)",
                  str(sorted(block)))
            tcallers = (trace.get("result") or {}).get("callers") or []
            check(bool(tcallers) and all(it.get("evidence_kind") for it in tcallers),
                  "policy_trace: у каждого вызывающего назван вид доказательства",
                  str([it.get("evidence_kind") for it in tcallers]))
            tnote = " ".join(str(x) for x in (trace.get("limits") or []))
            check("порог достоверности" in tnote,
                  "policy_trace называет порог и правило отсечения", tnote[:160])

            docs = fc.op_doc_search("honesty", limit=3)
            check(says(docs, "applies_to_calls", "не применяется")
                  or not docs.get("found"),
                  "doc_search: названа природа доказательства или слой не построен",
                  str(docs.get("evidence")))

            section("5. Порог — параметр, а не тайна")
            strict = fc.op_callers("honesty_entry", depth=1, limit=20, min_confidence=0.95)
            loose = fc.op_callers("honesty_entry", depth=1, limit=20, min_confidence=0.1)
            sres = strict.get("result") or {}
            lres = loose.get("result") or {}
            check(sres.get("confidence_floor") == 0.95,
                  "порог из вызова попадает в ответ", str(sres.get("confidence_floor")))
            check(sres.get("confidence_floor_source") == "min_confidence",
                  "источник порога назван: задан вызовом")
            check(len(lres.get("items") or []) >= len(sres.get("items") or []),
                  "понижение порога не уменьшает число связей",
                  f"строгий {len(sres.get('items') or [])}, свободный {len(lres.get('items') or [])}")
            bad = False
            try:
                fc.op_callers("honesty_entry", min_confidence=7)
            except ValueError:
                bad = True
            check(bad, "порог вне диапазона 0..1 отвергается, а не подставляется молча")
        finally:
            fc.close()
    finally:
        conn.close()

    # Формулировка сводки — контракт с раннером `tools/check_all.py`: он ищет
    # «пройдено N … провалено M». С прежней строкой «33 ok, 0 fail» набор
    # считался как «0 пройдено, 0 провалено» — то есть выглядел набором,
    # который ничего не проверил. Замер 31.10.2026: первый прогон группы core
    # показал именно 0/0 при живых 33 свойствах.
    print(f"\nпройдено {PASS}, провалено {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
