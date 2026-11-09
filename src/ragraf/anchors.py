"""Якоря замысла в коде: разбор размеченных блоков комментариев.

Зачем. До этого замысел жил только во внешнем `contracts.yaml`: решение было
принято потому, что замысел, размазанный комментариями, нельзя ни проверить, ни
потребовать (шапка `contracts.py`). Якоря в коде возвращаются на одном условии —
они ПРОВЕРЯЕМЫ: закрытый список ключей, строгий построчный разбор, адрес
`path:line` у каждой находки и подчинение манифесту.

Что разбирается (закрытые списки, а не «что встретится»):
  START_MODULE_CONTRACT … END_MODULE_CONTRACT — PURPOSE, SCOPE, DEPENDS, LINKS,
      ROLE, MAP_MODE;
  START_CONTRACT: имя … END_CONTRACT: имя — INPUTS, OUTPUTS, SIDE_EFFECTS;
  MODULE_MAP: a, b, c либо блок START_MODULE_MAP … END_MODULE_MAP;
  START_BLOCK_* / END_BLOCK_* — только парность, семантики не несёт.

Модель та же, что у манифеста: модуль = LINKS (идентификатор), files = [путь],
depends_on = DEPENDS; контракт = bind `путь:имя`, module из текущего модуля.
Поэтому сверка с вычисленным графом не дублируется.

Границы, названные прямо:
  * сигнатура контракта из якоря НЕ выводится: INPUTS/OUTPUTS — описание, а не
    машинная подпись. Сверка сигнатуры остаётся у манифеста и названа в отчёте;
  * кандидаты берутся из индекса (комментарии с якорем), а не обходом всего
    дерева: обход 940 файлов на каждый вызов — цена без пользы. Файл с якорем,
    не попавший в индекс, не осматривается — это названо в статистике;
  * RAGRAF якоря не пишет и не правит: только читает.

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import util

#: Закрытые списки ключей. Ключ вне списка — находка, а не «пропустим».
MODULE_KEYS = ("PURPOSE", "SCOPE", "DEPENDS", "LINKS", "ROLE", "MAP_MODE")
CONTRACT_KEYS = ("INPUTS", "OUTPUTS", "SIDE_EFFECTS")

#: Пределы. Полные числа — рядом с усечёнными списками.
MAX_ANCHOR_FILES = 500

_COMMENT = r"(?://|#|/\*+|\*+|--|;|<!--)?\s*"
_START_MODULE = re.compile(rf"^\s*{_COMMENT}START_MODULE_CONTRACT\s*$")
_END_MODULE = re.compile(rf"^\s*{_COMMENT}END_MODULE_CONTRACT\s*$")
_START_CONTRACT = re.compile(
    rf"^\s*{_COMMENT}START_CONTRACT\s*:\s*([A-Za-z_][\w.]*)\s*$")
_END_CONTRACT = re.compile(
    rf"^\s*{_COMMENT}END_CONTRACT\s*:\s*([A-Za-z_][\w.]*)\s*$")
_START_MAP = re.compile(rf"^\s*{_COMMENT}START_MODULE_MAP\s*$")
_END_MAP = re.compile(rf"^\s*{_COMMENT}END_MODULE_MAP\s*$")
_MAP_INLINE = re.compile(rf"^\s*{_COMMENT}MODULE_MAP\s*:\s*(.+?)\s*$")
_START_BLOCK = re.compile(rf"^\s*{_COMMENT}START_BLOCK_([A-Za-z0-9_]+)\s*$")
_END_BLOCK = re.compile(rf"^\s*{_COMMENT}END_BLOCK_([A-Za-z0-9_]+)\s*$")
_KEY_LINE = re.compile(rf"^\s*{_COMMENT}([A-Z][A-Z0-9_]*)\s*:\s*(.*?)\s*$")


def _split_list(raw: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,\s]+", str(raw or ""))
            if item.strip()]


def candidate_paths(conn, rev_id: str, limit: int = MAX_ANCHOR_FILES) -> list[str]:
    """Файлы, в комментариях которых есть якорь. Источник — индекс, не обход."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT path FROM text_chunk WHERE rev_id = ?"
            " AND (raw LIKE '%START_MODULE_CONTRACT%' OR raw LIKE '%START_CONTRACT%')"
            " ORDER BY path LIMIT ?", (rev_id, int(limit))).fetchall()
    except Exception:  # noqa: BLE001 — нет таблицы комментариев: якорей не искать
        return []
    return [util.to_posix(row[0]) for row in rows if row[0]]


