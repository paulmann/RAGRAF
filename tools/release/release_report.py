#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отчёт выпуска как артефакт поставки (этап X21).

Зачем отдельный модуль. Отчёт нужен ДВУМ сторонам: сборщику (положить файл в
поставку) и воротам (сверить файл с набором). Если бы каждая сторона считала
свой, они бы разошлись формулировками — ровно та беда, из-за которой делался X20.
Поэтому здесь одна сборка состояния из артефакта `ops_pins.json`, один рендер и
одно сравнение.

Почему состояние строится ИЗ ФАЙЛА, а не из памяти прогона: файл уезжает
клиенту и живёт дольше прогона. Отчёт обязан описывать то, что лежит рядом с
ним, а не то, что было в переменных сборщика.

Границы: время сборки, пути машины и возраст пина в отчёт НЕ попадают — отчёт
должен быть побайтово одинаковым при двух прогонах на неизменном дереве.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: Артефакт сборщика: отчёт о выпуске для человека.
REPORT_ARTIFACT = "RELEASE-REPORT.md"

#: Артефакт сборщика: описания операций и подтверждённый набор.
PINS_ARTIFACT = "ops_pins.json"


def _gate():
    """Ворота — только по требованию.

    `gate_release.py` сам импортирует сборщик и этот модуль на уровне модуля,
    поэтому обратный импорт на уровне модуля дал бы кольцо. Отложенный импорт
    разрывает кольцо и не мешает тестам подменять каталог инструментов.
    """
    import gate_release  # noqa: PLC0415 - отложенный импорт разрывает кольцо

    return gate_release


def document(tree: Path) -> dict:
    """Манифест описаний из поставки; пустой словарь — если читать нечего."""
    path = Path(tree) / PINS_ARTIFACT
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def tree_state(tree: Path, *, previous: Path | None = None,
               source: Path | None = None) -> dict:
    """Состояние выпуска по АРТЕФАКТАМ поставки (тот же вид, что у ворот).

    Ничего не решает: заполняет поля, которые умеет прочитать из файла. Решение
    выносит `gate.verdict_of` — единственная точка вердикта.
    """
    gate = _gate()
    body = document(tree)
    state: dict = {
        "version": str(body.get("version") or ""),
        "ops": str(body.get("pin_state_ops") or ""),
        "tools": str(body.get("pin_state_tools") or ""),
        "behaviour": "",
        "behaviour_note": "",
        "blocks": [],
        "warnings": [],
        "actions": [],
        "changes": {"ops_changed": [], "tools_changed": [], "samples_changed": [],
                    "history_since": {"total": 0, "authors": {}}},
    }
    behaviour, note = gate.behaviour_state(body)
    state["behaviour"] = behaviour
    state["behaviour_note"] = note
    samples = body.get("samples") or {}
    total = int(samples.get("total") or 0)
    checked = int(samples.get("checked") or 0)
    if not total:
        state["warnings"].append("пробы поведения не снимались")
    elif not checked:
        state["warnings"].append(f"пробы есть ({total}), ни одна не сверялась")
    if previous is not None and Path(previous).is_file():
        pins = Path(tree) / PINS_ARTIFACT
        names = gate.diff_names(Path(previous), pins)
        state["changes"]["ops_changed"] = names["ops_changed"]
        state["changes"]["tools_changed"] = names["tools_changed"]
        state["changes"]["samples_changed"] = gate.samples_changes(Path(previous), pins)
        if source is not None:
            state["changes"]["history_since"] = gate.history_since(Path(source),
                                                                   Path(previous))
    return state


def render(tree: Path, *, previous: Path | None = None, source: Path | None = None) -> str:
    """Markdown отчёта — тот же рендер, что у ворот (`summary_markdown`)."""
    return _gate().summary_markdown(tree_state(tree, previous=previous, source=source))


def write(tree: Path, *, previous: Path | None = None,
          source: Path | None = None) -> Path:
    """Положить отчёт в поставку. LF, без BOM, байты зависят только от набора."""
    path = Path(tree) / REPORT_ARTIFACT
    path.write_bytes(render(tree, previous=previous, source=source).encode("utf-8"))
    return path


def parse(text: str) -> dict:
    """Что отчёт заявляет о себе: версия и строка вердикта.

    Нужно для того, чтобы отказ проверки называл расхождение словами, а не
    только «байты не совпали».
    """
    version = ""
    verdict = ""
    for line in text.splitlines():
        if line.startswith("Версия: "):
            version = line.split(": ", 1)[-1].strip()
        elif line.startswith("разрешена") or line.startswith("остановлена"):
            verdict = line.strip()
    return {"version": version, "verdict": verdict}


def declared(manifest) -> bool:
    """Объявлен ли отчёт артефактом манифеста поставки."""
    return REPORT_ARTIFACT in tuple(getattr(manifest, "artifacts", ()) or ())
