"""Контур качества индекса: набор проверок, запись прогона, разбор расхождений.

Зачем отдельный модуль, а не ещё одна операция в фасаде. Возможность
`quality-loop` объявлена в данных слоя изданий (`src/ragraf/edition_baseline.json`)
двумя операциями: `quality_suite` и `quality_diff`. Пока кода не было, она честно
попадала в раздел `missing`. Если бы логика легла в фасад, проверять её можно было
бы только через фасад, то есть с лицензией, с открытым корпусом и с журналом
доступа — а сравнивать два прогона надо уметь и на копии журнала в песочнице.
Поэтому здесь только данные и вычисления, а обёртки — в `facade.py`.

Что проверяется и чего НЕ проверяется. Набор смотрит ПОКАЗАТЕЛИ ИНДЕКСА:
размер чанков, обрезанные сигнатуры, доступность вектора, честность режима
`capability`, долю неполного разбора. Конвенции проекта (BOM, доктрина, числа в
документах) — дело чекера `tools/Invoke-ProjectDoctrine.ps1`; дублировать его
здесь значило бы получить вторую копию, которая расходится с первой.

Детерминизм — обязательное свойство, а не удобство. Два прогона на неизменённом
индексе обязаны дать ОДИН И ТОТ ЖЕ ответ, иначе сравнивать прогоны бессмысленно:
любое «расхождение» можно будет списать на шум. Отсюда три правила:

  * в измерении нет времени, длительности и случайных значений;
  * доли считаются ЦЕЛЫМИ промилле, а не числами с плавающей точкой;
  * порядок проверок задан закрытым списком `CHECK_IDS`, а не порядком словаря.

Что попадает в журнал. Только числа и имена проверок: ни строк кода, ни путей
файлов, ни содержимого чанков. Прогон качества не имеет права становиться вторым
индексом и не должен хранить то, что хранит индекс.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

# Предел размера чанка в токенах. Одно значение на весь продукт: прежде оно
# стояло числом внутри запроса `facade._quality` и внутри ответа того же метода,
# то есть в двух местах, которые могли разойтись молча.
CHUNK_BUDGET_TOKENS = 8000

# Доля неполного разбора, выше которой набор говорит о размытом индексе.
# Промилле, а не проценты: целое число не зависит от порядка сложения.
PARSE_PARTIAL_WARN_PROMILLE = 200

# Закрытый список проверок. Порядок — часть ответа: он же порядок в `checks`.
CHECKS: tuple[tuple[str, str], ...] = (
    ("chunks_over_budget", "чанки сверх предела размера"),
    ("signature_truncated", "обрезанные сигнатуры функций"),
    ("vector_available", "доступность векторного поиска"),
    ("capability_honest", "честность режима capability"),
    ("parse_partial", "доля неполного разбора файлов"),
)
CHECK_IDS: tuple[str, ...] = tuple(item[0] for item in CHECKS)

# Закрытый список состояний проверки. `unknown` — не «всё хорошо», а «измерить
# нечем»: причина называется, и это отличает честную деградацию от зелени.
STATUSES: tuple[str, ...] = ("ok", "warn", "problem", "unknown")

# Предел, после которого ответ напоминает: автоматической ротации нет.
QUALITY_RUN_SOFT_LIMIT = 500

# Доля прореживания журнала: нужна prune_keep_default, а он —
# базовой операции index_status.
PRUNE_KEEP_SHARE = 25

# Ключи записи прогона: по ним проверяется, что в журнал не попали строки кода.
RUN_FIELDS: tuple[str, ...] = (
    "id", "ts", "rev", "capability", "digest", "label", "findings", "problems",
)

QA_DDL = f"""
CREATE TABLE IF NOT EXISTS quality_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  rev_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  project TEXT NOT NULL,
  checks INTEGER NOT NULL,
  findings INTEGER NOT NULL,
  problems INTEGER NOT NULL,
  label TEXT,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_rev ON quality_run(rev_id, id);