def parse_text(rel: str, text: str) -> dict[str, Any]:
    """Разобрать один файл. Возвращает модули, контракты и находки разметки."""
    modules: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    module_state: dict[str, Any] | None = None
    contract_state: dict[str, Any] | None = None
    map_state: dict[str, Any] | None = None
    blocks: dict[str, int] = {}
    seen_module = False
    file_modules: list[dict[str, Any]] = []

    def bad(line: int, detail: str) -> None:
        findings.append({"kind": "markup_malformed", "side": "anchor",
                         "path": rel, "line": int(line), "detail": detail})

    def close_module(line: int) -> None:
        nonlocal module_state
        if module_state is None:
            return
        fields = module_state["fields"]
        links = str(fields.get("LINKS") or "").strip()
        if not links:
            bad(module_state["line"], "MODULE_CONTRACT без LINKS: идентификатор модуля не задан")
            module_state = None
            return
        module_id = _split_list(links)[0]
        modules.append({
            "id": module_id,
            "purpose": str(fields.get("PURPOSE") or ""),
            "files": [rel],
            "depends_on": _split_list(str(fields.get("DEPENDS") or "")),
            "map": list(module_state["map"]),
            "map_mode": str(fields.get("MAP_MODE") or ""),
            "source": "anchor",
            "_lines": {
                "line": module_state["line"],
                "id_line": module_state["links_line"] or module_state["line"],
                "depends": dict(module_state["depends_lines"]),
                "depends_line": module_state["depends_line"] or module_state["line"],
                "map_line": module_state["map_line"] or module_state["line"],
            },
        })
        file_modules.append(modules[-1])
        module_state = None

    def close_contract(line: int) -> None:
        nonlocal contract_state
        if contract_state is None:
            return
        name = contract_state["name"]
        if module_state is not None:
            owner_id = str(module_state["fields"].get("LINKS") or "")
            module_line = module_state["line"]
        elif file_modules:
            owner_id = str(file_modules[-1]["id"])
            module_line = int((file_modules[-1].get("_lines") or {}).get("line") or 1)
        else:
            owner_id = ""
            module_line = 1
        links = _split_list(owner_id)
        if not links:
            bad(contract_state["line"],
                f"CONTRACT: {name} вне MODULE_CONTRACT: в файле нет ни одного модуля")
            contract_state = None
            return
        module_id = links[0]
        contracts.append({
            "id": f"{module_id}:{name}",
            "module": module_id,
            "bind": f"{rel}:{name}",
            "signature": "",
            "calls": [],
            "source": "anchor",
            "_lines": {"line": contract_state["line"],
                       "bind_line": contract_state["line"],
                       "module_line": module_line},
        })
        contract_state = None

    lines = text.splitlines()
    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        start_module = _START_MODULE.match(line)
        if start_module:
            if module_state is not None:
                bad(index, "второй START_MODULE_CONTRACT до закрытия предыдущего")
                close_module(index)
            if seen_module:
                bad(index, "в файле больше одного MODULE_CONTRACT: уникальность нарушена")
            seen_module = True
            module_state = {"line": index, "fields": {}, "map": [],
                            "links_line": 0, "depends_line": 0, "map_line": 0,
                            "depends_lines": {}}
            continue
        if _END_MODULE.match(line):
            if module_state is None:
                bad(index, "END_MODULE_CONTRACT без START_MODULE_CONTRACT")
            else:
                close_module(index)
            continue
        start_contract = _START_CONTRACT.match(line)
        if start_contract:
            if contract_state is not None:
                bad(index, "второй START_CONTRACT до закрытия предыдущего")
                close_contract(index)
            contract_state = {"name": start_contract.group(1), "line": index,
                              "fields": {}}
            continue
        end_contract = _END_CONTRACT.match(line)
        if end_contract:
            name = end_contract.group(1)
            if contract_state is None:
                bad(index, f"END_CONTRACT: {name} без START_CONTRACT")
            elif contract_state["name"] != name:
                bad(index, f"END_CONTRACT: {name} не совпадает со START_CONTRACT: "
                           f"{contract_state['name']}")
                close_contract(index)
            else:
                close_contract(index)
            continue
        if _START_MAP.match(line):
            map_state = {"line": index, "names": []}
            continue
        if _END_MAP.match(line):
            if map_state is None:
                bad(index, "END_MODULE_MAP без START_MODULE_MAP")
            else:
                if module_state is not None:
                    module_state["map"].extend(map_state["names"])
                    module_state["map_line"] = map_state["line"]
                map_state = None
            continue
        if map_state is not None:
            map_state["names"].extend(_split_list(line.strip(" */*#")))
            continue
        inline_map = _MAP_INLINE.match(line)
        if inline_map:
            if module_state is None:
                bad(index, "MODULE_MAP вне MODULE_CONTRACT")
            else:
                module_state["map"].extend(_split_list(inline_map.group(1)))
                module_state["map_line"] = index
            continue
        start_block = _START_BLOCK.match(line)
        if start_block:
            name = start_block.group(1)
            if name in blocks:
                bad(index, f"START_BLOCK_{name} объявлен дважды")
            blocks[name] = index
            continue
        end_block = _END_BLOCK.match(line)
        if end_block:
            name = end_block.group(1)
            if name not in blocks:
                bad(index, f"END_BLOCK_{name} без START_BLOCK_{name}")
            else:
                blocks.pop(name, None)
            continue
        if contract_state is not None:
            match = _KEY_LINE.match(line)
            if match:
                key = match.group(1)
                if key not in CONTRACT_KEYS:
                    bad(index, f"ключ {key} вне закрытого списка контракта "
                               f"({', '.join(CONTRACT_KEYS)})")
                continue
            continue
        if module_state is not None:
            match = _KEY_LINE.match(line)
            if match:
                key, value = match.group(1), match.group(2)
                if key not in MODULE_KEYS:
                    bad(index, f"ключ {key} вне закрытого списка модуля "
                               f"({', '.join(MODULE_KEYS)})")
                    continue
                module_state["fields"][key] = value
                if key == "LINKS":
                    module_state["links_line"] = index
                elif key == "DEPENDS":
                    module_state["depends_line"] = index
                    for dep in _split_list(value):
                        module_state["depends_lines"][dep] = index
                continue

    if contract_state is not None:
        bad(contract_state["line"],
            f"CONTRACT: {contract_state['name']} не закрыт END_CONTRACT")
    if module_state is not None:
        bad(module_state["line"], "MODULE_CONTRACT не закрыт END_MODULE_CONTRACT")
    if map_state is not None:
        bad(map_state["line"], "MODULE_MAP не закрыт END_MODULE_MAP")
    for name, start in sorted(blocks.items()):
        bad(start, f"START_BLOCK_{name} не закрыт END_BLOCK_{name}")
    return {"modules": modules, "contracts": contracts, "findings": findings}


