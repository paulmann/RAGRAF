"""Отчёт о пользе и локальный «field report» — без сети и без путей.

Зачем отдельный модуль
    Телеметрия уже считает вызовы, факты, символы и оценку токенов
    (`src/ragraf/telemetry.py`). Но отчёт, который можно показать человеку или
    приложить к обратной связи, должен собираться отдельно: у него другой
    период, другой формат и жёсткий фильтр приватности.

Что попадает в отчёт
    Только агрегаты: число вызовов и ошибок, факты, доля фактов со ссылкой
    `path:line`, объём ответов, оценка токенов и строк. Ни путей, ни имён
    проектов, ни запросов, ни фрагментов кода — их в журнале и нет
    (телеметрия хранит только хеш аргумента), но фильтр всё равно проверяется
    тестом: отчёт не должен стать каналом утечки.

Чего модуль НЕ делает
    * не отправляет ничего по сети;
    * не создаёт fork, ветку или PR: это действие владельца;
    * не включает в отчёт содержимое файлов.
"""

from __future__ import annotations

import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import util

REPORT_DIR_NAME = "field_reports"
SCHEMA = "ragraf.field_report.v1"


def collect(cfg, *, days: int = 30) -> dict[str, Any]:
    """Собрать агрегаты за период. Только числа, без путей и запросов."""
    days = max(1, min(int(days), 3650))
    since = time.time() - days * 86400.0
    from .revisions import ControlStore
    from .telemetry import CHARS_PER_LINE, CHARS_PER_UNIT, Telemetry

    control = ControlStore(cfg)
    try:
        reader = Telemetry.for_reading(control)
        conn = control.conn
        row = conn.execute(
            """SELECT COUNT(*) AS calls,
                      SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END) AS errors,
                      SUM(COALESCE(fact_count, 0)) AS facts,
                      SUM(COALESCE(facts_with_span, 0)) AS with_span,
                      SUM(COALESCE(result_chars, 0)) AS chars,
                      SUM(CASE WHEN replaces = 1 THEN COALESCE(result_chars, 0) ELSE 0 END) AS replacing,
                      SUM(CASE WHEN COALESCE(fact_count, 0) > 0 THEN 1 ELSE 0 END) AS calls_with_facts,
                      SUM(CASE WHEN replaces = 1 AND COALESCE(fact_count, 0) > 0 THEN 1 ELSE 0 END) AS saved_calls,
                      ROUND(AVG(duration_ms), 2) AS avg_ms
                 FROM usage_call WHERE ts >= ?""",
            (since,),
        ).fetchone()
        by_op = [
            dict(r)
            for r in conn.execute(
                """SELECT op, COUNT(*) AS calls,
                          SUM(COALESCE(fact_count, 0)) AS facts,
                          ROUND(AVG(duration_ms), 2) AS avg_ms
                     FROM usage_call WHERE ts >= ?
                    GROUP BY op ORDER BY calls DESC LIMIT 15""",
                (since,),
            ).fetchall()
        ]
        sessions = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_session WHERE started_at >= ?", (since,)
        ).fetchone()["c"]
    finally:
        try:
            control.conn.close()
        except Exception:  # noqa: BLE001
            pass

    calls = int(row["calls"] or 0)
    errors = int(row["errors"] or 0)
    facts = int(row["facts"] or 0)
    with_span = int(row["with_span"] or 0)
    chars = int(row["chars"] or 0)
    replacing = int(row["replacing"] or 0)
    return {
        "schema": SCHEMA,
        "generated_at": util.now_iso(),
        "period_days": days,
        "app_version": _version(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "sessions": int(sessions or 0),
        "calls": calls,
        "errors": errors,
        "facts": facts,
        "facts_with_span": with_span,
        "verifiable_ratio": round(with_span / facts, 4) if facts else 0.0,
        "chars": chars,
        "est_tokens": int(chars / CHARS_PER_UNIT),
        "replacing_chars": replacing,
        "est_lines_not_read": int(replacing / CHARS_PER_LINE),
        "hit_rate": round(int(row["calls_with_facts"] or 0) / calls, 4) if calls else 0.0,
        "saved_calls": int(row["saved_calls"] or 0),
        "avg_ms": float(row["avg_ms"] or 0.0),
        "by_op": by_op,
        # Явно перечислено, чего в отчёте нет: читатель должен видеть границу,
        # а не догадываться, что «наверное, путей нет».
        "excluded": ["пути", "имена проектов", "запросы", "фрагменты кода",
                     "значения секретов", "содержимое документов"],
    }


def render(payload: dict[str, Any]) -> str:
    """Markdown-черновик отчёта. Редактируется человеком перед отправкой."""
    lines = [
        "# RAGRAF Field Report",
        "",
        f"- версия RAGRAF: {payload.get('app_version') or '—'}",
        f"- ОС: {payload.get('os') or '—'} ({payload.get('arch') or '—'})",
        f"- период: последние {payload.get('period_days')} дн.",
        f"- сессий: {payload.get('sessions')}",
        f"- обращений к индексу: {payload.get('calls')}",
        f"- ошибок: {payload.get('errors')}",
        f"- фактов: {payload.get('facts')}, со ссылкой path:line — "
        f"{payload.get('facts_with_span')} ({float(payload.get('verifiable_ratio') or 0) * 100:.0f}%)",
        f"- объём ответов: {payload.get('chars')} символов "
        f"(~{payload.get('est_tokens')} токенов, оценка)",
        f"- заменили чтение файлов: {int(payload.get('est_lines_not_read') or 0)} строк (оценка)",
        f"- вызовов, заменивших чтение: {payload.get('saved_calls')}",
        f"- доля вызовов с фактами: {float(payload.get('hit_rate') or 0) * 100:.0f}%",
        "",
        "## Что работало",
        "",
        "<!-- заполните вручную: что нашли, что сэкономили -->",
        "",
        "## Что не работало",
        "",
        "<!-- заполните вручную: где индекс ошибся или не нашёл -->",
        "",
        "## Таблица операций",
        "",
        "| операция | вызовов | фактов | среднее, мс |",
        "|---|---:|---:|---:|",
    ]
    for row in payload.get("by_op") or []:
        lines.append(
            f"| {row.get('op')} | {row.get('calls')} | {row.get('facts') or 0} | "
            f"{row.get('avg_ms') or 0} |"
        )
    lines += [
        "",
        "## Границы отчёта",
        "",
        "В отчёт не входят: " + ", ".join(payload.get("excluded") or []) + ".",
        "",
        "Отчёт подготовлен локально. Ничего не отправлено: fork, ветку и PR",
        "создаёт владелец, если решит поделиться.",
        "",
    ]
    return "\n".join(lines)


def write(payload: dict[str, Any], *, directory: Path | None = None) -> Path:
    """Записать отчёт в `catalog/field_reports/` и вернуть путь."""
    from .sources import CATALOG_DIR

    target_dir = Path(directory) if directory else (CATALOG_DIR / REPORT_DIR_NAME)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = target_dir / f"feedback-{stamp}.md"
    util.write_text(path, render(payload))
    return path


def build(cfg, *, days: int = 30, directory: Path | None = None) -> dict[str, Any]:
    """Собрать, отрисовать и записать отчёт. Возвращает путь и содержимое."""
    payload = collect(cfg, days=days)
    path = write(payload, directory=directory)
    return {"path": util.to_posix(path), "payload": payload, "report": render(payload)}


def _version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return ""
