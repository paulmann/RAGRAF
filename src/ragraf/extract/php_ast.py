"""Извлечение фактов из PHP-файла через tree-sitter (ФТ-2..ФТ-9).

Извлекается из одного файла:
  * узлы: file, namespace, class/interface/trait/enum, function, method,
    property, constant, sql_literal, http_endpoint;
  * рёбра: contains, defines, imports, imports_heuristic, calls, calls_heuristic,
    instantiates, reads_superglobal, queries;
  * текстовый слой: комментарии и докблоки — ОТДЕЛЬНО от структурных фактов (ФТ-9).

Принципы:
  * каждое ребро несёт строку-доказательство (ФТ-10);
  * динамические конструкции дают ребро вида *_heuristic с пониженной достоверностью,
    а не молча теряются и не выдаются за точные (ФТ-4);
  * обход рекурсивный с явным входом/выходом из области видимости: иначе методы
    разных классов сливаются в один (ошибка, пойманная на первом прогоне);
  * разбор деградирует мягко: файл с ошибкой помечается partial/failed, но не
    выбрасывается — иначе отчёт покрытия соврёт (ФТ-11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import util

EXT_PREFIX = "@ext:"          # маркер неразрешённого внешнего имени
TABLE_PREFIX = "@table:"      # ребро к таблице БД
FILE_PREFIX = "@file:"        # ребро к файлу (include)
PHP_BUILTIN_PREFIX = "@php:"  # встроенная функция/класс PHP: не потеря, а ядро языка

# Распространённые встроенные функции PHP. Нужны, чтобы отличить «внешнюю зависимость»
# от «не разрешилось из-за отсутствия класса»: первые — норма, второе — сигнал о потере.
PHP_BUILTINS = frozenset(
    """
    isset empty unset echo print printf sprintf vsprintf implode explode trim ltrim rtrim
    str_replace str_contains str_starts_with str_ends_with str_pad str_repeat str_split
    strlen strpos strrpos stripos substr substr_count strtolower strtoupper ucfirst ucwords
    lcfirst nl2br htmlspecialchars htmlspecialchars_decode html_entity_decode strip_tags
    array_map array_filter array_reduce array_keys array_values array_merge array_merge_recursive
    array_slice array_splice array_push array_pop array_shift array_unshift array_search
    array_key_exists in_array array_flip array_unique array_combine array_column array_fill
    array_chunk array_diff array_intersect array_sum array_walk array_walk_recursive usort uasort
    uksort ksort asort arsort sort rsort count sizeof range compact extract
    json_encode json_decode json_last_error json_last_error_msg
    file_get_contents file_put_contents file_exists is_file is_dir is_readable is_writable
    is_executable filesize mkdir rmdir unlink rename copy basename dirname pathinfo realpath glob
    scandir fopen fclose fwrite fread fgets feof fflush file filemtime touch chmod tempnam
    sys_get_temp_dir disk_free_space disk_total_space
    preg_match preg_match_all preg_replace preg_replace_callback preg_split preg_quote preg_grep
    is_array is_string is_int is_integer is_float is_double is_bool is_null is_numeric is_object
    is_callable is_iterable is_countable intval floatval strval boolval settype gettype
    max min abs round floor ceil pow sqrt intdiv number_format rand random_int mt_rand
    time microtime mktime strtotime date gmdate date_default_timezone_set
    sleep usleep memory_get_usage memory_get_peak_usage gc_collect_cycles
    error_log trigger_error set_error_handler set_exception_handler
    call_user_func call_user_func_array function_exists class_exists method_exists property_exists
    class_implements class_parents get_class get_parent_class get_called_class
    func_get_args func_num_args spl_autoload_register header http_response_code setcookie
    filter_var filter_input parse_url http_build_query urlencode urldecode rawurlencode
    session_start session_regenerate_id session_destroy base64_encode base64_decode
    md5 sha1 hash hash_hmac random_bytes bin2hex openssl_random_pseudo_bytes
    str_pad strrev strtr wordwrap mb_strlen mb_substr mb_strtolower mb_strtoupper
    mb_stripos mb_strpos iconv mb_convert_encoding mb_internal_encoding
    gzencode gzdecode gzcompress gzuncompress serialize unserialize
    var_export var_dump print_r assert array_is_list str_contains
    array_flip arsort array_multisort array_pad array_product array_rand
    ob_start ob_get_clean ob_end_clean flush ignore_user_abort set_time_limit
    define defined constant microtime curl_init curl_setopt curl_exec curl_close
    PDO mysqli_connect pg_connect
    """.split()
)

# Встроенные классы и интерфейсы PHP — их не надо искать в проекте.
PHP_BUILTIN_CLASSES = frozenset(
    """
    PDO PDOStatement mysqli Exception Error Throwable RuntimeException LogicException
    InvalidArgumentException OutOfRangeException UnexpectedValueException ArrayAccess
    Countable Iterator IteratorAggregate Traversable Serializable JsonSerializable
    DateTime DateTimeImmutable DateInterval DateTimeZone stdClass Closure Generator
    ArrayObject SplStack SplQueue SplObjectStorage WeakMap Stringable SessionHandlerInterface
    Throwable TypeError ValueError ArgumentCountError DivisionByZeroError
    """.split()
)

SUPERGLOBALS = (
    "$_GET", "$_POST", "$_REQUEST", "$_COOKIE", "$_FILES",
    "$_SERVER", "$_SESSION", "$_ENV", "$GLOBALS",
)

SQL_RE = re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+[`\"']?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)
# Виды узлов для слоя оформления. Объявлены здесь, а не в css_html.py, потому что
# использовать их должен и PHP-извлекатель: интерфейс проекта собирается внутри
# PHP-шаблонов, и без разбора inline-разметки использования классов из PHP
# не видны. Именно так терялись 32 класса подсистемы agreements: они есть в
# разметке внутри PHP, а правил для них в стилях нет.
CLASS_KIND = "css_class"
NO_FILE = ""

# Атрибут class в разметке внутри PHP или в чистом HTML.
MARKUP_CLASS_RE = re.compile(r"""class\s*=\s*["']([^"']*)["']""")
MARKUP_CLASS_NAME_RE = re.compile(r"-?[A-Za-z_][A-Za-z0-9_-]*")


def class_node_id(name: str) -> str:
    """Идентификатор узла класса. Один на все файлы — по имени.

    Почему без файла в ключе: использование класса в разметке не знает, в каком
    файле он определён. Если бы файл входил в ключ, определение (в CSS) и
    использование (в разметке) давали бы разные узлы и никогда не сопоставлялись,
    а именно сопоставление и нужно: вопрос «где определён и где использован».
    """
    return util.stable_id(CLASS_KIND, name, NO_FILE)


SQL_WRITE_RE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\b", re.IGNORECASE)

# U4: вид доступа к таблице. Различать надо не только «запись/не запись»,
# но и вид операции: INSERT требует полного набора колонок, а UPDATE — условия.
SQL_ACCESS_RE = re.compile(
    r"\b(?P<kind>INSERT\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM|ALTER\s+TABLE"
    r"|CREATE\s+TABLE|DROP\s+TABLE|TRUNCATE|SELECT)\b",
    re.IGNORECASE,
)


def _sql_access(sql: str) -> str:
    """Вид доступа к таблице из текста запроса: select, insert, update, delete, ddl.

    Возвращается ПЕРВАЯ найденная операция: в одном литерале их может быть
    несколько (подзапрос в SELECT), и первая описывает целевое действие.
    Если ничего не распознано — "unknown", а не "select": выдумывать
    безопасное значение нельзя, неопределённость должна быть видна.
    """
    m = SQL_ACCESS_RE.search(sql)
    if m is None:
        return "unknown"
    kind = " ".join(m.group("kind").upper().split())
    if kind == "SELECT":
        return "select"
    if kind in ("INSERT INTO", "REPLACE INTO"):
        return "insert"
    if kind == "UPDATE":
        return "update"
    if kind == "DELETE FROM":
        return "delete"
    return "ddl"

INCLUDE_TYPES = {
    "include_expression": "include",
    "include_once_expression": "include_once",
    "require_expression": "require",
    "require_once_expression": "require_once",
}

CLASS_TYPES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "trait_declaration": "trait",
    "enum_declaration": "enum",
}

WS = re.compile(r"\s+")


@dataclass
class SymbolRec:
    node_id: str
    kind: str
    name: str
    fqn: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    signature: str | None = None
    parent_fqn: str | None = None
    props: dict = field(default_factory=dict)


@dataclass
class EdgeRec:
    src: str
    dst: str
    kind: str
    provenance: str
    evidence_line: int | None
    confidence: float
    sources: tuple[str, ...] = ("tree-sitter-php",)
    # Свойства ребра. Нужны для передачи признаков, которые иначе теряются:
    # например вид доступа к таблице (select/insert/update) — без него операция
    # table_usage не отвечает на вопрос «где идёт запись» (U4).
    props: dict = field(default_factory=dict)


@dataclass
class TextRec:
    chunk_id: str
    owner_node_id: str | None
    start_line: int
    end_line: int
    text_kind: str
    raw: str


@dataclass
class FileExtraction:
    path: str
    sha256: str
    lines: int
    lang: str = "php"
    parse_status: str = "ok"
    parse_note: str | None = None
    symbols: list[SymbolRec] = field(default_factory=list)
    edges: list[EdgeRec] = field(default_factory=list)
    texts: list[TextRec] = field(default_factory=list)
    sql_literals: list[dict] = field(default_factory=list)
    includes: list[dict] = field(default_factory=list)
    superglobals: list[dict] = field(default_factory=list)
    _pending_calls: list[int] = field(default_factory=list, repr=False)

    @property
    def file_node_id(self) -> str:
        for s in self.symbols:
            if s.kind == "file":
                return s.node_id
        raise KeyError(self.path)

    def edges_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.edges:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    def counts(self) -> dict[str, int]:
        kinds: dict[str, int] = {}
        for s in self.symbols:
            kinds[s.kind] = kinds.get(s.kind, 0) + 1
        return {
            "symbols": len(self.symbols),
            "edges": len(self.edges),
            "texts": len(self.texts),
            "sql_literals": len(self.sql_literals),
            "includes": len(self.includes),
            "superglobals": len(self.superglobals),
            "by_kind": kinds,
            "by_edge": self.edges_by_kind(),
        }


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------


def node_text(data: bytes, node) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def line_of(node) -> int:
    return int(node.start_point[0]) + 1


def end_line_of(node) -> int:
    return int(node.end_point[0]) + 1


def _collapse(text: str, limit: int = 300) -> str:
    out = WS.sub(" ", text).strip()
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _collapse_signature(head: str, params: list[dict], limit: int = 200) -> tuple[str, bool]:
    """Сжать сигнатуру по ГРАНИЦЕ ПАРАМЕТРА, а не по символу (пункт 1.3.1).

    Зачем. Прежняя реализация обрезала текст по 200 символам вслепую. На пилоте
    это задевало 33 конструктора с продвигаемыми свойствами и теряло середину
    списка: агент, строящий вызов, видел первые параметры и не знал об остальных.
    Хуже того, обрезка не сопровождалась никаким признаком.

    Теперь:
      * если сигнатура помещается — возвращается целиком, признак обрезки False;
      * если нет — сохраняются заголовок (модификаторы и имя) и столько
        параметров, сколько помещается, а дальше ставится ЯВНАЯ пометка вида
        `/* … ещё 4 */`. Пометка нужна, чтобы текст нельзя было прочитать как
        полный список;
      * последний параметр показывается всегда, когда он помещается вместе
        с пометкой: он часто несёт значение по умолчанию или вариативность.

    Полный машинный список параметров не теряется никогда — он лежит рядом
    в поле `signature_params`.
    """
    compact = WS.sub(" ", head).strip()
    if len(compact) <= limit or not params:
        return compact if len(compact) <= limit else compact[: limit - 1] + "…", \
            len(compact) > limit

    # Заголовок отделяется от списка параметров первой открывающей скобкой.
    open_idx = compact.find("(")
    if open_idx < 0:
        return compact[: limit - 1] + "…", True
    prefix = compact[: open_idx + 1]

    tail_marks = "…) : …"
    budget = limit - len(prefix) - len(tail_marks) - 12   # запас на пометку
    shown: list[str] = []
    used = 0
    for p in params:
        piece = (p.get("text") or p.get("name") or "").strip()
        if not piece:
            continue
        add = len(piece) + 2
        if used + add > budget and shown:
            break
        shown.append(piece)
        used += add

    if not shown:
        return compact[: limit - 1] + "…", True

    left = len(params) - len(shown)
    if left > 0:
        return f"{prefix} " + ", ".join(shown) + f") /* … ещё {left} */", True
    return f"{prefix} " + ", ".join(shown) + ")", True


# --------------------------------------------------------------------------
# Разбор
# --------------------------------------------------------------------------


class PhpExtractor:
    """Разбор PHP-файлов. Один экземпляр на ревизию (парсер переиспользуется)."""

    def __init__(self, include_roots: list[str] | None = None) -> None:
        self._parser = None
        self.include_roots = list(include_roots or [])
        self._data: bytes = b""
        self._res: FileExtraction | None = None
        self._declarations: dict[str, str] = {}
        self._pending: list[tuple[int, str, int, float]] = []
        self._class_of_id: dict[str, str] = {}
        self._func_of_id: dict[str, str] = {}
        self._magic: set[str] = set()
        self._use_map: dict[str, str] = {}          # алиас → полное имя (use ... as ...)
        self._prop_types: dict[str, dict[str, str]] = {}  # класс → {свойство: тип}

    # ---- парсер ----

    def parser(self):
        if self._parser is None:
            from tree_sitter_language_pack import get_parser  # type: ignore

            self._parser = get_parser("php")
        return self._parser

    def grammar_available(self) -> bool:
        try:
            self.parser()
            return True
        except Exception:
            return False

    # ---- один файл ----

    def extract(self, rel_path: str, data: bytes) -> FileExtraction:
        res = FileExtraction(
            path=util.to_posix(rel_path),
            sha256=util.sha256_bytes(data),
            lines=data.count(b"\n") + 1,
        )
        file_node_id = util.stable_id("file", res.path)
        res.symbols.append(
            SymbolRec(
                node_id=file_node_id, kind="file", name=Path(res.path).name, fqn=res.path,
                start_line=1, end_line=res.lines, start_byte=0, end_byte=len(data),
                props={"lang": "php"},
            )
        )

        try:
            tree = self.parser().parse(data)
        except Exception as exc:
            res.parse_status = "failed"
            res.parse_note = f"парсер недоступен: {type(exc).__name__}: {exc}"
            return res

        if tree.root_node.has_error:
            res.parse_status = "partial"
            res.parse_note = "в синтаксическом дереве есть узлы с ошибками"

        # Объявления собираются на ходу; вызовы, не разрешённые сразу, — в отложенный список.
        self._data = data
        self._res = res
        self._declarations = {}
        self._pending = []
        self._class_of_id = {}
        self._func_of_id = {}
        self._magic = set()
        self._use_map = {}
        self._prop_types = {}

        self._visit(tree.root_node, ns="", class_fqn=None, func_fqn=None, container=file_node_id)
        self._resolve_pending()

        # Разбор inline-разметки: интерфейс этого проекта собирается внутри
        # PHP-шаблонов (`<div class="z-agr-card__head">`), а не в отдельных
        # файлах разметки. Без этой ветки использования классов из PHP
        # не видны, и расхождение «класс используется, но правила нет»
        # остаётся незамеченным — именно так терялись 32 класса agreements.
        self._scan_markup_classes(file_node_id)

        # Ребро defines для символов верхнего уровня.
        for s in res.symbols:
            if s.kind in ("function", "class", "interface", "trait", "enum") and s.parent_fqn is None:
                res.edges.append(
                    EdgeRec(file_node_id, s.node_id, "defines", "stated", s.start_line, 1.0)
                )
        return res

    # ---- рекурсивный обход ----

    def _visit(self, node, ns: str, class_fqn: str | None, func_fqn: str | None, container: str) -> None:
        t = node.type

        # --- объявления ---

        if t == "namespace_definition":
            nm = self._field(node, "name")
            new_ns = nm or ""
            sym = self._symbol("namespace", nm or "(global)", new_ns or "(global)", node,
                               parent=None, props={"global": nm is None})
            self._declarations[new_ns] = sym.node_id
            self._edge(container, sym.node_id, "contains", "stated", line_of(node), 1.0)
            body = node.child_by_field_name("body")
            if body is not None:
                self._visit_children(body, new_ns, None, None, sym.node_id)
            else:
                self._visit_children(node, new_ns, None, None, sym.node_id)
            return

        if t in CLASS_TYPES:
            kind = CLASS_TYPES[t]
            nm = self._field(node, "name") or "(anonymous)"
            fqn = f"{ns}\\{nm}" if ns else nm
            parent = self._class_parent(node, ns)
            sym = self._symbol(kind, nm, fqn, node, parent=None,
                               props={"namespace": ns, "parent": parent})
            self._declarations[fqn] = sym.node_id
            self._class_of_id[sym.node_id] = fqn
            self._prop_types[fqn] = self._collect_property_types(node)
            self._edge(container, sym.node_id, "contains", "stated", line_of(node), 1.0)
            if parent:
                self._edge(sym.node_id, f"{EXT_PREFIX}{parent}", "extends", "derived", line_of(node), 0.6)
            body = node.child_by_field_name("body")
            target = body if body is not None else node
            self._visit_children(target, ns, fqn, None, sym.node_id)
            return

        if t in ("method_declaration", "function_definition"):
            is_method = t == "method_declaration"
            nm = self._field(node, "name") or "(anonymous)"
            if is_method:
                owner = class_fqn or ""
                fqn = f"{owner}::{nm}" if owner else nm
                kind = "method"
            else:
                fqn = f"{ns}\\{nm}" if ns else nm
                kind = "function"
            props = {"namespace": ns, "class": class_fqn}
            if is_method:
                props["visibility"] = self._visibility(node)
                props["static"] = self._is_static(node)
                if nm.startswith("__"):
                    self._magic.add(nm)
            sym = self._symbol(kind, nm, fqn, node, parent=class_fqn or func_fqn, props=props)
            self._declarations[fqn] = sym.node_id
            self._func_of_id[sym.node_id] = fqn
            self._edge(container, sym.node_id, "contains", "stated", line_of(node), 1.0)
            body = node.child_by_field_name("body")
            if body is not None:
                self._visit_children(body, ns, class_fqn, fqn, sym.node_id)
            return

        if t == "property_declaration":
            if class_fqn:
                vis = self._visibility(node)
                for el in node.children:
                    if el.type != "property_element":
                        continue
                    vn = next((c for c in el.children if c.type == "variable_name"), None)
                    if vn is None:
                        continue
                    nm = node_text(self._data, vn).lstrip("$")
                    fqn = f"{class_fqn}::{nm}"
                    sym = self._symbol("property", nm, fqn, el, parent=class_fqn, props={"visibility": vis})
                    self._declarations[fqn] = sym.node_id
                    self._edge(container, sym.node_id, "contains", "stated", line_of(el), 1.0)
            self._visit_children(node, ns, class_fqn, func_fqn, container)
            return

        if t == "const_declaration":
            base = class_fqn or ns or None
            for el in node.children:
                if el.type != "const_element":
                    continue
                nm_node = next((c for c in el.children if c.type == "name"), None)
                if nm_node is None:
                    continue
                nm = node_text(self._data, nm_node)
                fqn = f"{base}::{nm}" if base else nm
                sym = self._symbol("constant", nm, fqn, el, parent=base, props={})
                self._declarations[fqn] = sym.node_id
                self._edge(container, sym.node_id, "contains", "stated", line_of(el), 1.0)
            return

        # --- ссылки ---

        if t == "namespace_use_declaration":
            for full, alias in self._use_targets(node):
                if alias:
                    self._use_map[alias] = full
                self._use_map[full.split("\\")[-1]] = full
                self._edge(container, f"{EXT_PREFIX}{full}", "imports", "stated", line_of(node), 1.0)
            return

        if t == "function_call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type in ("name", "qualified_name"):
                callee = node_text(self._data, fn).strip()
                self._call(container, callee, ns, line_of(node), "calls", 1.0)
            else:
                self._edge(container, f"{EXT_PREFIX}dynamic_func", "calls_heuristic", "derived",
                           line_of(node), 0.4)
            self._visit_children(node, ns, class_fqn, func_fqn, container)
            return

        if t == "scoped_call_expression":
            scope = node.child_by_field_name("scope")
            nm = self._field(node, "name")
            if scope is not None and nm:
                scope_txt = node_text(self._data, scope).strip()
                if scope_txt in ("self", "static", "parent") and class_fqn:
                    self._call(container, f"{class_fqn}::{nm}", ns, line_of(node), "calls", 1.0)
                else:
                    # Полное имя через :: — проверяем объявление, а не делаем вид, что не нашли.
                    self._call(container, f"{scope_txt}::{nm}", ns, line_of(node), "calls", 0.9)
            self._visit_children(node, ns, class_fqn, func_fqn, container)
            return

        if t == "member_call_expression":
            nm = self._field(node, "name")
            obj = node.child_by_field_name("object")
            obj_txt = node_text(self._data, obj).strip() if obj is not None else ""
            if nm and obj_txt == "$this" and class_fqn:
                self._call(container, f"{class_fqn}::{nm}", ns, line_of(node), "calls", 1.0)
            elif nm and class_fqn and obj_txt.startswith("$this->"):
                # Тип свойства известен из объявления: это ДОКАЗУЕМАЯ связь, а не догадка.
                # Тип уже приведён к полному имени (через use-алиас), поэтому передаём его
                # с ведущим слэшем: имя абсолютное и не должно дополняться namespace.
                prop = obj_txt[len("$this->") :].strip()
                owner_type = self._prop_types.get(class_fqn, {}).get(prop)
                if owner_type:
                    self._call(
                        container, "\\" + owner_type.lstrip("\\") + "::" + nm,
                        ns, line_of(node), "calls", 1.0,
                    )
                else:
                    self._edge(container, f"{EXT_PREFIX}method:{nm}", "calls_heuristic", "derived",
                               line_of(node), 0.4)
            elif nm:
                # Класс объекта неизвестен → только эвристика по имени метода.
                self._edge(container, f"{EXT_PREFIX}method:{nm}", "calls_heuristic", "derived",
                           line_of(node), 0.4)
            self._visit_children(node, ns, class_fqn, func_fqn, container)
            return

        if t == "object_creation_expression":
            cls = node.child_by_field_name("class")
            if cls is None:
                cls = next((c for c in node.children if c.type in ("name", "qualified_name", "relative_name")), None)
            if cls is not None:
                target = node_text(self._data, cls).strip()
                full = target if "\\" in target else (f"{ns}\\{target}" if ns else target)
                cand = self._declarations.get(full)
                if target in ("self", "static", "parent") and class_fqn:
                    self._edge(container, f"{EXT_PREFIX}{class_fqn}", "instantiates", "derived",
                               line_of(node), 0.6)
                elif cand:
                    self._edge(container, cand, "instantiates", "stated", line_of(node), 1.0)
                else:
                    self._edge(container, f"{EXT_PREFIX}{full}", "instantiates", "derived",
                               line_of(node), 0.6)
            self._visit_children(node, ns, class_fqn, func_fqn, container)
            return

        if t in INCLUDE_TYPES:
            info = self._include_target(node)
            if info is None:
                self._edge(container, f"{EXT_PREFIX}include_dynamic", "imports_heuristic", "derived",
                           line_of(node), 0.4)
                self._res.includes.append(
                    {"path": None, "line": line_of(node), "kind": INCLUDE_TYPES[t], "literal": False}
                )
            else:
                self._edge(container, f"{FILE_PREFIX}{info}", "imports", "derived", line_of(node), 0.9)
                self._res.includes.append(
                    {"path": info, "line": line_of(node), "kind": INCLUDE_TYPES[t], "literal": True}
                )
            self._visit_children(node, ns, class_fqn, func_fqn, container)
            return

        if t in ("string", "encapsed_string"):
            self._handle_string(node, container)
            self._visit_children(node, ns, class_fqn, func_fqn, container)
            return

        if t == "variable_name":
            raw = node_text(self._data, node).strip()
            if raw in SUPERGLOBALS:
                self._res.superglobals.append({"name": raw, "line": line_of(node), "owner": container})
                self._edge(container, f"{EXT_PREFIX}superglobal:{raw}", "reads_superglobal",
                           "stated", line_of(node), 1.0)
            return

        if t == "comment":
            raw = node_text(self._data, node)
            self._res.texts.append(
                TextRec(
                    chunk_id=util.stable_id("text", "comment", util.sha256_text(raw), line_of(node)),
                    owner_node_id=container,
                    start_line=line_of(node),
                    end_line=end_line_of(node),
                    text_kind="docblock" if raw.lstrip().startswith("/**") else "comment",
                    raw=raw,
                )
            )
            return

        self._visit_children(node, ns, class_fqn, func_fqn, container)

    def _visit_children(self, node, ns: str, class_fqn: str | None, func_fqn: str | None, container: str) -> None:
        """Обход детей с учётом namespace-контекста.

        Критично для PHP: при точечной форме объявления идут СОСЕДЯМИ узла namespace,
        а не его детьми. Если это не учесть, весь файл разбирается без префикса и
        классы попадают в граф под короткими именами — из-за этого теряются
        межфайловые связи. Проверено прогоном на фикстуре.
        """
        current_ns = ns
        for child in node.children:
            if child.type == "namespace_definition":
                nm = self._field(child, "name")
                body = child.child_by_field_name("body")
                sym = self._symbol(
                    "namespace", nm or "(global)", nm or "(global)", child,
                    parent=None, props={"global": nm is None},
                )
                self._declarations[nm or ""] = sym.node_id
                self._edge(container, sym.node_id, "contains", "stated", line_of(child), 1.0)
                if body is not None:
                    # Блочная форма: объявления внутри — с новым namespace.
                    self._visit_children(body, nm or "", None, None, sym.node_id)
                else:
                    # Точечная форма: остаток файла относится к этому namespace.
                    current_ns = nm or ""
                    container = sym.node_id
                continue
            self._visit(child, current_ns, class_fqn, func_fqn, container)

    # ---- конструкции ----

    def _handle_string(self, node, container: str) -> None:
        raw = node_text(self._data, node)
        if len(raw) < 8 or not SQL_RE.search(raw):
            return
        tables = sorted({m.group(1) for m in SQL_TABLE_RE.finditer(raw)})
        # U4: признак «чтение или запись» вычисляется здесь и раньше ВЫБРАСЫВАЛСЯ:
        # он попадал в sql_literals, но не в ребро к таблице, поэтому операция
        # table_usage не могла ответить, какие вызовы пишут в журнал. Для правки
        # схемы это ключевой вопрос: важно знать, где именно идёт вставка.
        access = _sql_access(raw)
        self._res.sql_literals.append(
            {
                "line": line_of(node),
                "owner": container,
                "sql": _collapse(raw, 400),
                "tables": tables,
                "write": access != "select",
                "access": access,
            }
        )
        for tbl in tables:
            self._edge(
                container,
                f"{TABLE_PREFIX}{tbl.lower()}",
                "queries",
                "stated",
                line_of(node),
                0.8,
                props={"write": access != "select", "access": access},
            )

    def _call(self, owner: str, callee: str, ns: str, line: int, kind: str, conf: float) -> None:
        """Ребро вызова. Если цель ещё не объявлена — откладывается до конца файла."""
        candidates = []
        if callee.startswith("\\"):
            candidates.append(callee.lstrip("\\"))
        else:
            resolved_alias: str | None = None
            # Разворачивание алиаса use: Config::get → App\Core\Config::get (готовое имя)
            if "::" in callee:
                head, tail = callee.split("::", 1)
                alias = self._use_map.get(head)
                if alias:
                    resolved_alias = f"{alias}::{tail}"
            else:
                alias = self._use_map.get(callee.split("\\")[-1])
                if alias:
                    resolved_alias = alias
            if resolved_alias:
                candidates.append(resolved_alias)
            else:
                # Имя без алиаса трактуется внутри текущего namespace.
                if ns:
                    candidates.append(f"{ns}\\{callee}")
            candidates.append(callee)
        for cand in candidates:
            dst = self._declarations.get(cand)
            if dst:
                self._edge(owner, dst, kind, "stated", line, conf)
                return
        # Встроенная функция PHP — норма, а не потеря: помечается отдельным префиксом.
        base = callee.split("\\")[-1]
        if base.lower() in PHP_BUILTINS:
            self._edge(owner, f"{PHP_BUILTIN_PREFIX}fn:{base}", "calls_builtin", "stated", line, 1.0)
            return
        if base in PHP_BUILTIN_CLASSES:
            self._edge(owner, f"{PHP_BUILTIN_PREFIX}class:{base}", kind, "stated", line, 1.0)
            return
        # Первая форма имени как ожидаемая цель: возможно, объявление ниже по файлу.
        # Сохраняются ВСЕ кандидаты: при отложенном разборе нельзя полагаться только на
        # первый — он может быть неверным (имя внутри текущего namespace), а точное
        # совпадение окажется вторым. Ошибка была поймана тестом на extract-method.
        idx = len(self._res.edges)
        self._edge(owner, f"{EXT_PREFIX}{candidates[0]}", kind, "derived", line, round(conf * 0.9, 2))
        self._pending.append((idx, tuple(candidates), line, conf))

    def _resolve_pending(self) -> None:
        """Второй проход: вызов до объявления становится точным ребром (ФТ-4).

        Перебираются все кандидаты имени, а не только первый: для вызова `$this->m()`
        первым кандидатом идёт имя с префиксом namespace (неверное), а точный fqn —
        следующим. Без перебора связь extract-method терялась.
        """
        for idx, candidates, _line, conf in self._pending:
            dst = None
            for cand in candidates:
                dst = self._declarations.get(cand)
                if dst:
                    break
            if not dst:
                continue
            old = self._res.edges[idx]
            self._res.edges[idx] = EdgeRec(
                src=old.src, dst=dst, kind=old.kind, provenance="stated",
                evidence_line=old.evidence_line, confidence=conf, sources=old.sources,
            )

    # ---- поля и свойства узлов ----

    def _field(self, node, name: str) -> str | None:
        child = node.child_by_field_name(name)
        if child is None:
            return None
        return node_text(self._data, child).strip()

    def _visibility(self, node) -> str:
        for ch in node.children:
            if ch.type in ("visibility_modifier", "modifier", "var_modifier"):
                txt = node_text(self._data, ch).strip().lower()
                if txt in ("public", "private", "protected"):
                    return txt
                if txt == "var":
                    return "public"
        return "public"

    def _is_static(self, node) -> bool:
        return any(
            ch.type in ("static_modifier", "modifier")
            and node_text(self._data, ch).strip().lower() == "static"
            for ch in node.children
        )

    def _symbol(self, kind: str, name: str, fqn: str, node, parent: str | None, props: dict) -> SymbolRec:
        body = self._data[node.start_byte : node.end_byte]
        merged = dict(props or {})
        # body_sha — хэш текста символа. Нужен журналу расхождений: без него изменение
        # тела внутри тех же границ строк (частый случай) оставалось бы незамеченным.
        merged["body_sha"] = util.sha256_bytes(body)[:16]

        # Порядок важен: параметры разбираются ДО сигнатуры, чтобы сжатие шло
        # по границе параметра, а признак обрезки был фактическим (пункт 1.3.1).
        params_list: list[dict] = []
        params_truncated = False
        if kind in ("function", "method"):
            params_list, params_truncated = self._signature_params(node)

        sym = SymbolRec(
            node_id=util.stable_id(kind, fqn, self._res.path, line_of(node)),
            kind=kind,
            name=name,
            fqn=fqn,
            start_line=line_of(node),
            end_line=end_line_of(node),
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            signature=self._signature(node, params_list),
            parent_fqn=parent,
            props=merged,
        )
        # U3: машинный список параметров кладётся в props узла. Хранится в графе
        # как JSON и отдаётся операцией symbol рядом с текстовой сигнатурой:
        # текст удобен для чтения, структура — для построения вызова.
        if params_list or params_truncated:
            sym.props["signature_params"] = params_list
            # Признак обрезки — по фактическому тексту: он выставлен, если сжатие
            # сигнатуры что-то убрало (раньше здесь была приблизительная оценка).
            sig_text = sym.signature or ""
            sym.props["signature_truncated"] = bool(
                "… ещё" in sig_text or sig_text.endswith("…")
            )
        self._res.symbols.append(sym)
        return sym

    def _scan_markup_classes(self, file_node_id: str) -> None:
        """Найти классы оформления в inline-разметке PHP-файла.

        Что ищется: атрибут `class="..."` в тексте файла. Ищется по СЫРОМУ тексту,
        а не по дереву разбора: разметка внутри PHP для tree-sitter — просто
        строковые литералы и текст между `?> ... <?php`, и добраться до атрибутов
        через дерево дороже, чем найти их прямо в тексте.

        Почему это вообще нужно. В этом проекте интерфейс собирается в PHP:
        `agreements/card.php`, `templates/**.htm` содержат готовые блоки HTML.
        Пока такие классы не попадали в индекс, нельзя было заметить, что для
        32 классов подсистемы согласований НЕТ НИ ОДНОГО правила в стилях —
        страницы работали и выглядели «как-то» за счёт оформления по умолчанию.

        Ложные срабатывания отсекаются: имена с суффиксом-конкатенацией
        (`z-agr-status--<?= $s ?>` даёт обрубок `z-agr-status--`) отбрасываются,
        потому что классом такой обрубок не является.
        """
        text = self._data.decode("utf-8", errors="replace")
        seen: set[str] = set()
        dynamic: set[str] = set()
        for m in MARKUP_CLASS_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            for name in m.group(1).split():
                if not MARKUP_CLASS_NAME_RE.fullmatch(name):
                    continue
                if name.endswith("-") or name.endswith("_"):
                    dynamic.add(name)
                    continue
                node_id = class_node_id(name)
                if name not in seen:
                    seen.add(name)
                    self._res.symbols.append(
                        SymbolRec(
                            node_id=node_id, kind=CLASS_KIND, name=name,
                            fqn=f"css:{name}", start_line=line, end_line=line,
                            start_byte=0, end_byte=0, signature=None,
                            parent_fqn=None, props={"used_only": True},
                        )
                    )
                self._res.edges.append(
                    EdgeRec(
                        src=file_node_id, dst=node_id, kind="uses_class",
                        provenance="stated", evidence_line=line, confidence=1.0,
                        props={"in": self._res.path},
                    )
                )
        if dynamic:
            self._res.texts.append(
                TextRec(
                    chunk_id=util.stable_id("dyn", self._res.path, len(dynamic)),
                    owner_node_id=None, start_line=1, end_line=1,
                    text_kind="note",
                    raw="dynamic_classes:" + ",".join(sorted(dynamic)),
                )
            )

    def _signature(self, node, params: list[dict] | None = None) -> str | None:
        """Текстовая сигнатура символа.

        Если передан машинный список параметров, обрезка идёт по границе
        параметра с явной пометкой (см. `_collapse_signature`). Без списка
        (свойства, константы) остаётся прежнее поведение.
        """
        body = node.child_by_field_name("body")
        if body is None or body.start_byte <= node.start_byte:
            return _collapse(node_text(self._data, node), 200)
        head = self._data[node.start_byte : body.start_byte].decode("utf-8", errors="replace")
        if params:
            text, _ = _collapse_signature(head, params, 200)
            return text
        return _collapse(head, 200)

    def _signature_params(self, node) -> tuple[list[dict], bool]:
        """Машинный список параметров функции (U3).

        Зачем. Текстовая сигнатура обрезается по 200 символам (см. `_signature`),
        причём **молча**: при большом числе параметров они теряются в середине.
        Агент, строящий вызов по такой сигнатуре, путает порядок аргументов.
        На этом корпусе обрезка не срабатывает (максимум 181 символ), но запас
        всего 19 символов — один метод с восемью параметрами её включит.

        Возвращает пару (список параметров, признак обрезки). Каждый параметр —
        словарь с полями: name, type, optional, default, by_ref, variadic.
        Если поле не удалось разобрать, оно имеет значение None — выдумывать
        типы нельзя, неполнота должна быть видна.

        Разбор идёт по узлу formal_parameters: так типы и значения по умолчанию
        берутся из AST, а не вылавливаются регулярками из текста.
        """
        params_node = node.child_by_field_name("parameters")
        if params_node is None:
            # Узел параметров не найден — это норма для свойств и констант.
            return [], False

        out: list[dict] = []
        for child in params_node.children:
            if child.type not in ("simple_parameter", "variadic_parameter",
                                  "property_promotion_parameter"):
                continue
            name_node = child.child_by_field_name("name")
            name = node_text(self._data, name_node).strip() if name_node else ""
            if not name:
                continue

            type_node = child.child_by_field_name("type")
            type_text = node_text(self._data, type_node).strip() if type_node else None

            default_node = child.child_by_field_name("default_value")
            default_text = (
                _collapse(node_text(self._data, default_node).strip(), 60)
                if default_node else None
            )

            raw = node_text(self._data, child)
            out.append({
                "name": name,
                "type": type_text or None,
                "optional": default_node is not None,
                "default": default_text,
                # Передача по ссылке и вариативность меняют способ вызова —
                # без них агент может построить синтаксически верный, но неверный вызов.
                "by_ref": raw.strip().startswith("&")
                          or f"&{name}" in raw.replace(" ", ""),
                "variadic": child.type == "variadic_parameter",
                # Текст параметра целиком: нужен для сжатия сигнатуры по границе
                # параметра (пункт 1.3.1) и для показа атрибутов PHP 8.
                "text": _collapse(raw.strip(), 120),
            })

        # Признак обрезки: текстовая сигнатура не содержит последнего параметра.
        full_head = ""
        body = node.child_by_field_name("body")
        if body is not None and body.start_byte > node.start_byte:
            full_head = self._data[node.start_byte : body.start_byte].decode(
                "utf-8", errors="replace"
            )
        truncated = bool(out) and (
            len(full_head) > 200 and out[-1]["name"] not in _collapse(full_head, 200)
        )
        return out, truncated

    def _class_parent(self, node, ns: str) -> str | None:
        for ch in node.children:
            if ch.type in ("base_clause", "class_interface_clause"):
                for sub in ch.children:
                    if sub.type in ("name", "qualified_name"):
                        p = node_text(self._data, sub).strip()
                        return p if "\\" in p else (f"{ns}\\{p}" if ns else p)
        return None

    def _use_targets(self, node) -> list[tuple[str, str | None]]:
        """Цели use: (полное имя, алиас или None)."""
        out: list[tuple[str, str | None]] = []
        for ch in node.children:
            if ch.type == "namespace_use_clause":
                names = [s for s in ch.children if s.type in ("qualified_name", "name")]
                if not names:
                    continue
                full = node_text(self._data, names[0]).lstrip("\\").strip()
                alias: str | None = None
                for sub in ch.children:
                    if sub.type == "namespace_aliasing_clause":
                        for inner in sub.children:
                            if inner.type in ("name", "qualified_name"):
                                alias = node_text(self._data, inner).strip()
                                break
                out.append((full, alias))
            elif ch.type in ("qualified_name", "name"):
                out.append((node_text(self._data, ch).lstrip("\\").strip(), None))
        return out

    # ---- вывод типов свойств ----

    def _collect_property_types(self, class_node) -> dict[str, str]:
        """Карта «свойство класса → тип» по объявлениям и продвинутым параметрам.

        Для чего: `$this->config->get()` — самый частый шаблон прикладного PHP.
        Без типов такие вызовы остаются догадками, с типами — доказанными связями,
        то есть impact-анализ перестаёт терять основную массу рёбер.
        """
        out: dict[str, str] = {}
        body = class_node.child_by_field_name("body")
        scope = body if body is not None else class_node

        def scan(node) -> None:
            t = node.type
            if t == "property_declaration":
                type_node = node.child_by_field_name("type")
                if type_node is not None:
                    tname = self._normalize_type(node_text(self._data, type_node))
                    if tname:
                        for el in node.children:
                            if el.type != "property_element":
                                continue
                            vn = next((c for c in el.children if c.type == "variable_name"), None)
                            if vn is not None:
                                out[node_text(self._data, vn).lstrip("$")] = tname
            elif t in ("method_declaration", "function_definition"):
                nm = self._field(node, "name") or ""
                if nm in ("__construct",) or t == "method_declaration":
                    params = node.child_by_field_name("parameters")
                    if params is not None:
                        for p in params.children:
                            if p.type not in ("simple_parameter", "variadic_parameter", "property_promotion_parameter"):
                                continue
                            has_visibility = any(
                                c.type in ("visibility_modifier", "modifier") for c in p.children
                            )
                            if not has_visibility:
                                continue
                            vn = next((c for c in p.children if c.type == "variable_name"), None)
                            tnode = p.child_by_field_name("type")
                            if vn is None or tnode is None:
                                continue
                            tname = self._normalize_type(node_text(self._data, tnode))
                            if tname:
                                out[node_text(self._data, vn).lstrip("$")] = tname
                # Вложенные функции класса не разбираем: только прямые члены.
                return
            for child in node.children:
                scan(child)

        scan(scope)
        return out

    def _normalize_type(self, raw: str) -> str | None:
        """Тип из объявления → имя класса для связи.

        Пропускаются скалярные/встроенные типы (int, string, array, ...) и объединения
        с ними: связь нужна только к объявленным классам проекта или известным из use.
        """
        text = raw.strip().lstrip("?")
        if not text:
            return None
        skip = {
            "int", "float", "string", "bool", "array", "iterable", "callable",
            "mixed", "void", "null", "false", "true", "never", "object", "static",
            "self", "parent", "this",
        }
        for part in re.split(r"[|&]", text):
            cand = part.strip().lstrip("?")
            if not cand or cand.lower() in skip:
                continue
            if "<" in cand:
                continue
            alias = self._use_map.get(cand.split("\\")[-1])
            return alias or cand.lstrip("\\")
        return None

    def _include_target(self, node) -> str | None:
        """Литеральный путь include/require. None — путь динамический."""
        target = None
        for ch in node.children:
            if ch.type in ("string", "encapsed_string"):
                target = ch
                break
            if ch.type == "parenthesized_expression":
                target = next((s for s in ch.children if s.type == "string"), None)
                if target is not None:
                    break
        if target is None:
            return None
        raw = node_text(self._data, target).strip().strip("'\"").strip()
        if not raw:
            return None
        return util.to_posix(raw).lstrip("/")

    def _edge(self, src: str, dst: str, kind: str, prov: str, line: int | None,
              conf: float, props: dict | None = None) -> None:
        """Записать ребро. props передаются в граф и доступны операциям."""
        self._res.edges.append(
            EdgeRec(src=src, dst=dst, kind=kind, provenance=prov,
                    evidence_line=line, confidence=conf, props=dict(props or {}))
        )


# --------------------------------------------------------------------------
# Чанки для поиска (M4)
# --------------------------------------------------------------------------


@dataclass
class ChunkRec:
    chunk_id: str
    source_kind: str
    path: str
    node_id: str | None
    start_line: int
    end_line: int
    lang: str
    text: str
    text_hash: str
    label: str = ""
    tokens: int = 0


def build_chunks(
    res: FileExtraction,
    data: bytes,
    *,
    by_symbol: bool = True,
    max_lines: int = 120,
    min_lines: int = 3,
) -> list[ChunkRec]:
    """Чанки для лексического и векторного индекса.

    Три случая, каждый проверен на реальном корпусе
    (tools/probe_chunks.py на пилотный PHP-портал):

    1. Символы есть — один чанк на символ; крупные символы режутся окнами
       (у пилота есть метод на 1217 строк).

    2. СИМВОЛОВ НЕТ — процедурный PHP: страницы и скрипты часто не содержат
       ни одной функции. На пилотный PHP-портал таких файлов 20: mail/index.php,
       admin/*.php, config.php и другие. Прежняя реализация делала из такого файла
       ОДИН чанк на весь файл (до 11 331 токена, строки 1..304) — он размывал
       поиск: слабо совпадал со всем подряд и в гибридном ранжировании вылезал
       наверх. Теперь такой файл режется окнами по max_lines строк — те же окна
       применяются и к очень длинным «символам».

    3. by_symbol выключен — тот же оконный режим по всему файлу.

    Окно всегда несёт заголовок (сигнатуру символа или путь файла), чтобы не
    терять контекст на стыке окон.
    """
    if not by_symbol:
        return _chunk_file_whole_or_windows(res, data, max_lines, min_lines)

    targets = [s for s in res.symbols if s.kind not in ("file", "namespace")]
    if not targets:
        # Случай 2: процедурный файл без символов.
        return _chunk_file_whole_or_windows(res, data, max_lines, min_lines)

    chunks: list[ChunkRec] = []
    for s in targets:
        body = data[s.start_byte : s.end_byte].decode("utf-8", errors="replace")
        span = s.end_line - s.start_line + 1
        if span <= max_lines:
            if span < min_lines and s.kind not in CLASS_TYPES.values():
                continue
            chunks.append(
                _mk_chunk(res.path, s.node_id, s.start_line, s.end_line, body, "symbol", "php",
                          label=s.fqn, tokens_kind=s.kind)
            )
            continue
        # Крупный символ — окна с заголовком сигнатуры.
        head = s.signature or s.fqn or s.kind
        chunks.extend(
            _split_windows(
                body, res.path, s.node_id, s.start_line, max_lines, min_lines, head, "symbol"
            )
        )
    return chunks


def _chunk_file_whole_or_windows(
    res: FileExtraction, data: bytes, max_lines: int, min_lines: int, source_kind: str = "file"
) -> list[ChunkRec]:
    """Чанки для файла без символов либо при отключённом символьном режиме.

    Файл помещается в окно — один чанк. Не помещается — режется окнами с
    заголовком (путь файла), иначе получается один размытый чанк на весь файл.
    """
    text = data.decode("utf-8", errors="replace")
    if res.lines <= max_lines:
        return [_mk_chunk(res.path, res.file_node_id, 1, res.lines, text, source_kind, "php")]
    return _split_windows(
        text, res.path, res.file_node_id, 1, max_lines, min_lines, res.path, source_kind
    )


def _split_windows(
    body: str,
    path: str,
    owner_id: str | None,
    first_line: int,
    max_lines: int,
    min_lines: int,
    head: str,
    source_kind: str,
) -> list[ChunkRec]:
    """Нарезать текст окнами по max_lines строк с перекрытием 10 строк.

    Перекрытие нужно, чтобы конструкция на стыке окон не потерялась сразу в обоих.
    Заголовок окна повторяет сигнатуру (или путь файла) — так по чанку видно,
    к какому символу он относится, даже если окно вырезано из середины тела.
    """
    chunks: list[ChunkRec] = []
    lines = body.splitlines()
    step = max(1, max_lines - 10)
    part_idx = 0
    for start in range(0, len(lines), step):
        piece = lines[start : start + max_lines]
        if not piece:
            break
        if len(piece) < min_lines and part_idx > 0:
            break
        text = f"// {head}\n" + "\n".join(piece)
        chunks.append(
            _mk_chunk(
                path,
                owner_id,
                first_line + start,
                first_line + start + len(piece) - 1,
                text,
                source_kind,
                "php",
                label=f"{head}#{part_idx}",
            )
        )
        part_idx += 1
    return chunks


def _mk_chunk(
    path: str, node_id: str | None, start: int, end: int, text: str,
    source_kind: str, lang: str, label: str = "", tokens_kind: str | None = None,
) -> ChunkRec:
    text_hash = util.sha256_text(text)
    return ChunkRec(
        chunk_id=util.stable_id("chunk", source_kind, path, label, text_hash),
        source_kind=source_kind,
        path=path,
        node_id=node_id,
        start_line=start,
        end_line=end,
        lang=lang,
        text=text,
        text_hash=text_hash,
        label=label,
    )
