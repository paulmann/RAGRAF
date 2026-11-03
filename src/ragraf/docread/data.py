"""Читатель данных: CSV/TSV, JSON/JSONL, XML, YAML, TOML.

Главное правило этого модуля: **чанк — логическая единица со своим адресом**, а не
произвольный набор строк.

Из него следуют три решения, которые иначе выглядят как придирки:

1. **CSV: заголовок колонок в каждом чанке.** Строка `42, 2026-10-29` без имён
   колонок — это два числа без смысла: агент не может сказать, что такое 42.
   Поэтому записи группируются в узлы, у которых первая строка — заголовок, а
   `repeat_header` заставляет повторять его при разрезе по бюджету.

2. **JSON/XML/YAML/TOML: адрес — путь в структуре.** `path#/items/0` (JSON Pointer)
   и `path#xpath(/root/item[2])` разрешимы: повторный разбор даёт тот же фрагмент.
   Адрес `path:12` — нет: в JSON нет строк в смысле файла, а pretty-print может
   измениться от одного пробела.

3. **Фрагмент, который нельзя разобрать, обязан нести `parent_path`.** Разрез
   большого объекта по бюджету рвёт синтаксис; тогда «где я нахожусь» — это и есть
   весь адрес. Это проверка 6 валидатора, и она включается здесь, а не «потом».

Кодировки: CSV и TSV приходят в cp1251 и с BOM (замерено на реальных корпусах),
поэтому декодирование идёт через `textdec`, а не через `open(encoding="utf-8")`.

Чего здесь нет и почему:
  * `pandas` — в окружении отсутствует (замер 29.10.2026), а тянуть его из сети при
    индексации запрещено. `csv` из стандартной библиотеки делает то, что нужно;
  * записи Excel в CSV с разделителем `;` — реальность русской локали, поэтому
    разделитель определяется по первой строке, а не считается запятой.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from .. import textdec
from ..docnode import DocumentNode, ReadResult

#: Сколько строк попадает в один узел CSV. Мера — не «удобно», а бюджет: 40 строк
#: по ~80 символов это ~3200 символов, то есть внутри предела эмбеддера (4000).
CSV_ROWS_PER_NODE = 40

#: Сколько элементов списка JSON превращаются в отдельные узлы. Дальше — один узел
#: на остаток: список из 50 000 записей иначе дал бы 50 000 чанков.
JSON_ITEMS_LIMIT = 500

#: Глубина обхода структуры. Три уровня — это «объект → его поля → вложенные поля»;
#: глубже адрес перестаёт помогать (он длиннее самого содержимого).
JSON_DEPTH = 3

CSV_SUFFIXES = (".csv", ".tsv")
JSON_SUFFIXES = (".json", ".jsonl", ".ndjson")
XML_SUFFIXES = (".xml", ".xsd", ".xsl", ".svg", ".rss", ".atom")
YAML_SUFFIXES = (".yaml", ".yml")
TOML_SUFFIXES = (".toml",)


def _clean(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _pointer(parent: str, key: Any) -> str:
    """JSON Pointer: `/items/0`. Ключ экранируется по RFC 6901."""
    token = str(key).replace("~", "~0").replace("/", "~1")
    return f"{parent}/{token}"


# ---------------------------------------------------------------------------
# CSV / TSV
# ---------------------------------------------------------------------------


def _sniff_delimiter(sample: str, suffix: str) -> str:
    if suffix == ".tsv":
        return "\t"
    first = next((line for line in sample.splitlines() if line.strip()), "")
    counts = {candidate: first.count(candidate) for candidate in (";", ",", "\t", "|")}
    best = max(counts, key=lambda key: counts[key])
    return best if counts[best] else ","


def read_csv(rel: str, raw: bytes) -> ReadResult:
    text, encoding = textdec.decode(raw, rel)
    text = _clean(text)
    if not text:
        return ReadResult(status="degraded", reason="файл пуст", reader="csv")
    suffix = Path(rel).suffix.lower()
    delimiter = _sniff_delimiter(text[:8192], suffix)
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as exc:
        return ReadResult(status="failed",
                          reason=f"разбор CSV: {type(exc).__name__}: {exc}",
                          reader="csv")
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ReadResult(status="degraded", reason="в файле нет непустых строк",
                          reader="csv")
    header = rows[0]
    header_line = " | ".join(cell.strip() for cell in header)
    data_rows = rows[1:]
    nodes: list[DocumentNode] = []
    table_id = f"csv:{Path(rel).name}"
    if not data_rows:
        nodes.append(DocumentNode(
            kind="record",
            locator=f"{rel}:row=1",
            text=header_line,
            structure={"columns": header_line, "table_id": table_id,
                       "rows": 1, "parent_path": "/",
                       "no_heading_reason": "CSV: одна строка заголовков"},
        ))
    else:
        for start in range(0, len(data_rows), CSV_ROWS_PER_NODE):
            block = data_rows[start:start + CSV_ROWS_PER_NODE]
            lines = [" | ".join(cell.strip() for cell in row) for row in block]
            first = start + 2          # строка 1 — заголовок
            last = first + len(block) - 1
            locator = (f"{rel}:row={first}" if first == last
                       else f"{rel}:rows={first}-{last}")
            nodes.append(DocumentNode(
                kind="record",
                locator=locator,
                text=f"{header_line}\n" + "\n".join(lines),
                structure={
                    "columns": header_line,
                    # Заголовок колонок повторяется в каждой части разрезанного
                    # узла: значение без имени колонки — число без смысла.
                    "repeat_header": header_line,
                    "table_id": table_id,
                    "rows": len(block),
                    "parent_path": "/",
                },
                metadata={"delimiter": delimiter, "encoding": encoding},
            ))
    return ReadResult(nodes=nodes, status="ok", reader="csv",
                      metadata={"delimiter": delimiter, "encoding": encoding,
                                "header": header_line, "rows": len(data_rows)})


# ---------------------------------------------------------------------------
# JSON / JSONL / YAML / TOML
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _flatten(value: Any, pointer: str, depth: int, out: list[tuple[str, str, dict]],
             limit: int) -> None:
    """Разложить структуру на `(pointer, текст, структура)`.

    Правило: объект раскрывается на поля, список — на элементы до предела, скаляр
    становится листом. Всё, что глубже `JSON_DEPTH`, остаётся одним куском: адрес
    глубже содержимого не помогает.
    """
    if depth >= JSON_DEPTH or isinstance(value, (str, int, float, bool)) or value is None:
        out.append((pointer, _scalar(value), {"parent_path": pointer}))
        return
    if isinstance(value, dict):
        if not value:
            out.append((pointer, "{}", {"parent_path": pointer}))
            return
        for key, item in value.items():
            child = _pointer(pointer, key)
            if isinstance(item, (dict, list)) and depth + 1 < JSON_DEPTH:
                _flatten(item, child, depth + 1, out, limit)
            else:
                text = json.dumps(item, ensure_ascii=False, indent=1) \
                    if isinstance(item, (dict, list)) else _scalar(item)
                out.append((child, text, {"parent_path": pointer}))
        return
    if isinstance(value, list):
        if not value:
            out.append((pointer, "[]", {"parent_path": pointer}))
            return
        for index, item in enumerate(value[:limit]):
            child = _pointer(pointer, index)
            if isinstance(item, (dict, list)) and depth + 1 < JSON_DEPTH:
                _flatten(item, child, depth + 1, out, limit)
            else:
                text = json.dumps(item, ensure_ascii=False, indent=1) \
                    if isinstance(item, (dict, list)) else _scalar(item)
                out.append((child, text, {"parent_path": pointer}))
        if len(value) > limit:
            rest = value[limit:]
            out.append((pointer, json.dumps(rest, ensure_ascii=False, indent=1),
                        {"parent_path": pointer, "items_truncated": len(rest)}))
        return
    out.append((pointer, _scalar(value), {"parent_path": pointer}))


def _nodes_from_data(rel: str, data: Any, reader: str) -> list[DocumentNode]:
    flat: list[tuple[str, str, dict]] = []
    _flatten(data, "", 0, flat, JSON_ITEMS_LIMIT)
    nodes: list[DocumentNode] = []
    for pointer, text, structure in flat:
        locator = f"{rel}#{pointer or '/'}"
        structure = dict(structure)
        structure.setdefault("no_heading_reason",
                             "структурированные данные: разделов нет")
        nodes.append(DocumentNode(
            kind="record",
            locator=locator,
            text=text,
            structure=structure,
        ))
    return nodes


def read_json(rel: str, raw: bytes) -> ReadResult:
    text, encoding = textdec.decode(raw, rel)
    suffix = Path(rel).suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        nodes: list[DocumentNode] = []
        bad = 0
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            pointer = _pointer("", number - 1)
            nodes.append(DocumentNode(
                kind="record",
                locator=f"{rel}#{pointer}",
                text=json.dumps(item, ensure_ascii=False, indent=1),
                structure={"parent_path": "", "line": number,
                           "no_heading_reason": "JSONL: записи построчно"},
            ))
        if not nodes:
            return ReadResult(status="failed",
                              reason=f"ни одна строка не разобрана как JSON (строк {bad})",
                              reader="jsonl")
        warnings = [f"строк не разобрано: {bad}"] if bad else []
        return ReadResult(nodes=nodes, status="ok", reader="jsonl",
                          metadata={"encoding": encoding, "lines": len(nodes)},
                          warnings=warnings)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return ReadResult(status="failed",
                          reason=f"разбор JSON: {exc.msg} (строка {exc.lineno})",
                          reader="json")
    return ReadResult(nodes=_nodes_from_data(rel, data, "json"), status="ok",
                      reader="json", metadata={"encoding": encoding})


def read_yaml(rel: str, raw: bytes) -> ReadResult:
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="degraded",
                          reason=f"нет библиотеки PyYAML: структура не разобрана "
                                 f"({type(exc).__name__})",
                          reader="yaml")
    text, encoding = textdec.decode(raw, rel)
    warnings: list[str] = []
    try:
        data = yaml.safe_load(text)
    except yaml.constructor.ConstructorError:
        # Измерено на `community/mkdocs.yml` (29.10.2026): конфигурация mkdocs
        # содержит тег `!!python/name:markdown.extensions.toc.slugify_unicode`.
        # `safe_load` на нём падает и — без этой ветки — файл терялся ЦЕЛИКОМ:
        # раньше он индексировался текстом и находился поиском, то есть отказ
        # читателя был бы регрессией покрытия, а не честной деградацией.
        # Неизвестный тег разбирается как строка: значение известно, тип — нет.
        class _Tolerant(yaml.SafeLoader):
            pass

        def _unknown(loader, suffix, node):  # noqa: ANN001 — сигнатура PyYAML
            if isinstance(node, yaml.ScalarNode):
                return loader.construct_scalar(node)
            return None

        _Tolerant.add_multi_constructor("tag:yaml.org,2002:python/", _unknown)
        _Tolerant.add_multi_constructor("!", _unknown)
        try:
            data = yaml.load(text, Loader=_Tolerant)
        except Exception as exc:  # noqa: BLE001
            return ReadResult(status="failed",
                              reason=f"разбор YAML: {type(exc).__name__}: {exc}",
                              reader="yaml")
        warnings.append("YAML содержит нестандартные теги: разобраны как строки")
    except Exception as exc:  # noqa: BLE001 — сообщение парсера и есть причина
        return ReadResult(status="failed",
                          reason=f"разбор YAML: {type(exc).__name__}: {exc}",
                          reader="yaml")
    if data is None:
        return ReadResult(status="degraded", reason="в файле нет данных",
                          reader="yaml", metadata={"encoding": encoding})
    return ReadResult(nodes=_nodes_from_data(rel, data, "yaml"), status="ok",
                      reader="yaml", metadata={"encoding": encoding},
                      warnings=warnings)


def read_toml(rel: str, raw: bytes) -> ReadResult:
    try:
        import tomllib
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="degraded",
                          reason=f"нет tomllib: структура не разобрана "
                                 f"({type(exc).__name__})",
                          reader="toml")
    text, encoding = textdec.decode(raw, rel)
    try:
        data = tomllib.loads(text)
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="failed",
                          reason=f"разбор TOML: {type(exc).__name__}: {exc}",
                          reader="toml")
    return ReadResult(nodes=_nodes_from_data(rel, data, "toml"), status="ok",
                      reader="toml", metadata={"encoding": encoding})


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------


def read_xml(rel: str, raw: bytes) -> ReadResult:
    try:
        from lxml import etree
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="degraded",
                          reason=f"нет библиотеки lxml: структура XML не разобрана "
                                 f"({type(exc).__name__})",
                          reader="xml")
    parser = etree.XMLParser(recover=False, resolve_entities=False,
                             huge_tree=False, no_network=True)
    try:
        root = etree.fromstring(raw, parser=parser)
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="failed",
                          reason=f"разбор XML: {type(exc).__name__}: {exc}",
                          reader="xml")
    nodes: list[DocumentNode] = []
    truncated = 0
    MAX_ELEMENTS = 20000
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        text = _clean(" ".join(part.strip() for part in element.itertext()
                               if part and part.strip()))
        if not text:
            continue
        if len(nodes) >= MAX_ELEMENTS:
            truncated += 1
            continue
        try:
            xpath = element.getroottree().getpath(element)
        except Exception:  # noqa: BLE001
            continue
        # Родитель корневого элемента — сам документ. Пустая строка здесь означала
        # бы «родитель неизвестен», и проверка 6 справедливо считала бы адрес
        # неполным; `"/"` говорит правду: выше корня в дереве ничего нет.
        parent = "/"
        parent_element = element.getparent()
        if parent_element is not None:
            try:
                parent = parent_element.getroottree().getpath(parent_element)
            except Exception:  # noqa: BLE001
                parent = "/"
        nodes.append(DocumentNode(
            kind="record",
            locator=f"{rel}#xpath({xpath})",
            text=text,
            structure={"parent_path": parent, "tag": str(element.tag),
                       "no_heading_reason": "XML: разделов нет, адрес — путь в дереве"},
            metadata={"attributes": len(element.attrib)},
        ))
    warnings: list[str] = []
    if truncated:
        warnings.append(f"элементов сверх предела {MAX_ELEMENTS}: {truncated}")
    if not nodes:
        return ReadResult(status="degraded",
                          reason="в XML нет текстового содержимого",
                          reader="xml", warnings=warnings)
    return ReadResult(nodes=nodes, status="ok", reader="xml",
                      metadata={"elements": len(nodes)}, warnings=warnings)


# ---------------------------------------------------------------------------
# Диспетчеризация
# ---------------------------------------------------------------------------


def read(rel: str, raw: bytes) -> ReadResult:
    suffix = Path(rel).suffix.lower()
    if suffix in CSV_SUFFIXES:
        return read_csv(rel, raw)
    if suffix in JSON_SUFFIXES:
        return read_json(rel, raw)
    if suffix in XML_SUFFIXES:
        return read_xml(rel, raw)
    if suffix in YAML_SUFFIXES:
        return read_yaml(rel, raw)
    if suffix in TOML_SUFFIXES:
        return read_toml(rel, raw)
    return ReadResult(status="failed",
                      reason=f"читатель данных не знает формат: {rel}",
                      reader="data")
