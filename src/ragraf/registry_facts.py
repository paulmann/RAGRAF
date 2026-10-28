"""Состояние реестра разработок и журнал его прежних состояний.

Зачем отдельный модуль. «Сколько корпусов в реестре и сколько из них
проиндексировано» спрашивают четыре потребителя: чекер
(`tools/Invoke-ProjectDoctrine.ps1`, Test-DocumentedNumbers), ворота публикации
(`tools/release/gate_release.py`), генератор строки реестра
(`tools/registry_line.py`) и тест цикла качества (`tests/test_quality_loop.py`).
Пока правило счёта живёт в каждом из них, они расходятся — это уже измерялось
на числах поверхности продукта.

Второе, что здесь живёт, — ЖУРНАЛ ПРЕЖНИХ СОСТОЯНИЙ. Без него нельзя отличить
две разные вещи, которые до 28.10.2026 считались одной:

    документ ОТСТАЛ — названный состав реестра был верен на одной из прошлых
                      записей; это устаревший замер, а не ложь;
    документ ВРЁТ  — такого состава не было никогда.

Журнал append-only (`catalog/registry_history.jsonl`, по строке JSON на
запись) и пополняется ТОЛЬКО при изменении состава: `updated_at` реестра
двигается при индексации любого корпуса, а состав меняется редко, поэтому
журнал не растёт на каждый прогон. Ограничение длины — `MAX_REGISTRY_HISTORY`:
при переполнении остаются последние записи, и это единственная разрешённая
потеря — старая запись состава важнее читаемости файла только до известного
предела.

Журнал лежит рядом с `catalog.json`, вне корпуса индексации (`**/catalog/**`
исключён в `config/ragraf.yaml`), поэтому его рост НЕ сдвигает контрольную
сумму корпуса. Это условие, а не деталь: файл, который меняется при каждой
индексации, заставил бы сторожа переиндексировать бесконечно.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Имя журнала прежних состояний реестра. Лежит рядом с `catalog.json`.
REGISTRY_HISTORY_NAME = "registry_history.jsonl"

#: Предел длины журнала. Прежние состояния нужны, чтобы отличить отставание от
#: вымысла; глубже двухсот записей состав реестра уже не отличить от нынешнего
#: по существу.
MAX_REGISTRY_HISTORY = 200


def registry_state(entries: Mapping[str, Any] | None) -> dict[str, int]:
    """Сколько корпусов заведено и сколько из них проиндексировано.

    Правило «проиндексирован» — непустой `rev_id` в записи реестра: индекс
    построен, если есть ревизия. Правило живёт здесь одно на всех
    потребителей, а не переписывается в каждом.
    """
    items = list((entries or {}).values())
    indexed = sum(1 for item in items
                  if str((item or {}).get("rev_id") or "").strip())
    return {"corpora": len(items), "indexed": indexed}


def history_path(catalog_path: Path | str) -> Path:
    """Путь журнала состояний для файла реестра."""
    return Path(catalog_path).with_name(REGISTRY_HISTORY_NAME)


def read_history(catalog_path: Path | str) -> list[dict[str, Any]]:
    """Журнал прежних состояний, от старых записей к новым.

    Нечитаемая строка пропускается, а не рвёт чтение: журнал append-only, и
    оборванная запись (прерванный прогон) не должна делать реестр нечитаемым.
    """
    path = history_path(catalog_path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def record_state(catalog_path: Path | str, state: dict[str, int],
                 stamp: str) -> dict[str, Any] | None:
    """Дописать состояние в журнал, если состав изменился.

    Возвращает добавленную запись либо `None`, когда состав тот же: повторная
    запись того же состава ничего не добавляет к различению «отстал/врёт»,
    а только растит файл.
    """
    path = history_path(catalog_path)
    rows = read_history(catalog_path)
    last = rows[-1] if rows else None
    if last is not None:
        same = (int(last.get("corpora") or 0), int(last.get("indexed") or 0))
        if same == (int(state["corpora"]), int(state["indexed"])):
            return None

    record = {"recorded_at": stamp,
              "corpora": int(state["corpora"]),
              "indexed": int(state["indexed"])}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    kept = rows + [record]
    if len(kept) > MAX_REGISTRY_HISTORY:
        kept = kept[-MAX_REGISTRY_HISTORY:]
        body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in kept)
        path.write_text(body, encoding="utf-8", newline="\n")
    return record


def normalize_state(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Записи журнала с числами и датой в одном виде — для сравнения."""
    result: list[dict[str, Any]] = []
    for item in records:
        result.append({"recorded_at": str(item.get("recorded_at") or ""),
                       "corpora": int(item.get("corpora") or 0),
                       "indexed": int(item.get("indexed") or 0)})
    return result
