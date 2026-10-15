"""Обобщённое извлечение символов и вызовов из языков программирования.

Зачем этот модуль
    До него разбор деревом умел только PHP: `PhpExtractor` написан под грамматику
    PHP и жёстко зашит. Из-за этого .ps1, .sh, .bat, .cs, .pl, .py и другие файлы
    в индекс либо не попадали вовсе, либо шли как текст без структуры — и операции
    `symbol`, `code`, `callers` по ним не работали.

    На сканировании стека выяснилось, что это не мелочь: в каталогах разработки
    1235 файлов Perl, 1337 Python, 1217 C#, 212 shell, 96 PowerShell. Все они
    были невидимы для структурных операций.

Как устроено
    Грамматики всех нужных языков уже есть в tree-sitter-language-pack (проверено:
    26 языков). Разные языки дают разные имена узлов, но смысл у них общий:
    объявление функции, объявление класса, вызов. Поэтому здесь один раз описан
    НАБОР ИМЁН для каждого языка, а обход и построение фактов общие.

Что извлекается
    * узлы: function, method, class, interface, struct, enum, property, constant;
    * рёбра: defines (объявление), contains (вложенность), calls (вызов).

    Виды узлов и рёбер взяты те же, что у PHP-извлекателя: иначе операции фасада
    (callers, impact, slice) не увидят новые языки, потому что обход графа
    фильтрует рёбра по известным видам.

Чего здесь НЕТ и почему
    * типов параметров и возвращаемых значений — их извлечение зависит от языка,
      и для каждого нужен свой разбор; делается отдельно там, где нужно;
    * разбора потока данных — это работа графа свойств кода (Joern), и только
      для тех языков, которые он поддерживает;
    * разбора языков без грамматики (SQL, batch, INI) — они остаются текстовыми.

Ошибки разбора
    Если файл не разобрался (частая история у PowerShell: грамматика спотыкается
    на `1KB`, `[math]::Round`, атрибутах параметров), это НЕ ошибка индексации.
    Файл всё равно попадёт в поиск текстовыми чанками, а в примечаниях будет
    видно, что структура по нему не построена. Выдумывать узлы нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .php_ast import (
    ChunkRec,
    EdgeRec,
    FileExtraction,
    SymbolRec,
    TextRec,
    _collapse,
    _collapse_signature,
    EXT_PREFIX,
    SQL_RE,
    SQL_TABLE_RE,
    TABLE_PREFIX,
    _sql_access,
    file_ref_edges,
    route_edges,
    routes_from_text,
)
from .. import textdec, util


@dataclass
class LangProfile:
    """Описание языка: какие узлы грамматики что означают.

    Атрибуты — наборы имён узлов грамматики. Пустой набор означает, что
    в этом языке такого понятия нет.
    """

    name: str                              # имя грамматики tree-sitter
    title: str                             # название для человека
    functions: tuple[str, ...] = ()         # узлы объявления функции
    methods: tuple[str, ...] = ()           # узлы объявления метода
    classes: tuple[str, ...] = ()           # узлы объявления класса
    interfaces: tuple[str, ...] = ()
    structs: tuple[str, ...] = ()
    enums: tuple[str, ...] = ()
    properties: tuple[str, ...] = ()
    constants: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()             # узлы вызова
    namespaces: tuple[str, ...] = ()        # узлы пространства имён или модуля
    imports: tuple[str, ...] = ()           # узлы подключения
    # Вид, который зависит НЕ от имени узла, а от ключевого слова внутри него.
    #
    # Зачем. В C# грамматика даёт один и тот же тип узла record_declaration
    # и для record class (ссылочный тип), и для record struct (значимый тип).
    # Замер на пробном файле (tools/diag_csharp_record.py) это подтвердил:
    #   public sealed record AlertRule(...)        -> record_declaration
    #   public readonly record struct Point(...)   -> record_declaration
    # По имени узла их различить невозможно. Первая версия профиля отнесла
    # все записи к structs — и в индексе C#-проекта record AlertRule
    # оказался обычной структурой, хотя это ссылочный тип. Разница
    # существенная: по ней судят о копировании vs передаче по ссылке.
    #
    # Формат: тип узла -> (искомое слово, вид при наличии, вид без него).
    keyword_kinds: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    # Узлы, которые обход не должен заходить внутрь: тела уже разобраны как методы.
    body_field: str = "body"
    # Имена полей грамматики для имени и параметров.
    name_field: str = "name"
    params_field: str = "parameters"
    # Узлы внутри объявления класса, в которых лежат имена БАЗОВЫХ классов.
    #
    # Проверено вызовом грамматик (не по памяти) — у каждого языка своё место:
    #   python      argument_list (поле superclasses): Base, Mixin
    #   javascript  class_heritage: extends Base
    #   typescript  class_heritage: extends Base / implements IFace
    #   java        superclass (поле) и super_interfaces (поле interfaces)
    #   csharp      base_list: Base, IFace
    #   cpp         base_class_clause: public Base
    #   ruby        superclass (поле): Base
    #   powershell  у объявления класса базовый класс идёт после двоеточия
    #
    # Имена собираются из потомков этих узлов по видам-идентификаторам
    # (`identifier`, `type_identifier`, `constant`, `qualified_name` и подобным),
    # а не по тексту целиком: текст включал бы ключевые слова (`public`, `extends`)
    # и давал бы несуществующие "классы".
    inherit_nodes: tuple[str, ...] = ()


# Профили языков. Наборы составлены по фактическому разбору файлов из каталогов
# разработки: имена узлов проверены на реальных файлах, а не взяты из документации.
PROFILES: dict[str, LangProfile] = {
    "javascript": LangProfile(
        name="javascript", title="JavaScript",
        functions=("function_declaration", "function_expression",
                   "generator_function_declaration", "function"),
        methods=("method_definition",),
        classes=("class_declaration", "class"),
        inherit_nodes=("class_heritage",),            # class C extends B
        properties=("field_definition", "public_field_definition"),
        calls=("call_expression", "new_expression"),
        imports=("import_statement",),
    ),
    "typescript": LangProfile(
        name="typescript", title="TypeScript",
        functions=("function_declaration", "function_expression",
                   "generator_function_declaration"),
        methods=("method_definition", "method_signature",
                 "abstract_method_signature"),
        classes=("class_declaration", "abstract_class_declaration"),
        inherit_nodes=("class_heritage",),            # extends / implements
        interfaces=("interface_declaration",),
        enums=("enum_declaration",),
        properties=("public_field_definition", "property_signature"),
        calls=("call_expression", "new_expression"),
        imports=("import_statement",),
    ),
    "python": LangProfile(
        name="python", title="Python",
        functions=("function_definition",),
        methods=("function_definition",),   # внутри класса функция = метод
        classes=("class_definition",),
        inherit_nodes=("argument_list",),             # class C(Base, Mixin)
        calls=("call",),
        imports=("import_statement", "import_from_statement"),
    ),
    "csharp": LangProfile(
        name="csharp", title="C#",
        functions=("local_function_statement",),
        methods=("method_declaration", "constructor_declaration",
                 "destructor_declaration", "operator_declaration"),
        classes=("class_declaration",),
        inherit_nodes=("base_list",),                 # class C : Base, IFace
        interfaces=("interface_declaration",),
        structs=("struct_declaration", "record_declaration"),
        enums=("enum_declaration",),
        properties=("property_declaration", "field_declaration"),
        calls=("invocation_expression", "object_creation_expression"),
        namespaces=("namespace_declaration", "file_scoped_namespace_declaration"),
        imports=("using_directive",),
        # record class и record struct дают один тип узла: различаются
        # наличием слова struct в заголовке (проверено замером).
        keyword_kinds={
            "record_declaration": ("struct", "struct", "class"),
        },
    ),
    "perl": LangProfile(
        name="perl", title="Perl",
        functions=("subroutine_declaration_statement", "sub"),
        methods=("method_declaration",),
        classes=("package_statement",),
        calls=("function_call_expression", "method_call_expression",
               "call_expression"),
        namespaces=("package_statement",),
        imports=("use_statement", "require_expression"),
    ),
    "bash": LangProfile(
        name="bash", title="bash / sh",
        functions=("function_definition",),
        calls=("command",),
        imports=("command",),   # source и . попадают сюда же
    ),
    "powershell": LangProfile(
        name="powershell", title="PowerShell",
        functions=("function_statement",),
        classes=("class_statement",),
        inherit_nodes=("class_statement",),           # class C : Base
        enums=("enum_statement",),
        calls=("command", "invokation_expression"),
        imports=("using_statement",),
    ),
    "ruby": LangProfile(
        name="ruby", title="Ruby",
        functions=("method", "singleton_method"),
        methods=("method", "singleton_method"),
        classes=("class", "module"),
        inherit_nodes=("superclass",),                # class C < Base
        calls=("call", "method_call"),
        imports=("call",),
    ),
    "lua": LangProfile(
        name="lua", title="Lua",
        functions=("function_declaration", "function_definition"),
        calls=("function_call",),
    ),
    "go": LangProfile(
        name="go", title="Go",
        functions=("function_declaration",),
        methods=("method_declaration",),
        structs=("type_declaration",),
        calls=("call_expression",),
        imports=("import_declaration",),
    ),
    "java": LangProfile(
        name="java", title="Java",
        methods=("method_declaration", "constructor_declaration"),
        classes=("class_declaration",),
        inherit_nodes=("superclass", "super_interfaces"),  # extends / implements
        interfaces=("interface_declaration",),
        enums=("enum_declaration",),
        calls=("method_invocation", "object_creation_expression"),
        imports=("import_declaration",),
    ),
    "c": LangProfile(
        name="c", title="C",
        functions=("function_definition",),
        structs=("struct_specifier",),
        calls=("call_expression",),
        imports=("preproc_include",),
    ),
    "cpp": LangProfile(
        name="cpp", title="C++",
        functions=("function_definition",),
        methods=("function_definition",),
        classes=("class_specifier",),
        inherit_nodes=("base_class_clause",),         # class C : public Base
        structs=("struct_specifier",),
        calls=("call_expression",),
        imports=("preproc_include",),
    ),
}


class GenericExtractor:
    """Извлечение символов и вызовов для языка с грамматикой tree-sitter.

    Один экземпляр обслуживает один язык: парсер переиспользуется, потому что
    создание парсера стоит времени.
    """

    def __init__(self, profile: LangProfile) -> None:
        self.profile = profile
        self._parser = None
        self._data: bytes = b""
        self._res: FileExtraction | None = None
        self._declared: dict[str, str] = {}   # имя → node_id объявленного символа
        self._pending: list[tuple[str, str, int, str, str]] = []
        self._containers: list[str] = []      # стек вложенности классов

    def parser(self):
        """Создать парсер лениво: не все прогоны дойдут до этого языка."""
        if self._parser is None:
            from tree_sitter_language_pack import get_parser  # type: ignore

            self._parser = get_parser(self.profile.name)
        return self._parser

    def extract(self, path: str, data: bytes) -> FileExtraction:
        # Кодировка определяется до разбора и до подсчёта строк: для CP1251
        # (Perl-скрипты BWO) текст раньше терялся целиком, для UTF-16 ломался
        # ещё и подсчёт строк (байт 0x0A разделён нулём). sha256 — по исходным
        # байтам: он сверяется со сканом корпуса, и подменять его нельзя.
        raw = data
        text, enc, data = textdec.prepare(raw, path)
        res = FileExtraction(
            path=util.to_posix(path),
            sha256=util.sha256_bytes(raw),
            lines=textdec.line_count(raw, text, enc),
            lang=self.profile.name,
            encoding=enc,
        )
        file_id = util.stable_id("file", res.path, res.path)
        res.symbols.append(
            SymbolRec(
                node_id=file_id, kind="file", name=res.path.rsplit("/", 1)[-1],
                fqn=res.path, start_line=1, end_line=res.lines,
                start_byte=0, end_byte=len(data), props={"lang": self.profile.name},
            )
        )

        try:
            tree = self.parser().parse(data)
        except Exception as exc:
            res.parse_status = "failed"
            res.parse_note = f"парсер недоступен: {type(exc).__name__}: {exc}"
            return res

        if tree.root_node.has_error:
            # Неполный разбор — не ошибка индексации: файл попадёт в поиск
            # текстовыми чанками, а структура будет неполной. Скрывать это нельзя.
            res.parse_status = "partial"
            res.parse_note = "в дереве есть узлы с ошибками: структура неполная"

        self._data = data
        self._res = res
        self._declared = {}
        self._pending = []
        self._containers = []
        # Perl: какие имена объявлены в этом файле и с каким знаком (`$`, `@`, `%`).
        # Нужен до обхода, потому что приведение знака делается в момент
        # использования, а объявление может стоять ниже по файлу.
        self._perl_declared = (
            self._collect_perl_declarations(tree.root_node)
            if self.profile.name == "perl" else {}
        )
        self._visit(tree.root_node, parent=None, container=file_id)

        # Отложенные вызовы привязываются после обхода: цель может быть
        # объявлена позже места вызова.
        self._resolve_pending()

        # P1-3: точки входа из скриптов. У JavaScript нет ни shebang, ни
        # `action=`, но адрес запроса лежит в `fetch('...')` и `$.get('...')`,
        # и без этого разбора файл-обработчик не считался точкой входа.
        if self.profile.name in ("javascript", "typescript"):
            text = data.decode("utf-8", errors="replace")
            res.routes = routes_from_text(res.path, text)
            route_edges(res, file_id)
            # Ссылки на файлы строковыми литералами (P1-2): в JS рабочий файл
            # часто назван просто строкой (`var ENDPOINT = 'php/Files7.php'`).
            file_ref_edges(res, file_id, text)

        # Ребро defines для символов верхнего уровня.
        for sym in res.symbols:
            if sym.kind in ("function", "class", "interface", "struct", "enum",
                            "module") and sym.parent_fqn is None:
                res.edges.append(
                    EdgeRec(file_id, sym.node_id, "defines", "stated",
                            sym.start_line, 1.0)
                )
        return res

    # ---- обход дерева ----

    def _text(self, node) -> str:
        return self._data[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace"
        )

    def _name_of(self, node) -> str | None:
        """Имя символа: по полю имени, затем по первому подходящему ребёнку."""
        child = node.child_by_field_name(self.profile.name_field)
        if child is not None:
            text = self._text(child).strip()
            if text:
                return text
        # Запасной путь: первый ребёнок-идентификатор.
        #
        # `simple_name` добавлено по факту: в грамматике PowerShell имя класса и
        # имя базового класса — узлы `simple_name` (`class Child : Base`), и без
        # него ЧТЕНИЕ имени возвращало None, то есть класс не попадал в индекс
        # вообще. Замерено: до правки `extract("t.ps1", b"class Child : Base {}")`
        # давал единственный символ — файл, а класс отсутствовал.
        for ch in node.children:
            if ch.type in ("identifier", "name", "word", "variable_name",
                           "constant", "type_identifier", "simple_identifier",
                           "simple_name"):
                text = self._text(ch).strip()
                if text:
                    return text
        return None

    def _kind_of(self, node) -> str | None:
        """Вид символа по типу узла грамматики.

        Раньше принимал только тип узла. Теперь принимает узел: есть случаи,
        когда вид зависит не от имени узла, а от содержимого — как с record
        в C# (record class против record struct).
        """
        p = self.profile
        node_type = node.type

        # Самый точный случай: вид зависит от ключевого слова внутри узла.
        if node_type in p.keyword_kinds:
            keyword, kind_when_found, kind_when_absent = p.keyword_kinds[node_type]
            head = self._signature_head(node)
            # Слово ищется как отдельное: "struct" не должно ловить,
            # например, идентификатор "StructBuilder".
            tokens = {
                ch.strip("(),;{}=<>[]").lower()
                for ch in head.replace("\t", " ").split()
            }
            return kind_when_found if keyword in tokens else kind_when_absent

        if node_type in p.functions:
            return "method" if self._containers else "function"
        if node_type in p.methods:
            # Внутри класса любая функция — метод, даже если грамматика
            # не различает их (Python, C++).
            return "method" if self._containers else "function"
        if node_type in p.classes:
            return "class"
        if node_type in p.interfaces:
            return "interface"
        if node_type in p.structs:
            return "struct"
        if node_type in p.enums:
            return "enum"
        if node_type in p.properties:
            return "property"
        if node_type in p.constants:
            return "constant"
        if node_type in p.namespaces:
            return "module"
        return None

    def _visit(self, node, parent: str | None, container: str) -> None:
        """Рекурсивный обход. Символы записываются, вызовы откладываются."""
        kind = self._kind_of(node)

        if kind is not None:
            name = self._name_of(node)
            if name:
                node_id = util.stable_id(
                    kind, name, self._res.path, node.start_point[0] + 1
                )
                fqn = f"{self._res.path}:{name}"
                res = self._res
                res.symbols.append(
                    SymbolRec(
                        node_id=node_id,
                        kind=kind,
                        name=name,
                        fqn=fqn,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        signature=_collapse(self._signature_head(node), 300),
                        parent_fqn=parent,
                        props={
                            "lang": self.profile.name,
                            "grammar_node": node.type,
                            "body_sha": util.sha256_bytes(
                                self._data[node.start_byte : node.end_byte]
                            )[:16],
                        },
                    )
                )
                res.edges.append(
                    EdgeRec(container, node_id, "contains", "stated",
                            node.start_point[0] + 1, 1.0)
                )
                self._declared[name] = node_id
                # Классы становятся контейнерами: их содержимое — методы.
                if kind in ("class", "interface", "struct", "enum", "module"):
                    self._containers.append(node_id)
                    # НАСЛЕДОВАНИЕ. Базовые классы извлекаются ДО обхода тела:
                    # ребро наследования — факт об объявлении, а не о содержимом.
                    for base_name, base_line in self._base_names(node):
                        res.edges.append(
                            EdgeRec(node_id, f"{EXT_PREFIX}{base_name}", "extends",
                                    "derived", base_line, 0.6)
                        )
                    self._visit_children(node, parent=fqn, container=node_id)
                    self._containers.pop()
                else:
                    self._visit_children(node, parent=fqn, container=node_id)
                return

        # Вызовы записываются от имени ближайшего контейнера.
        if node.type in self.profile.calls:
            self._record_call(node, container)
            # Внутрь вызова заходим: аргументы содержат вложенные вызовы.
            self._visit_children(node, parent=parent, container=container)
            return

        # --- P1-3: полиглот ---
        # Perl: пакетные переменные (`our %hFORMAT`, `my $FORMAT`) и SQL в
        # строках и heredoc. До этой ветки `%hFORMAT` в индексе не было вовсе:
        # профиль языка описывал подпрограммы и вызовы, а переменные — нет,
        # и вопрос «где задаётся формат импорта» упирался в чтение файла.
        if self.profile.name == "perl":
            if node.type == "variable_declaration":
                self._perl_variable(node, container)
                return
            if node.type in ("scalar", "hash", "array", "container_variable"):
                self._perl_variable_use(node, container)
                self._visit_children(node, parent=parent, container=container)
                return
            if node.type in ("heredoc_content", "string_literal",
                             "interpolated_string_literal"):
                self._sql_from_text(node, container)
                self._visit_children(node, parent=parent, container=container)
                return

        self._visit_children(node, parent=parent, container=container)

    # ---- P1-3: Perl и SQL вне PHP ----

    def _collect_perl_declarations(self, root) -> dict[str, set[str]]:
        """Собрать объявленные в файле имена Perl и их знаки (`$`, `@`, `%`).

        Нужен ДО обхода: в обращении к элементу хэша грамматика пишет
        `$hFORMAT{...}` даже тогда, когда переменная объявлена как `%hFORMAT`.
        Опираться на порядок объявлений нельзя — они бывают и ниже места
        использования, поэтому собирается заранее.
        """
        found: dict[str, set[str]] = {}

        def walk(node) -> None:
            if node.type == "variable_declaration":
                for ch in node.children:
                    if ch.type in ("scalar", "hash", "array"):
                        raw = self._text(ch).strip()
                        if len(raw) > 1 and raw[0] in "$@%":
                            found.setdefault(raw[1:], set()).add(raw[0])
            for ch in node.children:
                walk(ch)

        walk(root)
        return found

    def _perl_variable(self, node, container: str) -> None:
        """Объявление пакетной переменной Perl: `our %hFORMAT`, `my $FORMAT`.

        Объявление — ФАКТ со своим адресом, поэтому узел отдельный и
        принадлежит файлу. Использования идут в общий узел по имени (см.
        `_perl_variable_use`): так «где объявлено» и «где используется»
        отвечают на разные вопросы, и оба — со ссылками.
        """
        scope = next((c.type for c in node.children
                      if c.type in ("our", "my", "local", "state")), "")
        name_node = next((c for c in node.children
                          if c.type in ("scalar", "hash", "array")), None)
        if name_node is None:
            return
        name = self._text(name_node).strip()
        if not name:
            return
        line = node.start_point[0] + 1
        node_id = util.stable_id("perlvar_decl", self._res.path, name, line)
        self._res.symbols.append(
            SymbolRec(
                node_id=node_id, kind="variable", name=name,
                fqn=f"{self._res.path}::{name}", start_line=line, end_line=line,
                start_byte=node.start_byte, end_byte=node.end_byte,
                signature=_collapse(f"{scope} {name}".strip(), 120),
                parent_fqn=None,
                props={"lang": "perl", "sigil": name[:1], "scope": scope or "my"},
            )
        )
        self._res.edges.append(
            EdgeRec(container, node_id, "declares", "stated", line, 1.0)
        )

    def _perl_variable_use(self, node, container: str) -> None:
        """Использование переменной Perl: общий узел по имени + ребро чтения.

        Особый случай — `container_variable`: в обращении к элементу хэша
        грамматика пишет `$hFORMAT{...}`, хотя переменная объявлена как
        `%hFORMAT` (проверено вызовом грамматики: `hash_element_expression`
        → `container_variable` со знаком `$`). Без приведения знака
        использование пакетной переменной не связывалось с её объявлением:
        в индексе появлялся `$hFORMAT`, которого в файле нет.

        Знак приводится по ОБЪЯВЛЕНИЯМ этого же файла: `%hFORMAT` — если
        объявлен хэш, `@a` — если массив. Если соответствующего объявления
        нет (например, это ссылка на хэш `$self->{...}`), знак остаётся `$`:
        выдумывать имя `%self` нельзя.
        """
        if node.type == "container_variable":
            raw = self._text(node).strip()
            base = raw[1:] if raw[:1] in "$@%" else raw
            parent = getattr(node, "parent", None)
            ptype = getattr(parent, "type", "")
            want = "%" if ptype == "hash_element_expression" else (
                "@" if ptype == "array_element_expression" else "$")
            declared = self._perl_declared.get(base, set())
            sigil = want if want in declared else ("$" if "$" in declared else want)
            name = f"{sigil}{base}"
        else:
            name = self._text(node).strip()
        if not name or name in ("$_", "@_", "$0", "$1", "$@"):
            # Служебные переменные Perl: общего узла на них не заводим,
            # иначе граф наполняется связями «все файлы читают $_».
            return
        line = node.start_point[0] + 1
        node_id = util.stable_id("perlvar", name)
        self._res.symbols.append(
            SymbolRec(
                node_id=node_id, kind="variable", name=name, fqn=f"perl:{name}",
                start_line=line, end_line=line, start_byte=0, end_byte=0,
                signature=None, parent_fqn=None,
                props={"lang": "perl", "shared": True, "sigil": name[:1]},
            )
        )
        self._res.edges.append(
            EdgeRec(container, node_id, "reads_var", "stated", line, 1.0)
        )

    def _sql_from_text(self, node, container: str) -> None:
        """SQL из строкового литерала или heredoc любого языка с профилем.

        Нужно для Perl: `$dbh->do("UPDATE orders SET x=1")` и
        `<<'SQL' ... INSERT INTO ... SQL` — обычная для BWO запись в базу.
        Разбор общий с PHP (образцы и вид доступа берутся из `php_ast`),
        чтобы «кто пишет в таблицу» отвечал одинаково на обоих языках.
        """
        raw = self._text(node)
        if len(raw) < 8 or not SQL_RE.search(raw):
            return
        tables = sorted({m.group(1) for m in SQL_TABLE_RE.finditer(raw)})
        access = _sql_access(raw)
        line = node.start_point[0] + 1
        # Как запрос попал в СУБД: `$dbh->do(...)` — прямой вызов,
        # `$dbh->prepare(...)` — подготовленное выражение. Поле нужно, чтобы
        # «кто пишет» не смешивал подготовку и выполнение (P1-4).
        via = "literal"
        p = getattr(node, "parent", None)
        depth = 0
        while p is not None and depth < 4:
            if p.type == "method_call_expression":
                nm = next((self._text(c).strip() for c in p.children
                           if c.type == "method"), "")
                low = nm.lower()
                if low == "prepare":
                    via = "prepared"
                    break
                if low in ("do", "selectall", "selectrow", "selectcol"):
                    via = f"direct:{low}"
                    break
            p = getattr(p, "parent", None)
            depth += 1
        self._res.sql_literals.append({
            "line": line, "owner": container,
            "sql": _collapse(raw.strip("'\""), 400),
            "tables": tables, "write": access != "select", "access": access,
            "via": via,
        })
        for tbl in tables:
            self._res.edges.append(
                EdgeRec(container, f"{TABLE_PREFIX}{tbl.lower()}", "queries", "stated",
                        line, 0.8, props={"write": access != "select", "access": access,
                                          "sql": _collapse(raw.strip("'\""), 200),
                                          "via": via})
            )

    def _base_names(self, node) -> list[tuple[str, int]]:
        """Имена БАЗОВЫХ классов из объявления класса.

        Возвращает список (имя, строка). Пустой список — норма: у класса может
        не быть базы, а может быть база вне корпуса.

        Берутся только узлы-ИДЕНТИФИКАТОРЫ, а не текст целиком. Текст включал бы
        ключевые слова и разделители (`extends`, `public`, `,`, `:`) и дал бы
        "классы" вида `public Base`. Имена собираются из разрешённых видов, и
        это подтверждено вызовом грамматик (см. `inherit_nodes` в профиле).

        Заглушки вида `<module>` и лямбды отбрасываются: базой класса они не
        бывают, а ребро на них — мусор в графе наследования.
        """
        if not self.profile.inherit_nodes:
            return []
        found: list[tuple[str, int]] = []
        seen: set[str] = set()
        ident_types = (
            "identifier", "type_identifier", "constant", "qualified_name",
            "scoped_type_identifier", "name", "simple_identifier", "word",
            "namespace_name", "scoped_identifier", "generic_name", "destructor_name",
            # `simple_name` есть в грамматике PowerShell: там базовый класс в
            # объявлении — это второе `simple_name` после двоеточия
            # (`class Child : Base`), и без его учёта наследование не находилось
            # вообще (проверено вызовом грамматики).
            "simple_name",
        )

        # Имя самого класса не является базой: в PowerShell и в Python имя класса
        # лежит внутри того же узла объявления, и без этой проверки `Child` попал
        # бы в список собственных баз.
        own_name = self._name_of(node)

        def walk(n) -> None:
            if n.type in ident_types:
                text = self._text(n).strip().strip("\\")
                if (text and not text.startswith("<") and text != own_name
                        and text not in seen):
                    seen.add(text)
                    found.append((text, n.start_point[0] + 1))
                return
            for ch in n.children:
                walk(ch)

        # Проверяются ДВА случая, и оба нужны:
        #   * узлы баз лежат среди ДЕТЕЙ объявления (Python, Java, C#, C++, Ruby,
        #     JavaScript, TypeScript);
        #   * либо вид узла БАЗЫ совпадает с видом самого объявления — так
        #     устроен PowerShell, где `class Child : Base` целиком является
        #     `class_statement`, и среди его детей нет ни одного `class_statement`.
        # Без второй ветки у PowerShell наследование не находилось вообще
        # (проверено вызовом грамматики: _base_names возвращал пустой список).
        for ch in node.children:
            if ch.type in self.profile.inherit_nodes:
                walk(ch)
        if node.type in self.profile.inherit_nodes:
            walk(node)
        return found

    def _visit_children(self, node, parent: str | None, container: str) -> None:
        for child in node.children:
            self._visit(child, parent=parent, container=container)

    def _signature_head(self, node) -> str:
        """Заголовок объявления без тела: имя и параметры."""
        body = node.child_by_field_name(self.profile.body_field)
        if body is None or body.start_byte <= node.start_byte:
            head = self._text(node)
        else:
            head = self._data[node.start_byte : body.start_byte].decode(
                "utf-8", errors="replace"
            )
        return head.split("\n", 1)[0].strip()

    def _record_call(self, node, container: str) -> None:
        """Записать вызов: имя цели и место."""
        callee = self._callee_name(node)
        if not callee:
            return
        line = node.start_point[0] + 1
        self._pending.append((container, callee, line, "calls", "stated"))

    def _callee_name(self, node) -> str | None:
        """Имя вызываемой цели.

        Способ зависит от грамматики: у одних вызов — это узел с полем
        `function`, у других — команда с первым словом в роли имени.
        """
        # Основной путь: поле function/name/callee.
        for field_name in ("function", "name", "callee", "method", "constructor"):
            child = node.child_by_field_name(field_name)
            if child is not None:
                text = self._text(child).strip()
                if text and len(text) < 200:
                    return text
        # Запасной путь для команд оболочки и PowerShell: первое слово.
        for ch in node.children:
            if ch.type in ("command_name", "identifier", "word", "simple_identifier",
                           "variable_name", "property_identifier"):
                text = self._text(ch).strip()
                if text and len(text) < 200:
                    return text
        return None

    def _resolve_pending(self) -> None:
        """Привязать отложенные вызовы к объявленным символам.

        Если цель вызова объявлена в этом же файле — ребро доказанное.
        Если нет — ребро помечается как выведенное по имени: точная цель
        неизвестна, но связь вероятна. Это честнее, чем молча терять вызов
        или выдавать догадку за факт.
        """
        res = self._res
        for container, callee, line, kind, prov in self._pending:
            short = callee.rsplit(":", 1)[-1].rsplit(".", 1)[-1].strip()
            target = self._declared.get(short) or self._declared.get(callee)
            if target:
                res.edges.append(
                    EdgeRec(container, target, kind, "stated", line, 1.0)
                )
            else:
                # Внешняя цель: ребро к символическому узлу по имени.
                ext_id = util.stable_id("external", short, self.profile.name)
                if not any(s.node_id == ext_id for s in res.symbols):
                    res.symbols.append(
                        SymbolRec(
                            node_id=ext_id, kind="external", name=short,
                            fqn=f"{self.profile.name}:{short}", start_line=line,
                            end_line=line, start_byte=0, end_byte=0,
                            props={"lang": self.profile.name, "external": True},
                        )
                    )
                res.edges.append(
                    EdgeRec(container, ext_id, "calls_heuristic", "derived",
                            line, 0.4)
                )


def chunks_for_language(
    res: FileExtraction,
    data: bytes,
    *,
    max_lines: int = 120,
    min_lines: int = 3,
    by_symbol: bool = True,
) -> list[ChunkRec]:
    """Чанки для языков с разбором: один чанк на символ, крупные — окнами.

    Почему так, а не окнами по всему файлу: чанк должен соответствовать
    смыслу — функции или классу. Тогда поиск находит цельный фрагмент,
    а не обрывок между двумя определениями.

    Про аргумент by_symbol. Индексатор передаёт его всем извлекателям
    одинаково, потому что он есть в настройках корпуса. Здесь он принимается
    ради совместимости вызова, но не меняет поведение: разбиение по символам
    для нового слоя уже и есть единственный режим. Если когда-нибудь появится
    необходимость резать файл без учёта символов — это будет видно по этому
    аргументу, а не спрятано.
    """
    text = textdec.decode(data, res.path)[0]
    lines = text.splitlines()
    out: list[ChunkRec] = []

    targets = [s for s in res.symbols
               if s.kind not in ("file", "external", "namespace",
                                 # P1-3: узлы-«якоря» (маршрут, ключ перевода,
                                 # пакетная переменная) НЕ являются телами кода.
                                 # Без этого исключения из них делались чанки,
                                 # и на BWO индекс вырос с 102 до 277 тысяч
                                 # чанков при том же коде: поиск замедлялся, а
                                 # находил служебные строки вместо кода.
                                 "route_node", "placeholder", "variable")]
    targets.sort(key=lambda s: s.start_line)

    for sym in targets:
        start, end = sym.start_line, sym.end_line
        if end < start:
            continue
        body = "\n".join(lines[start - 1 : end])
        if not body.strip():
            continue
        span = end - start + 1
        if span <= max_lines:
            payload = f"{sym.kind} {sym.name} ({res.path}:{start})\n{body}"
            out.append(
                ChunkRec(
                    chunk_id=util.stable_id("sym", sym.node_id, start),
                    source_kind="symbol", path=res.path, node_id=sym.node_id,
                    start_line=start, end_line=end, lang=res.lang,
                    text=payload[:8000], text_hash=util.sha256_text(payload),
                )
            )
            continue
        # Крупный символ режется окнами с перекрытием.
        step = max(1, max_lines - 10)
        for offset in range(0, span, step):
            piece = lines[start - 1 + offset : start - 1 + offset + max_lines]
            if not piece:
                break
            first = start + offset
            last = first + len(piece) - 1
            payload = (f"{sym.kind} {sym.name} "
                       f"({res.path}:{first}-{last})\n" + "\n".join(piece))
            out.append(
                ChunkRec(
                    chunk_id=util.stable_id("symwin", sym.node_id, first),
                    source_kind="symbol", path=res.path, node_id=sym.node_id,
                    start_line=first, end_line=last, lang=res.lang,
                    text=payload[:8000], text_hash=util.sha256_text(payload),
                )
            )

    if not out:
        # Символов нет: файл попадает в поиск целиком окнами, как текст.
        max_chars = 24000
        step = max(1, max_lines - 10)
        for offset in range(0, max(len(lines), 1), step):
            piece = lines[offset : offset + max_lines]
            if not piece:
                break
            body = "\n".join(piece)
            if len(body) > max_chars:
                body = body[:max_chars]
            out.append(
                ChunkRec(
                    chunk_id=util.stable_id("filewin", res.path, offset),
                    source_kind="file", path=res.path, node_id=None,
                    start_line=offset + 1, end_line=offset + len(piece),
                    lang=res.lang, text=body, text_hash=util.sha256_text(body),
                )
            )
    return out