def load(root: str | Path, conn, rev_id: str) -> dict[str, Any]:
    """Собрать якоря ревизии: кандидаты из индекса, разбор с диска."""
    root = Path(root)
    paths = candidate_paths(conn, rev_id)
    indexed = len(paths)
    if not paths:
        # Комментарии индекса не всегда хранят разметку (зависит от языка и
        # извлекателя). Тогда кандидаты берутся по файлам кода — но ТОЛЬКО
        # когда быстрый путь дал ноль: иначе каждый вызов читал бы весь корпус.
        try:
            rows = conn.execute(
                "SELECT DISTINCT path FROM node WHERE rev_id = ?"
                " AND path IS NOT NULL AND path <> '' ORDER BY path LIMIT ?",
                (rev_id, MAX_ANCHOR_FILES)).fetchall()
            paths = [util.to_posix(row[0]) for row in rows if row[0]]
        except Exception:  # noqa: BLE001
            paths = []
    modules: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    read = 0
    for rel in paths:
        target = root / rel
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append({"kind": "markup_malformed", "side": "anchor",
                             "path": rel, "line": 1,
                             "detail": f"файл с якорем не читается: {type(exc).__name__}"})
            continue
        read += 1
        parsed = parse_text(rel, text)
        modules.extend(parsed["modules"])
        contracts.extend(parsed["contracts"])
        findings.extend(parsed["findings"])
    modules.sort(key=lambda item: (item["id"], item["files"][0]))
    contracts.sort(key=lambda item: (item["bind"], item["id"]))
    findings.sort(key=lambda item: (item["path"], item["line"], item["detail"]))
    return {
        "files": read,
        "candidates": indexed,
        "scanned": len(paths),
        "modules": modules,
        "contracts": contracts,
        "findings": findings,
        "stats": {
            "files": read,
            "candidates": indexed,
            "scanned": len(paths),
            "modules": len(modules),
            "contracts": len(contracts),
            "malformed": len(findings),
        },
    }
