"""Экспорт и удаление локальной аналитики — без сети.

Зачем отдельный модуль
    `feedback.py` готовит человекочитаемый отчёт, а здесь — машинный срез и
    удаление. Промт владельца требует три операции: посмотреть, выгрузить,
    удалить. Отправки нет ни в одной: локальный инструмент не должен сам
    открывать сеть.

Что попадает в экспорт
    Те же агрегаты, что в отчёте: вызовы, факты, доля проверяемых, символы,
    оценка токенов и строк, разбивка по операциям. Путей, имён проектов,
    запросов и содержимого файлов там нет — их нет и в журнале.

Удаление
    Перед удалением пишется резервная копия в `catalog/analytics/`. Удаление
    без копии запрещено: журнал — единственный источник измерений, и «очистить
    для чистого замера» не должно означать «потерять историю».
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import util

SCHEMA = "ragraf.usage.v1"


def _dir(base: Path | None = None) -> Path:
    from .sources import CATALOG_DIR

    target = Path(base) if base else (CATALOG_DIR / "analytics")
    target.mkdir(parents=True, exist_ok=True)
    return target


def export_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Привести отчёт к схеме `ragraf.usage.v1` без лишних полей."""
    return {
        "schema": SCHEMA,
        "generated_at": payload.get("generated_at") or util.now_iso(),
        "app_version": payload.get("app_version") or "",
        "os": payload.get("os") or "",
        "arch": payload.get("arch") or "",
        "period_days": payload.get("period_days"),
        "sources": {
            "sessions": payload.get("sessions", 0),
            "calls": payload.get("calls", 0),
        },
        "queries": {
            "total": payload.get("calls", 0),
            "hit_rate": payload.get("hit_rate", 0.0),
            "saved_calls": payload.get("saved_calls", 0),
            "avg_ms": payload.get("avg_ms", 0.0),
            "by_op": payload.get("by_op") or [],
        },
        "value": {
            "estimated_tokens_saved": payload.get("est_tokens", 0),
            "estimated_lines_not_read": payload.get("est_lines_not_read", 0),
            "facts": payload.get("facts", 0),
            "facts_with_span": payload.get("facts_with_span", 0),
            "verifiable_ratio": payload.get("verifiable_ratio", 0.0),
        },
        "errors": {"total": payload.get("errors", 0)},
        "excluded": payload.get("excluded") or [],
    }


def export(cfg, *, days: int = 30, out: str | Path | None = None) -> dict[str, Any]:
    """Записать машинный срез в файл. Сеть не используется."""
    from . import feedback

    raw = feedback.collect(cfg, days=days)
    payload = export_payload(raw)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        path = _dir() / f"ragraf-usage-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    return {"path": util.to_posix(path), "payload": payload,
            "note": "файл локальный; ничего не отправлено"}


def delete_rows(conn, *, backup_dir: Path | None = None) -> dict[str, Any]:
    """Удалить журнал, предварительно сохранив его в файл.

    Принимает соединение, а не конфиг: так операция проверяема на временной
    базе и не требует собирать корпус.
    """
    calls = [dict(row) for row in conn.execute("SELECT * FROM usage_call").fetchall()]
    sessions = [dict(row) for row in conn.execute("SELECT * FROM usage_session").fetchall()]
    target = _dir(backup_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    backup = target / f"analytics-backup-{stamp}.json"
    backup.write_text(
        json.dumps({"schema": "ragraf.analytics.backup.v1",
                    "exported_at": util.now_iso(),
                    "usage_call": calls,
                    "usage_session": sessions},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    if not backup.exists() or backup.stat().st_size == 0:
        raise RuntimeError("резервная копия не создана: удаление отменено")
    conn.execute("DELETE FROM usage_call")
    conn.execute("DELETE FROM usage_session")
    conn.commit()
    return {
        "deleted_calls": len(calls),
        "deleted_sessions": len(sessions),
        "backup": util.to_posix(backup),
        "note": "журнал очищен; копия сохранена локально",
    }


def delete(cfg, *, backup_dir: Path | None = None) -> dict[str, Any]:
    """Удалить локальную аналитику корпуса с резервной копией."""
    from .revisions import ControlStore

    control = ControlStore(cfg)
    try:
        return delete_rows(control.conn, backup_dir=backup_dir)
    finally:
        try:
            control.conn.close()
        except Exception:  # noqa: BLE001
            pass