"""


class QualityRefusal(Exception):
    """Прогон невозможен по названной причине. Не «нет данных вообще»."""


def canonical(payload: Any) -> str:
    """Каноническая запись для сравнения и хэша: без порядка словаря и пробелов."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(payload: Any) -> str:
    """Отпечаток измерения. Два одинаковых прогона дают одинаковый отпечаток."""
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Источники
# ---------------------------------------------------------------------------


@dataclass
class Sources:
    """Откуда берутся числа. Ровно три хранилища, все — только на чтение.

    Передаются по отдельности, а не «фасадом»: иначе проверку контура можно было
    бы прогнать только с живым фасадом, то есть с лицензией и с открытым
    корпусом, и песочница с копией журнала стала бы невозможна.
    """

    control: Any           # ControlStore (журнал: .conn, .cfg, .readonly)
    graph: Any             # соединение графа (store.connect)
    lexvec: Any            # LexVecStore (.conn, .vec_ok)

    @classmethod
    def from_facade(cls, facade: Any) -> "Sources":
        return cls(control=facade.control, graph=facade.graph, lexvec=facade.lexvec)


def _scalar(conn: Any, sql: str, params: tuple = (), default: Any = 0) -> Any:
    """Одно значение из запроса. Отсутствие данных — default, а не исключение."""
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return default
    if row is None:
        return default
    value = row[0] if not isinstance(row, sqlite3.Row) else row[0]
    return default if value is None else value


def _check(
    check_id: str,
    status: str,
    *,
    measured: Any = None,
    unit: str = "",
    threshold: Any = None,
    reason: str = "",
) -> dict[str, Any]:
    title = dict(CHECKS)[check_id]
    if status not in STATUSES:  # закрытый список: опечатка не создаёт состояние
        raise QualityRefusal(f"недопустимое состояние проверки {status!r} у «{check_id}»")
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "measured": measured,
        "unit": unit,
        "threshold": threshold,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Показатели: ОДИН расчёт на весь продукт
# ---------------------------------------------------------------------------


def _pick(row: Any, names: tuple[str, ...], index: int, default: Any = 0) -> Any:
    """Значение из строки по имени или по номеру.

    Вид строки зависит от соединения: в фасаде это `sqlite3.Row`, в песочнице
    проверок может быть обычный кортеж. Обращение по имени к кортежу падает,
    поэтому оба вида поддерживаются здесь, а не в каждом запросе.
    """
    if row is None:
        return default
    for name in names:
        try:
            value = row[name]
        except (IndexError, KeyError, TypeError):
            continue
        return default if value is None else value
    try:
        value = row[index]
    except (IndexError, KeyError, TypeError):
        return default
    return default if value is None else value


def _count(conn: Any, sql: str, params: tuple = ()) -> int:
    """Счётчик запросом. Отсутствие таблицы — исключение, а не тихий ноль."""
    row = conn.execute(sql, params).fetchone()
    return int(_pick(row, ("c",), 0, 0))


def measures(sources: Sources, *, rev: str | None, samples: int = 64) -> dict[str, Any]:
    """Показатели качества индекса. Единственное место, где они считаются.

    Зачем одна функция, а не два похожих запроса. Прежде показатели считались
    дважды: здесь — для проверок набора, и в `facade._quality` — для ответа
    `index_status`. Числа совпадали, но совпадали ПО СОВПАДЕНИЮ: предел размера
    чанка был записан трижды (`quality.CHUNK_BUDGET_TOKENS` и дважды числом
    внутри `facade._quality`). Правка одного места оставляла второе врать молча —
    ровно тот класс дефекта, из-за которого в проекте уже появлялись «одно число
    в трёх редакциях».

    Границы честные: недоступное хранилище даёт блок с непустым `error`, а не
    исключение; отсутствие данных даёт нули, и состояние проверки при этом
    остаётся `unknown`, а не «всё хорошо».
    """
    rev_id = str(rev or "").strip()
    return {
        "rev": rev_id,
        "budget_tokens": CHUNK_BUDGET_TOKENS,
        "chunks": _measure_chunks(sources, rev_id),
        "signatures": _measure_signatures(sources, rev_id),
        "vectors": _measure_vectors(sources, samples),
    }


