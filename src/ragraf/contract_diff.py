"""Сводка изменений контракта между двумя выпусками.

Зачем. `ops_pins.json` уезжает в поставку (этап X9): в нём хеши описаний
операций и инструментов, подтверждённый набор и версия. Сравнивать два выпуска
глазами по 76 строкам — занятие, в котором пропускают главное: исчезнувший
параметр и новый необязательный выглядят одинаково — «строка изменилась».

Что считается `breaking` (клиент, живущий по старому контракту, сломается):

  * объект исчез (операции или инструмента больше нет);
  * у операции появился параметр БЕЗ умолчания, параметр исчез или переименован,
    сменился тип параметра, пропал ключ ответа;
  * у инструмента появилось обязательное свойство, свойство исчезло, сменился
    тип, сузился `enum`, `additionalProperties` стал `false`;
  * пометка безопасности ухудшилась: `readOnlyHint` снят, `destructiveHint`
    появился, `idempotentHint` снят.

Что считается `additive` (старый клиент продолжает работать):

  * объект добавлен;
  * у операции появился параметр С умолчанием, добавился ключ ответа;
  * у инструмента появилось необязательное свойство, расширился `enum`.

Что не относятcя ни к тому, ни к другому и названо отдельно (`changed`):
изменённое умолчание, другой вид сборки ответа (`returns_from`),
`openWorldHint`. Это правка смысла, а не формы: решает человек.

Границы, названные прямо:

  * сравнение идёт по ФАЙЛАМ `ops_pins.json`, то есть по тому, что уехало
    клиенту, а не по текущему дереву;
  * поведение не сравнивается: два выпуска с одинаковым описанием могут
    отвечать по-разному, и это не ловится;
  * словесные описания в контракт не входят (см. `pin._schema_form`).

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = 1

#: Закрытый список видов изменения.
CHANGE_KINDS: tuple[str, ...] = ("breaking", "additive", "changed")


class ContractDiffError(ValueError):
    """Сравнить нельзя по названной причине."""


def load_document(path: str | Path) -> dict[str, Any]:
    """Прочитать манифест описаний. Свой формат не принимается молча."""
    item = Path(path)
    if not item.is_file():
        raise ContractDiffError(f"файла нет: {item}")
    try:
        data = json.loads(item.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ContractDiffError(f"{item.name} не разобран: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractDiffError(f"{item.name}: ожидался объект, а не {type(data).__name__}")
    if not isinstance(data.get("ops"), dict):
        raise ContractDiffError(
            f"{item.name}: нет раздела ops — это не манифест описаний "
            f"(его пишет tools/release/build_release.py)")
    return data


def _details(document: dict[str, Any], key: str) -> dict[str, Any]:
    details = document.get(key)
    if isinstance(details, dict):
        return details
    return {}


def _op_params(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in detail.get("params") or []:
        if isinstance(item, dict) and item.get("name"):
            out[str(item["name"])] = item
    return out


def _flat_props(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Свойства схемы с вложенностью: путь `filter.kind` → спецификация.

    Без рекурсии правка во вложенном объекте не видна: `filter` остаётся
    `object`, а внутри уже другой тип. Именно такой случай проверяет подсадка
    в наборе.
    """
    out: dict[str, dict[str, Any]] = {}

    def walk(schema: dict[str, Any], prefix: str) -> None:
        for name, spec in sorted((schema.get("properties") or {}).items()):
            path = f"{prefix}{name}"
            if not isinstance(spec, dict):
                out[path] = {}
                continue
            out[path] = spec
            if isinstance(spec.get("properties"), dict):
                walk(spec, path + ".")

    walk(detail.get("schema") or {}, "")
    return out


def _required_paths(detail: dict[str, Any]) -> set[str]:
    """Обязательные свойства с вложенными путями."""
    out: set[str] = set()

    def walk(schema: dict[str, Any], prefix: str) -> None:
        for name in schema.get("required") or []:
            out.add(prefix + str(name))
        for name, spec in (schema.get("properties") or {}).items():
            if isinstance(spec, dict) and isinstance(spec.get("properties"), dict):
                walk(spec, prefix + str(name) + ".")

    walk(detail.get("schema") or {}, "")
    return out


