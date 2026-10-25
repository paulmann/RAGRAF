"""Разбор экспорта CPG в формате neo4jcsv (без JVM).

Почему CSV, а не GraphSON: на полном корпусе (323 PHP-файла) экспорт GraphSON
падает с OutOfMemoryError — он собирает один огромный JSON («Required array
length 2147483640 + 8 is too large»), и даже 18 ГБ heap не хватает. Формат
neo4jcsv отдаёт данные потоково: **отдельный CSV на каждую метку вершины и на
каждый тип ребра**. Замер: 223 МБ, 27 с, код 0 (см. tools/probe_export_variants.py).

Фактическая структура (проверено на реальном экспорте):
    nodes_<LABEL>_header.csv  — одна строка с колонками
    nodes_<LABEL>_data.csv    — данные БЕЗ заголовка, колонки по порядку
    edges_<TYPE>_header.csv   — ":START_ID,:END_ID,:TYPE"
    edges_<TYPE>_data.csv     — данные без заголовка

Преимущество такого формата: можно читать только нужные метки и типы, не разбирая
десятки мегабайт посторонних данных (BLOCK, IDENTIFIER, LITERAL и т.п.).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# Колонки экспорта → наши имена полей.
PROP_MAP = {
    "NAME": "name",
    "FULL_NAME": "fullName",
    "FILENAME": "filename",
    "LINE_NUMBER": "lineNumber",
    "LINE_NUMBER_END": "lineNumberEnd",
    "COLUMN_NUMBER": "columnNumber",
    "IS_EXTERNAL": "isExternal",
    "SIGNATURE": "signature",
    "CODE": "code",
    "METHOD_FULL_NAME": "methodFullName",
    "DISPATCH_TYPE": "dispatchType",
    "ORDER": "order",
    "TYPE_FULL_NAME": "typeFullName",
}

# Метки вершин, которые нужны для связей. Остальные не читаются вообще.
WANTED_LABELS = {
    "FILE", "METHOD", "TYPE_DECL", "CALL", "METHOD_REF", "NAMESPACE_BLOCK", "NAMESPACE",
}

# Типы рёбер, нужные для связей и срезов.
WANTED_EDGE_TYPES = {
    "CALL", "AST", "CFG", "DDG", "REACHING_DEF", "REF", "CONTAINS",
    "INHERITS_FROM", "SOURCE_FILE", "ARGUMENT",
}


@dataclass
class JoernGraph:
    """Плоское представление CPG."""

    nodes: dict[int, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    nodes_total: int = 0
    edges_total: int = 0
    files_seen: int = 0
    files_used: int = 0

    def label_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for node in self.nodes.values():
            label = str(node.get("label") or "?")
            out[label] = out.get(label, 0) + 1
        return out

    def edge_label_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.edges:
            label = str(e.get("label") or "?")
            out[label] = out.get(label, 0) + 1
        return out


def _strip(name: str) -> str:
    """Убрать служебные префиксы Neo4j-CSV и суффикс типа."""
    text = name.strip().lstrip("\ufeff").lstrip(":")
    if ":" in text:
        text = text.split(":", 1)[0]
    return text.upper()


def load_export(path: str | Path) -> JoernGraph:
    """Прочитать каталог экспорта neo4jcsv.

    Читаются только нужные метки вершин и типы рёбер: файлы остальных меток
    пропускаются целиком (это основная экономия — десятки мегабайт).
    """
    p = Path(path)
    if not p.is_dir():
        raise FileNotFoundError(f"ожидался каталог экспорта: {p}")

    graph = JoernGraph()
    files = sorted(f for f in p.iterdir() if f.is_file())

    # --- вершины ---
    for header_file in files:
        name = header_file.name
        if not (name.startswith("nodes_") and name.endswith("_header.csv")):
            continue
        label = name[len("nodes_") : -len("_header.csv")].strip("_")
        data_file = header_file.with_name(name.replace("_header.csv", "_data.csv"))
        if not data_file.exists():
            continue
        graph.files_seen += 1
        if label not in WANTED_LABELS:
            continue
        graph.files_used += 1
        _load_nodes(header_file, data_file, label, graph)

    # --- рёбра ---
    for header_file in files:
        name = header_file.name
        if not (name.startswith("edges_") and name.endswith("_header.csv")):
            continue
        edge_type = name[len("edges_") : -len("_header.csv")].strip("_")
        data_file = header_file.with_name(name.replace("_header.csv", "_data.csv"))
        if not data_file.exists():
            continue
        graph.files_seen += 1
        if edge_type not in WANTED_EDGE_TYPES:
            continue
        graph.files_used += 1
        _load_edges(header_file, data_file, edge_type, graph)

    return graph


def _read_header(path: Path) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.reader(fh):
            if row:
                return [c for c in row]
    return []


def _iter_pairs(header: list[str], data_file: Path) -> Iterator[tuple[str, str]]:
    """Отдавать (колонка, значение) для каждой строки данных.

    Данные идут без заголовка, поэтому колонки сопоставляются по порядку.
    Разделитель — запятая (формат Neo4j CSV); экранирование кавычками учитывается
    стандартным csv-ридером.
    """
    normalized = [_strip(h) for h in header]
    with open(data_file, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row:
                continue
            # Строка целиком может быть пустой или содержать только пробелы.
            if len(row) == 1 and not row[0].strip():
                continue
            for idx, value in enumerate(row):
                if idx >= len(normalized):
                    break
                yield normalized[idx], value


def _load_nodes(header_file: Path, data_file: Path, label: str, graph: JoernGraph) -> None:
    header = _read_header(header_file)
    current: dict | None = None

    for col, value in _iter_pairs(header, data_file):
        if col == "ID":
            if current is not None:
                _store_node(current, graph)
            try:
                current = {"id": int(value), "label": label}
            except (TypeError, ValueError):
                current = None
            continue
        if current is None:
            continue
        dst = PROP_MAP.get(col)
        if dst and value.strip():
            current[dst] = value.strip()
    if current is not None:
        _store_node(current, graph)


def _store_node(node: dict, graph: JoernGraph) -> None:
    """Сохранить вершину: считаем все, храним только с непустым идентификатором."""
    graph.nodes_total += 1
    node_id = node.get("id")
    if node_id is None:
        return
    graph.nodes[int(node_id)] = node


def _load_edges(header_file: Path, data_file: Path, edge_type: str, graph: JoernGraph) -> None:
    """Рёбра: заголовок Neo4j вида ':START_ID,:END_ID,:TYPE'.

    Тип ребра известен из имени файла, поэтому берём только пару идентификаторов —
    дополнительных колонок у рёбер CALL/CFG нет.
    """
    header = _read_header(header_file)
    normalized = [_strip(h) for h in header]
    try:
        idx_start = normalized.index("START_ID")
        idx_end = normalized.index("END_ID")
    except ValueError:
        # Запасной вариант: порядок start, end, type.
        idx_start, idx_end = 0, 1

    with open(data_file, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if len(row) <= max(idx_start, idx_end):
                continue
            try:
                start = int(row[idx_start])
                end = int(row[idx_end])
            except (TypeError, ValueError):
                continue
            graph.edges_total += 1
            graph.edges.append({"label": edge_type, "outV": start, "inV": end})


# --------------------------------------------------------------------------
# Извлечение связей
# --------------------------------------------------------------------------


def extract_method_index(graph: JoernGraph) -> dict[int, dict]:
    """Методы CPG: id → {name, fullName, filename, line, external}."""
    out: dict[int, dict] = {}
    for nid, node in graph.nodes.items():
        if node.get("label") != "METHOD":
            continue
        out[nid] = {
            "name": node.get("name"),
            "fullName": node.get("fullName"),
            "filename": node.get("filename"),
            "line": _as_int(node.get("lineNumber")),
            "external": str(node.get("isExternal", "")).lower() in ("true", "1"),
        }
    return out


def calls_with_owner(graph: JoernGraph) -> list[dict]:
    """Вызовы с методом-владельцем.

    Владелец определяется по вложению в AST: спускаемся по AST-рёбрам от каждого
    метода и помечаем встреченные узлы его идентификатором.

    КРИТИЧНО (ошибка, найденная прогоном и исправленная здесь): при обходе НЕЛЬЗЯ
    заходить в другие METHOD-узлы. Иначе метод-заглушка `<global>` (он есть в каждом
    файле и содержит весь файл в AST) захватывает вызовы всех методов проекта:
    на реальном корпусе 179 330 из 183 357 вызовов получили владельца `<global>`, и
    после фильтрации осталось 992 вызова.
    """
    methods = extract_method_index(graph)
    if not methods:
        return []

    call_targets: dict[int, int] = {}
    children: dict[int, list[int]] = {}
    for e in graph.edges:
        if e["label"] == "CALL":
            call_targets.setdefault(int(e["outV"]), int(e["inV"]))
        elif e["label"] == "AST":
            children.setdefault(int(e["outV"]), []).append(int(e["inV"]))

    owner_of: dict[int, int] = {}
    for mid in methods:
        stack = list(children.get(mid, ()))
        seen = 0
        while stack:
            nid = stack.pop()
            seen += 1
            if seen > 1_000_000:
                break
            # Граница метода: вызовы внутреннего метода принадлежат ему, не внешнему.
            if nid in methods and nid != mid:
                continue
            if nid in owner_of:
                continue
            owner_of[nid] = mid
            child_list = children.get(nid)
            if child_list:
                stack.extend(child_list)

    out: list[dict] = []
    for call_id, node in graph.nodes.items():
        if node.get("label") != "CALL":
            continue
        callee = node.get("methodFullName")
        target_id = call_targets.get(call_id)
        if not callee and target_id is not None and target_id in graph.nodes:
            callee = graph.nodes[target_id].get("fullName")
        if not callee:
            continue
        owner_id = owner_of.get(call_id)
        owner = methods.get(owner_id) if owner_id is not None else None
        out.append(
            {
                "call_id": call_id,
                "owner_method": owner.get("fullName") if owner else None,
                "owner_file": owner.get("filename") if owner else None,
                "callee": str(callee),
                "callee_name": node.get("name"),
                "line": _as_int(node.get("lineNumber")),
                "column": _as_int(node.get("columnNumber")),
                "dispatch": node.get("dispatchType"),
                "code": (node.get("code") or "")[:200],
                "callee_node_id": target_id,
            }
        )
    return out


def normalize_joern_path(path: str | None) -> str | None:
    """Привести путь из CPG к виду нашего графа.

    Joern для PHP отдаёт пути с ведущей точкой и разделителями Windows:
    `.zwork\\artifacts\\x\\PipelineEngine.php`, `src\\Core\\Config.php`.
    Наш граф хранит POSIX-пути без ведущей точки (`src/Core/Config.php`).
    Без нормализации сопоставление по файлу всегда даёт отказ (проверено прогоном).
    """
    if not path:
        return None
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    # Схлопывание двойных разделителей: они появляются, если путь собран
    # из двух частей. Без этого путь не совпадёт с индексом, где разделители
    # одинарные (проверено вызовом: `\\core\\x.py` давало `core//x.py`).
    while "//" in text:
        text = text.replace("//", "/")
    return text or None


def normalize_joern_name(name: str | None) -> str:
    """Привести имя Joern к нашему формату.

    Joern для PHP пишет методы через стрелку (`App\\Core\\Config->get`),
    а наш граф использует двойное двоеточие (`App\\Core\\Config::get`).
    Без этой нормализации сопоставление имён не работает вообще — проверено
    прогоном: из 992 вызовов не привязался ни один.

    Так же отбрасывается суффикс лямбды (`-><lambda>0`) и заглушка `<clinit>`.

    ИМЯ ДЛЯ PYTHON. Здесь было только правило PHP, и на Python-корпусе
    нормализация портила имя: Joern отдаёт вызов вида
        core\\api_errors.py:__init__
    а правило заменяло все `->` на `::` и обрезало ведущие точки, из-за
    чего имя не совпадало ни с одним fqn индекса (`core/api_errors.py:__init__`).
    Замер: 40 806 вызовов, подтверждено 0.

    Для Python имя надо привести к нашему виду:
      * заменить разделители пути (обратные слеши → прямые);
      * убрать пробелы вокруг разделителя `:`;
      * сохранить форму `путь:имя`, она уже совпадает с fqn индекса.
    """
    if not name:
        return ""
    text = str(name).strip().lstrip("\\")
    # --- Python: путь с обратными слешами и разделитель `:` ---
    # Признак: имя содержит `.py:` — так Joern обозначает метод Python
    # внутри файла. Для таких имён правило PHP не применяется.
    if ".py:" in text or text.endswith(".py"):
        path, sep, symbol = text.partition(":")
        # Замена и схлопывание: нормализация должна быть устойчива
        # к двойным разделителям (они встречаются в именах Joern, если
        # путь собирался из двух частей).
        path = path.replace("\\", "/").lstrip("./")
        while "//" in path:
            path = path.replace("//", "/")
        return f"{path}:{symbol}" if sep else path
    # --- PHP: имена методов через стрелку ---
    if "-><lambda>" in text:
        text = text.split("-><lambda>", 1)[0]
    if text.endswith("::<clinit>") or text.endswith("-><clinit>"):
        text = text.rsplit("::" if "::" in text else "->", 1)[0]
    text = text.replace("->", "::")
    # Ведущая точка (путь файла вместо пространства имён) не нужна.
    return text.lstrip(".")


def is_global_owner(owner: str | None) -> bool:
    """Владелец — глобальная область файла, а НЕ вложенный в неё символ.

    Такие вызовы не к чему привязать: у файла нет символа-владельца в терминах
    методов. Обозначение РАЗНОЕ у разных языков, и проверять надо оба маркера:
    `<global>` (PHP) и `<module>` (Python).

    Чего здесь НЕЛЬЗЯ делать — считать глобальным любой владелец, содержащий
    маркер. Маркер обозначает модуль, а не только его тело: у вложенного в модуль
    символа владелец выглядит как `<module>.Имя` (проверено на реальном артефакте
    запросов: `src\\ragraf\\facade.py:<module>.Facade._quality`,
    `src\\ragraf\\telemetry.py:<module>.Telemetry.summary`). Прежняя проверка
    подстроки отбрасывала и такие записи: из 129 770 записей обходного пути
    отсеивались ВСЕ 129 770, в том числе 39 762 с настоящим вложенным символом.
    Мера этого дефекта — не «меньше связей», а «ноль связей при наличии данных».

    Правило: глобальна только заглушка БЕЗ имени после маркера. Хвост вида
    `<module>`, `<global>`, `<module>-><lambda>0` — глобальная область;
    `<module>.name` — реальный символ, и запись обязана идти дальше.
    """
    if not owner:
        return True
    text = str(owner)
    for marker in ("<global>", "<module>"):
        if marker in text:
            tail = text.split(marker, 1)[1]
            # Имя символа начинается с точки: `<module>.Telemetry.summary`.
            # Лямбда и суффиксы методов идут через дефис и остаются глобальными.
            return not tail.startswith(".")
    return False


def _as_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