def _measure_chunks(sources: Sources, rev: str) -> dict[str, Any]:
    """Размеры чанков: число, среднее, максимум, превышения предела.

    Мера — `chunk_map.tokens`, а не длина токенизированной колонки `ft_chunk`:
    вторая в несколько раз длиннее исходника и давала «чанки на 44 тысячи
    токенов» при пределе 8 000 (измеренный дефект). `chunk_map.tokens` — оценка
    размера самого чанка, и именно она сравнивается с пределом при чанкинге.
    """
    out: dict[str, Any] = {
        "total": 0,
        "max_tokens": 0,
        "avg_tokens": 0,
        "over_budget": 0,
        "files_without_symbols": 0,
        "budget_tokens": CHUNK_BUDGET_TOKENS,
        "measure": "chunk_map.tokens: оценка размера чанка при чанкинге",
        "error": "",
    }
    try:
        row = sources.lexvec.conn.execute(
            """SELECT COUNT(*) AS c,
                      COALESCE(MAX(tokens), 0) AS max_tok,
                      COALESCE(SUM(CASE WHEN tokens > ? THEN 1 ELSE 0 END), 0) AS over,
                      COALESCE(AVG(tokens), 0) AS avg_tok
                 FROM chunk_map WHERE rev_id = ?""",
            (CHUNK_BUDGET_TOKENS, rev),
        ).fetchone()
        files = sources.lexvec.conn.execute(
            """SELECT COUNT(DISTINCT path) AS c FROM chunk_map
                WHERE rev_id = ? AND source_kind = 'file'""",
            (rev,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — причина называется, а не глотается
        out["error"] = type(exc).__name__
        return out
    out["total"] = int(_pick(row, ("c", "total"), 0))
    out["max_tokens"] = int(_pick(row, ("max_tok",), 1))
    out["over_budget"] = int(_pick(row, ("over",), 2))
    out["avg_tokens"] = int(_pick(row, ("avg_tok",), 3))
    out["files_without_symbols"] = int(_pick(files, ("c",), 0))
    return out


def _measure_signatures(sources: Sources, rev: str) -> dict[str, Any]:
    """Сигнатуры функций и docblock'и: без них контракт параметров недостижим."""
    out: dict[str, Any] = {
        "functions": 0,
        "truncated": 0,
        "with_untyped_params": 0,
        "docblocks": 0,
        "note": "",
        "error": "",
    }
    try:
        out["functions"] = _count(
            sources.graph,
            "SELECT COUNT(*) AS c FROM node WHERE rev_id = ? AND kind IN ('function','method')",
            (rev,))
        out["truncated"] = _count(
            sources.graph,
            """SELECT COUNT(*) AS c FROM node WHERE rev_id = ?
                 AND kind IN ('function','method')
                 AND props LIKE '%' || char(34) || 'signature_truncated' || char(34) || ': true%'""",
            (rev,))
        out["with_untyped_params"] = _count(
            sources.graph,
            """SELECT COUNT(*) AS c FROM node WHERE rev_id = ?
                 AND kind IN ('function','method')
                 AND props LIKE '%' || char(34) || 'type' || char(34) || ': null%'""",
            (rev,))
        out["docblocks"] = _count(
            sources.graph,
            "SELECT COUNT(*) AS c FROM text_chunk WHERE rev_id = ? AND text_kind = 'docblock'",
            (rev,))
    except Exception as exc:  # noqa: BLE001 — причина называется
        out["error"] = type(exc).__name__
        return out
    if out["truncated"]:
        out["note"] = ("есть обрезанные сигнатуры: текстовая форма теряет параметры, "
                       "используйте signature_params")
    return out


def _measure_vectors(sources: Sources, samples: int = 64) -> dict[str, Any]:
    """Векторы: доступность, число, нормы p50/max, признак нормированности.

    Ненормированные векторы — НЕ проблема: перевод расстояния в оценку в lexvec
    учитывает нормы (`q_norm`, `c_norm`). Первая версия проверки помечала такие
    векторы неисправностью и показывала «здоровье: нет» на обоих рабочих
    корпусах, где оценки поиска были верные. Поэтому признак нормированности
    выдаётся справкой, а `problem` из него не делается.
    """
    out: dict[str, Any] = {
        "available": False,
        "sampled": 0,
        "norm_p50": None,
        "norm_max": None,
        "normalized": False,
        "embedded": 0,
        "note": "",
        "error": "",
    }
    available = bool(getattr(sources.lexvec, "vec_ok", False))
    out["available"] = available
    if not available:
        return out
    try:
        import math
        import struct

        rows = sources.lexvec.conn.execute(
            "SELECT embedding FROM vec_chunk LIMIT ?", (int(samples),)).fetchall()
        norms: list[float] = []
        for row in rows:
            blob = _pick(row, ("embedding",), 0, None)
            if blob is None:
                continue
            if isinstance(blob, memoryview):
                blob = blob.tobytes()
            n = len(blob) // 4
            if n <= 0:
                continue
            vals = struct.unpack(f"<{n}f", blob[: n * 4])
            norms.append(math.sqrt(sum(v * v for v in vals)))
        norms.sort()
        p50 = norms[len(norms) // 2] if norms else None
        out["sampled"] = len(norms)
        out["norm_p50"] = round(p50, 4) if p50 is not None else None
        out["norm_max"] = round(norms[-1], 4) if norms else None
        out["normalized"] = bool(norms) and p50 is not None and abs(p50 - 1.0) < 0.05
        if p50 is not None and abs(p50 - 1.0) >= 0.05:
            out["note"] = ("нормы далеки от 1: перевод расстояния в оценку обязан "
                           "учитывать нормы")
        out["embedded"] = _count(sources.lexvec.conn, "SELECT COUNT(*) AS c FROM vec_chunk")
    except Exception as exc:  # noqa: BLE001 — причина называется
        out["error"] = type(exc).__name__
        out["available"] = False
        return out
    return out


# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------


def _chunks(block: dict[str, Any]) -> dict[str, Any]:
    """Проверка размера чанков. Числа берутся из общего расчёта, не из своего."""
    budget = int(block.get("budget_tokens") or CHUNK_BUDGET_TOKENS)
    if block.get("error"):
        return _check("chunks_over_budget", "unknown", measured=None, unit="чанков",
                      threshold=budget,
                      reason=f"размеры чанков не прочитаны ({block['error']}): измерить нечем")
    total = int(block.get("total") or 0)
    if total == 0:
        return _check("chunks_over_budget", "unknown", measured=None, unit="чанков",
                      threshold=budget,
                      reason="в индексе нет чанков этой ревизии: измерить размер нечем")
    over = int(block.get("over_budget") or 0)
    max_tokens = int(block.get("max_tokens") or 0)
    if over:
        return _check("chunks_over_budget", "problem", measured=over, unit="чанков",
                      threshold=budget,
                      reason=(f"{over} из {total} чанков длиннее {budget} токенов "
                              f"(самый длинный — {max_tokens}): выдача размывается"))
    return _check("chunks_over_budget", "ok", measured=0, unit="чанков", threshold=budget,
                  reason=f"самый длинный чанк — {max_tokens} токенов при пределе {budget}")


def _signatures(block: dict[str, Any]) -> dict[str, Any]:
    """Проверка обрезанных сигнатур. Числа — из общего расчёта."""
    if block.get("error"):
        return _check("signature_truncated", "unknown", measured=None, unit="сигнатур",
                      threshold=0,
                      reason=f"сигнатуры не прочитаны ({block['error']}): измерить нечем")
    functions = int(block.get("functions") or 0)
    if functions == 0:
        return _check("signature_truncated", "unknown", measured=None, unit="сигнатур",
                      threshold=0,
                      reason="в графе нет функций этой ревизии: обрезанных сигнатур быть не может, "
                             "но и судить не о чем")
    truncated = int(block.get("truncated") or 0)
    if truncated:
        return _check("signature_truncated", "problem", measured=truncated, unit="сигнатур",
                      threshold=0,
                      reason=(f"{truncated} из {functions} сигнатур обрезаны: контракт "
                              "параметров недостижим, и ответ об этом не предупреждает"))
    return _check("signature_truncated", "ok", measured=0, unit="сигнатур", threshold=0,
                  reason=f"ни одна из {functions} сигнатур не обрезана")


def _vectors(block: dict[str, Any]) -> dict[str, Any]:
    """Проверка доступности вектора. Числа — из общего расчёта."""
    if block.get("error"):
        return _check("vector_available", "warn", measured=0, unit="векторов", threshold=1,
                      reason=(f"векторы не прочитаны ({block['error']}): считать доступность "
                              "нечем — поиск идёт только лексическим путём"))
    if not block.get("available"):
        return _check("vector_available", "warn", measured=0, unit="векторов", threshold=1,
                      reason="векторный поиск недоступен: расширение не загрузилось — "
                             "поиск идёт только лексическим путём")
    embedded = int(block.get("embedded") or 0)
    if embedded == 0:
        return _check("vector_available", "warn", measured=0, unit="векторов", threshold=1,
                      reason="расширение доступно, но векторов в индексе ноль: индекс собран "
                             "без векторного слоя")
    return _check("vector_available", "ok", measured=embedded, unit="векторов", threshold=1,
                  reason=f"векторов в индексе: {embedded}")


def _capability(sources: Sources, rev: str, capability: str) -> dict[str, Any]:
    """Режим `full` обязан опираться на рёбра от свойств кода, а не на факт запуска.

    Смысл `full` записан в самом коде (`src/ragraf/indexer.py`, ветка обходного
    пути): это «в графе есть межпроцедурные рёбра, пришедшие от графа свойств
    кода». Поэтому проверка считает именно такие рёбра, а не смотрит на слово
    «full» в паспорте ревизии: слово проверять бессмысленно, оно и есть предмет
    проверки.
    """
    claimed = str(capability or "").strip()
    if claimed not in ("full", "partial", "degraded"):
        return _check("capability_honest", "unknown", measured=claimed or None,
                      unit="режим", threshold="full",
                      reason=f"режим {claimed or 'не указан'} вне закрытого списка: судить о "
                             "честности нечем")
    try:
        from .cpg import CPG_SOURCE
    except Exception:  # noqa: BLE001 — причина называется, а не глотается
        return _check("capability_honest", "unknown", measured=claimed, unit="режим",
                      threshold="full",
                      reason="имя источника графа свойств кода недоступно: проверить режим нечем")

    from_cpg = int(_scalar(
        sources.graph,
        "SELECT COUNT(*) FROM edge WHERE rev_id = ? AND sources LIKE ?",
        (rev, f"%{CPG_SOURCE}%")))
    if claimed == "full":
        if from_cpg > 0:
            return _check("capability_honest", "ok", measured=from_cpg, unit="рёбер",
                          threshold=1,
                          reason=f"режим full подтверждён: {from_cpg} рёбер пришли от графа "
                                 f"свойств кода ({CPG_SOURCE})")
        return _check("capability_honest", "problem", measured=0, unit="рёбер", threshold=1,
                      reason=("режим full завышен: рёбер от свойств кода нет, операции среза "
                              "недоступны, а клиенту обещано обратное"))
    return _check("capability_honest", "warn", measured=from_cpg, unit="рёбер", threshold=1,
                  reason=(f"режим {claimed}: свойства кода неполные, операции среза честно "
                          f"помечены недоступными (рёбер от свойств кода: {from_cpg})"))


def _parse(sources: Sources, rev: str) -> dict[str, Any]:
    parsed = int(_scalar(
        sources.graph,
        "SELECT COUNT(*) FROM file WHERE rev_id = ? AND parse_status IN ('ok','partial','failed')",
        (rev,)))
    if parsed == 0:
        return _check("parse_partial", "unknown", measured=None, unit="промилле",
                      threshold=PARSE_PARTIAL_WARN_PROMILLE,
                      reason="файлов с разбором деревом в индексе нет: доля не считается")
    partial = int(_scalar(
        sources.graph,
        "SELECT COUNT(*) FROM file WHERE rev_id = ? AND parse_status IN ('partial','failed')",
        (rev,)))
    promille = partial * 1000 // parsed
    if promille > PARSE_PARTIAL_WARN_PROMILLE:
        return _check("parse_partial", "warn", measured=promille, unit="промилле",
                      threshold=PARSE_PARTIAL_WARN_PROMILLE,
                      reason=(f"неполный разбор у {partial} из {parsed} файлов "
                              f"({promille}‰): структура части файлов в индексе неполная, и это "
                              "видно только здесь"))
    return _check("parse_partial", "ok", measured=promille, unit="промилле",
                  threshold=PARSE_PARTIAL_WARN_PROMILLE,
                  reason=f"неполный разбор у {partial} из {parsed} файлов ({promille}‰)")


# ---------------------------------------------------------------------------
# Набор и прогон
# ---------------------------------------------------------------------------


def prune_keep_default(limit: int | None = None) -> int:
    """Сколько последних прогонов оставить, если `keep` не указан.

    Считается от предела каждый раз, а не берётся из константы на импорте:
    иначе правка `QUALITY_RUN_SOFT_LIMIT` не меняла бы умолчание прореживания —
    ровно тот дефект, из-за которого два числа и были связаны только памятью.
    """
    bound = int(QUALITY_RUN_SOFT_LIMIT if limit is None else limit)
    return max(1, bound // PRUNE_KEEP_SHARE)


def journal_stats(sources: Sources) -> dict[str, Any]:
    """Сколько прогонов в журнале и перевалил ли он за предел.

    Зачем это наблюдаемое. Предел `QUALITY_RUN_SOFT_LIMIT` был объявлен, но его
    не читал ни один потребитель: замер 25.10.2026 (`rg QUALITY_RUN_SOFT_LIMIT`)
    давал объявление и два упоминания в docstring. Следствие: администратор
    узнавал, что журнал перевалил за предел, только позвав прореживание вручную,
    то есть уже зная, зачем пришёл.

    Границы честные. Недоступная таблица даёт `error` с именем причины и
    `runs: 0`, а не «всё в порядке»: ноль при ошибке и пустой журнал — разные
    состояния, и различие названо полем, а не оставлено читателю на догадку.
    """
    limit = int(QUALITY_RUN_SOFT_LIMIT)
    out: dict[str, Any] = {
        "runs": 0,
        "soft_limit": limit,
        "keep_default": prune_keep_default(limit),
        "over_limit": False,
        "excess": 0,
        "note": "",
    }
    try:
        out["runs"] = int(_count(sources.control.conn,
                                 "SELECT COUNT(*) AS c FROM quality_run"))
    except sqlite3.Error as exc:
        out["error"] = type(exc).__name__
        out["note"] = "журнал прогонов не прочитан: число прогонов неизвестно"
        return out
    excess = max(0, int(out["runs"]) - limit)
    out["excess"] = excess
    out["over_limit"] = excess > 0
    if excess > 0:
        out["note"] = (f"журнал выше предела на {excess} прогонов: quality_prune "
                       f"оставит {out['keep_default']}")
    return out


def journal_path(sources: Sources) -> str:
    """Путь файла журнала. Нужен, чтобы сделать копию ДО удаления.

    Берётся у самого SQLite (`PRAGMA database_list`), а не из настроек: только
    так путь совпадает с тем файлом, который открыт на запись. Журнал в памяти
    (или без файла) копировать нечем — это отказ с причиной, а не «пропустим
    копию, удаление и так небольшое».
    """
    try:
        rows = sources.control.conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error as exc:
        raise QualityRefusal(f"путь журнала не прочитан: {type(exc).__name__}") from exc
    for row in rows:
        name = str(_pick(row, ("name",), 1, ""))
        file = str(_pick(row, ("file",), 2, "") or "")
        if name == "main" and file.strip():
            return file
    raise QualityRefusal("журнал открыт без файла на диске: копию сделать нечем, "
                         "а удалять без копии запрещено")