def _schema_props(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Совместимое имя: плоская карта свойств (см. `_flat_props`)."""
    return _flat_props(detail)


def _required(detail: dict[str, Any]) -> set[str]:
    schema = detail.get("schema") or {}
    values = schema.get("required") if isinstance(schema, dict) else None
    return {str(item) for item in (values or [])}

def _reasons_op(name: str, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    old_params = _op_params(before)
    new_params = _op_params(after)
    for param in sorted(set(new_params) - set(old_params)):
        if "default" in new_params[param]:
            reasons.append({"kind": "additive",
                            "what": f"параметр {param} добавлен с умолчанием"})
        else:
            reasons.append({"kind": "breaking",
                            "what": f"параметр {param} добавлен без умолчания"})
    for param in sorted(set(old_params) - set(new_params)):
        reasons.append({"kind": "breaking", "what": f"параметр {param} исчез"})
    for param in sorted(set(old_params) & set(new_params)):
        old, new = old_params[param], new_params[param]
        if str(old.get("type") or "") != str(new.get("type") or ""):
            reasons.append({"kind": "breaking",
                            "what": f"тип параметра {param}: {old.get('type') or '—'} → "
                                    f"{new.get('type') or '—'}"})
        if str(old.get("default") or "") != str(new.get("default") or ""):
            reasons.append({"kind": "changed",
                            "what": f"умолчание {param}: {old.get('default') or '—'} → "
                                    f"{new.get('default') or '—'}"})
    old_returns = {str(item) for item in (before.get("returns") or [])}
    new_returns = {str(item) for item in (after.get("returns") or [])}
    for key in sorted(old_returns - new_returns):
        reasons.append({"kind": "breaking", "what": f"ключ ответа {key} исчез"})
    for key in sorted(new_returns - old_returns):
        reasons.append({"kind": "additive", "what": f"ключ ответа {key} добавлен"})
    if str(before.get("returns_from") or "") != str(after.get("returns_from") or ""):
        reasons.append({"kind": "changed",
                        "what": f"вид сборки ответа: {before.get('returns_from') or '—'} → "
                                f"{after.get('returns_from') or '—'}"})
    return reasons


def _reasons_tool(name: str, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    old_props = _flat_props(before)
    new_props = _flat_props(after)
    old_required = _required_paths(before)
    new_required = _required_paths(after)
    for prop in sorted(set(new_props) - set(old_props)):
        if prop in new_required:
            reasons.append({"kind": "breaking",
                            "what": f"свойство {prop} добавлено обязательным"})
        else:
            reasons.append({"kind": "additive",
                            "what": f"свойство {prop} добавлено необязательным"})
    for prop in sorted(set(old_props) - set(new_props)):
        reasons.append({"kind": "breaking", "what": f"свойство {prop} исчезло"})
    for prop in sorted(set(old_props) & set(new_props)):
        old, new = old_props[prop], new_props[prop]
        local: list[dict[str, str]] = []
        if str(old.get("type") or "") != str(new.get("type") or ""):
            local.append({"kind": "breaking",
                          "what": f"тип {prop}: {old.get('type') or '—'} → {new.get('type') or '—'}"})
        old_enum = {str(item) for item in (old.get("enum") or [])}
        new_enum = {str(item) for item in (new.get("enum") or [])}
        if old_enum and new_enum:
            for value in sorted(old_enum - new_enum):
                local.append({"kind": "breaking", "what": f"значение {value} убрано из {prop}"})
            for value in sorted(new_enum - old_enum):
                local.append({"kind": "additive", "what": f"значение {value} добавлено в {prop}"})
        # Сужение диапазона — такое же ломающее, как смена типа: клиент, который
        # передавал значение внутри старого диапазона, теперь получает отказ.
        for key, worse in (("minimum", "повышен"), ("minLength", "повышен"),
                           ("maximum", "понижен"), ("maxLength", "понижен")):
            before_value, after_value = old.get(key), new.get(key)
            if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)):
                narrowed = (after_value > before_value if worse == "повышен"
                            else after_value < before_value)
                if narrowed:
                    local.append({"kind": "breaking",
                                  "what": f"{key} {prop} {worse}: {before_value} → {after_value}"})
        was_required = prop in old_required
        now_required = prop in new_required
        if was_required != now_required:
            local.append({"kind": "breaking" if now_required else "additive",
                          "what": f"свойство {prop} "
                                  f"{'стало обязательным' if now_required else 'стало необязательным'}"})
        if str(old.get("default") or "") != str(new.get("default") or ""):
            local.append({"kind": "changed",
                          "what": f"умолчание {prop}: {old.get('default') or '—'} → "
                                  f"{new.get('default') or '—'}"})
        if not local and json.dumps(old, sort_keys=True, ensure_ascii=False) != \
                json.dumps(new, sort_keys=True, ensure_ascii=False):
            # Хеш изменится, значит отчёт обязан сказать хотя бы «форма другая»:
            # молчание там, где пин сработал, было бы худшим видом тишины.
            local.append({"kind": "changed", "what": f"форма {prop} изменилась"})
        reasons.extend(local)
    old_extra = (before.get("schema") or {}).get("additionalProperties", True)
    new_extra = (after.get("schema") or {}).get("additionalProperties", True)
    if old_extra is not False and new_extra is False:
        reasons.append({"kind": "breaking", "what": "лишние свойства запрещены"})
    old_meta = before.get("annotations") or {}
    new_meta = after.get("annotations") or {}
    for key, old_value, new_value, bad in (
            ("readOnlyHint", True, False, "инструмент перестал быть только читающим"),
            ("destructiveHint", False, True, "инструмент стал разрушающим"),
            ("idempotentHint", True, False, "инструмент перестал быть идемпотентным")):
        if old_meta.get(key) == old_value and new_meta.get(key) == new_value:
            reasons.append({"kind": "breaking", "what": bad})
    if old_meta.get("openWorldHint") != new_meta.get("openWorldHint"):
        reasons.append({"kind": "changed",
                        "what": f"openWorldHint: {old_meta.get('openWorldHint')} → "
                                f"{new_meta.get('openWorldHint')}"})
    return reasons


def _compare(before: dict[str, Any], after: dict[str, Any],
             reason_of) -> dict[str, Any]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = []
    for name in sorted(set(before) & set(after)):
        reasons = reason_of(name, before[name], after[name])
        if not reasons:
            continue
        kinds = {item["kind"] for item in reasons}
        changed.append({"name": name,
                        "breaking": "breaking" in kinds,
                        "reasons": reasons})
    return {"added": added, "removed": removed, "changed": changed}


def diff(before_path: str | Path, after_path: str | Path) -> dict[str, Any]:
    """Сравнить два манифеста описаний. Ничего не подтверждает и не меняет."""
    before = load_document(before_path)
    after = load_document(after_path)
    ops = _compare(_details(before, "details"), _details(after, "details"), _reasons_op)
    tools = _compare(_details(before, "tool_details"),
                     _details(after, "tool_details"), _reasons_tool)
    breaking = sum(1 for section in (ops, tools) for item in section["changed"]
                   if item["breaking"])
    breaking += len(ops["removed"]) + len(tools["removed"])
    adding = len(ops["added"]) + len(tools["added"])
    adding += sum(1 for section in (ops, tools) for item in section["changed"]
                  if not item["breaking"])
    return {
        "schema": SCHEMA,
        "before": {"path": str(before_path), "version": str(before.get("version") or ""),
                   "ops_total": int(before.get("ops_total") or 0),
                   "tools_total": int(before.get("tools_total") or 0),
                   "pin_state": str(before.get("pin_state") or "")},
        "after": {"path": str(after_path), "version": str(after.get("version") or ""),
                  "ops_total": int(after.get("ops_total") or 0),
                  "tools_total": int(after.get("tools_total") or 0),
                  "pin_state": str(after.get("pin_state") or "")},
        "ops": ops,
        "tools": tools,
        "totals": {"breaking": breaking, "compatible": adding,
                   "added": len(ops["added"]) + len(tools["added"]),
                   "removed": len(ops["removed"]) + len(tools["removed"]),
                   "changed": len(ops["changed"]) + len(tools["changed"])},
        "verdict": ("совместимо" if breaking == 0 else "есть ломающие изменения"),
        "limits": [
            "сравниваются ФАЙЛЫ поставки, а не текущее дерево: это то, что уехало клиенту",
            "поведение не сравнивается: одинаковое описание может отвечать по-разному",
            "решение «принимаю» остаётся за человеком: отчёт ничего не подтверждает",
        ],
    }
