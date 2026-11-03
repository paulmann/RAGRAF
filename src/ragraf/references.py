"""Целостность ссылок графа: ребро обязано вести в существующий узел.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Замер 30.10.2026 на rev-0925 (закрытие этапа 8): из
78 996 рёбер 249 вели в узел, которого в ревизии нет. Все 249 — на заглушки
`@ext:`, `@file:`, `@table:`, `@php:`, и ни одного такого узла не было ни в одной
ревизии хранилища. Ребро в никуда нельзя открыть по адресу, а операция, которая
его показала, выдаёт вывод за связь: у такой связи нет ни определения, ни места.

ПРАВИЛО ЭТАПА 10. Заглушка обязана существовать в ревизии ЛИБО ребро не должно
возникать. Модуль закрывает первую половину правила: после сборки рёбер он
заводит узел для каждого адреса, на который сослалось ребро. Узел помечен
`kind = 'external'` и не имеет файла с строкой — определения в корпусе нет, есть
только имя. Вторая половина (ребро не должно возникать) закрывается в
производителях: см. `extract/php_ast.py:looks_like_sql_statement`.

ПОЧЕМУ УЗЕЛ, А НЕ УДАЛЕНИЕ РЕБРА. Ребро «вызов внешнего символа» — факт о коде
вызова: он есть, его видно в файле по `evidence_path:evidence_line`. Удалив
ребро, мы потеряли бы этот факт и ответили «вызовов нет». Узел-заглушка
сохраняет факт и при этом не даёт выдать его за связь с определением: у
заглушки нет файла, и ответ обязан назвать её внешней.

ГРАНИЦА. Модуль НЕ решает, доказана ли связь, и не меняет достоверность.
Он отвечает на один вопрос: существует ли узел, на который указывает ребро.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from . import util

#: Префиксы адресов, у которых нет и не может быть объявления в корпусе.
#: Список один на запись и на проверку: производители рёбер (`extract/*`) и
#: операция `references` обязаны отвечать одинаково.
PLACEHOLDER_PREFIXES: tuple[str, ...] = (
    "@ext:",     # внешний символ: базовый класс, метод, пакет вне корпуса
    "@file:",    # путь, упомянутый строковым литералом
    "@table:",   # таблица БД из текста запроса
    "@php:",     # встроенная функция языка
    "@route:",   # адрес маршрута
)

#: Вид узла-заглушки. Совпадает с видом, которым извлекатели помечают внешние
#: символы (`javascript:reduce`, `python:cwd`), поэтому «внешний узел» в ответах
#: виден одинаково независимо от того, кто его создал.
STUB_KIND = "external"

#: Виды рёбер, к которым применяется проверка целостности. Проверяются ВСЕ:
#: ребро в никуда — дефект независимо от вида.
CHECK_ALL = True


def placeholder_prefix(value: Any) -> str:
    """Префикс-заглушка адреса (`@ext:` …) или пустая строка.

    Пустая строка означает «адрес не заглушка»: такой адрес обязан быть
    настоящим узлом, и если его нет — это дефект, а не внешняя ссылка.
    """
    text = str(value or "")
    for prefix in PLACEHOLDER_PREFIXES:
        if text.startswith(prefix):
            return prefix
    return ""


def is_placeholder(value: Any) -> bool:
    """Является ли адрес заглушкой (внешним символом, а не узлом корпуса)."""
    return bool(placeholder_prefix(value))


def stub_name(value: Any) -> str:
    """Имя заглушки для узла: адрес без префикса.

    Имя берётся ЦЕЛИКОМ (`Site\\Support\\Ui::t`), а не последним сегментом
    (`t`). Причина: разрешение имён ищет кандидатов по `node.name`, и короткое
    имя заглушки (`t`, `get`) попало бы в список кандидатов как «настоящее
    определение», то есть заглушка начала бы притягивать к себе ребра.
    """
    text = str(value or "")
    prefix = placeholder_prefix(text)
    tail = text[len(prefix):] if prefix else text
    return tail.strip() or text


def stub_props(value: Any) -> dict:
    """Свойства узла-заглушки: по ним ответ отличает её от определения."""
    prefix = placeholder_prefix(value)
    return {
        "placeholder": True,
        "placeholder_prefix": prefix,
        "shared": True,
        # Формулировка для человека: её показывает `symbol`/`callees`.
        "note": "внешний адрес: объявления в корпусе нет",
    }


def missing_targets(conn, rev_id: str) -> list[dict]:
    """Адреса рёбер, которых нет среди узлов ревизии. Один запрос на всё.

    Возвращает по одной записи на адрес: сколько рёбер на него ведёт, какие
    это виды и какой вид доказательства они несут. Именно этот список —
    источник числа «рёбра в никуда» в отчётах: отдельного счётчика в покрытии
    нет, чтобы число нельзя было записать мимо факта.
    """
    rows = conn.execute(
        """
        SELECT e.dst AS dst,
               COUNT(*) AS edges,
               GROUP_CONCAT(DISTINCT e.kind) AS kinds,
               GROUP_CONCAT(DISTINCT COALESCE(e.evidence_kind, '')) AS evidence
          FROM edge e
          LEFT JOIN node n ON n.rev_id = e.rev_id AND n.node_id = e.dst
         WHERE e.rev_id = ? AND n.node_id IS NULL
         GROUP BY e.dst
         ORDER BY edges DESC, dst
        """,
        (rev_id,),
    ).fetchall()
    # Строки читаются ПО ПОЗИЦИИ, а не по имени колонки: у обычного соединения
    # sqlite3 нет `row_factory`, и обращение по имени падало бы с TypeError.
    # Это уже пятый случай этой ошибки в проекте (та же была в `load_graph`,
    # резолвере fqn и `_resolve_inheritance_names`), поэтому правило одно:
    # в новом коде — только индексы. Проверка поймала это на первом прогоне
    # `tests/test_graph_honesty.py`.
    out: list[dict] = []
    for row in rows:
        dst = str(row[0])
        prefix = placeholder_prefix(dst)
        out.append(
            {
                "dst": dst,
                "edges": int(row[1] or 0),
                "kinds": sorted({k for k in str(row[2] or "").split(",") if k}),
                "evidence": sorted({k for k in str(row[3] or "").split(",") if k}),
                "placeholder": bool(prefix),
                "prefix": prefix,
            }
        )
    return out


def audit(conn, rev_id: str) -> dict:
    """Сводка целостности ссылок ревизии: сколько рёбер ведёт в никуда и куда."""
    total_edges = int(
        conn.execute("SELECT COUNT(*) FROM edge WHERE rev_id = ?", (rev_id,)).fetchone()[0] or 0
    )
    total_nodes = int(
        conn.execute("SELECT COUNT(*) FROM node WHERE rev_id = ?", (rev_id,)).fetchone()[0] or 0
    )
    items = missing_targets(conn, rev_id)
    by_prefix: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    dangling = 0
    non_placeholder: list[dict] = []
    for item in items:
        dangling += item["edges"]
        prefix = item["prefix"] or "(без префикса)"
        by_prefix[prefix] = by_prefix.get(prefix, 0) + item["edges"]
        for kind in item["kinds"]:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        if not item["placeholder"]:
            non_placeholder.append(item)
    return {
        "rev": rev_id,
        "edges": total_edges,
        "nodes": total_nodes,
        "dangling_edges": dangling,
        "dangling_targets": len(items),
        "by_prefix": dict(sorted(by_prefix.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))),
        # Адрес без известного префикса — не внешняя ссылка, а потерянная цель
        # внутри корпуса. Это отдельный класс дефекта, и он называется списком,
        # а не числом: такой адрес надо открыть и посмотреть.
        "non_placeholder": non_placeholder[:20],
        "integrity": dangling == 0,
    }


def _stub_rows(conn, rev_id: str, items: Iterable[Mapping[str, Any]]) -> list[tuple]:
    """Строки узлов-заглушек для списка отсутствующих адресов."""
    known = {
        str(row[0])
        for row in conn.execute(
            "SELECT node_id FROM node WHERE rev_id = ?", (rev_id,)
        ).fetchall()
    }
    rows: list[tuple] = []
    for item in items:
        dst = str(item["dst"])
        if dst in known:
            continue
        prefix = placeholder_prefix(dst)
        if not prefix:
            # Адрес не заглушка: узел обязан был существовать, и создавать ему
            # замену значило бы замести потерю цели. Такой адрес остаётся в
            # отчёте как дефект и разбирается отдельно.
            continue
        rows.append(
            (
                rev_id, dst, STUB_KIND, stub_name(dst), dst,
                None, None, None, None,
                util.dumps(stub_props(dst)), "structural",
            )
        )
        known.add(dst)
    return rows


def materialize(conn, rev_id: str, *, add_nodes=None, batch: int = 2000) -> dict:
    """Завести узлы-заглушки для адресов, на которые ссылаются рёбра.

    Пишет через `add_nodes`, если он передан (индексатор передаёт свой
    `RevisionWriter`): тогда правило записи узлов одно на весь прогон. Без
    него пишет напрямую, и всё равно `INSERT OR IGNORE`: настоящий узел
    заглушкой не перезаписывается никогда.

    Возвращает числа для отчёта и покрытия: сколько узлов создано, сколько
    адресов осталось без узла и почему.
    """
    items = missing_targets(conn, rev_id)
    rows = _stub_rows(conn, rev_id, items)
    created = 0
    if rows:
        if add_nodes is not None:
            created = int(add_nodes(rows) or 0)
        else:
            sql = (
                "INSERT OR IGNORE INTO node(rev_id, node_id, kind, name, fqn, path,"
                " start_line, end_line, signature, props, layer)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            for start in range(0, len(rows), batch):
                conn.executemany(sql, rows[start : start + batch])
            conn.commit()
            created = len(rows)
    left = audit(conn, rev_id)
    by_prefix: dict[str, int] = {}
    for row in rows:
        prefix = placeholder_prefix(row[1]) or "(без префикса)"
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
    return {
        "created": created,
        "targets": len(items),
        "created_by_prefix": dict(sorted(by_prefix.items(), key=lambda kv: (-kv[1], kv[0]))),
        "dangling_after": left["dangling_edges"],
        "left_without_placeholder": len(left["non_placeholder"]),
    }


def describe_note(info: Mapping[str, Any]) -> str:
    """Строка для журнала индексации и заметок прогона."""
    created = int(info.get("created") or 0)
    dangling = int(info.get("dangling_after") or 0)
    by_prefix = info.get("created_by_prefix") or {}
    text = f"целостность ссылок: узлов-заглушек создано {created}"
    if by_prefix:
        text += " (" + ", ".join(f"{k}{v}" for k, v in by_prefix.items()) + ")"
    if dangling:
        text += (f"; адресов без узла осталось {dangling} — это дефект, "
                 "а не внешняя ссылка")
    else:
        text += "; рёбер в никуда 0"
    return text
