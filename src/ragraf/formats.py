"""Реестр форматов данных RAGRAF: один список вместо трёх.

Зачем отдельный модуль
    До него ответ на вопрос «что индексируется» был размазан по трём местам:
    карта расширений (`config.LANG_BY_EXT`), список языков корпуса (`languages`
    в YAML) и перечень разборщика документов (`documents.SUPPORTED`). Из панели
    этим управлять было нельзя: не существовало ни перечня, ни идентификаторов,
    которые можно включать и выключать.

    Здесь один реестр. Из него получаются:
      * перечень форматов для панели — с группой, расширениями и способом разбора;
      * соответствие «расширение → формат» для отбора файлов при сканировании;
      * пометка, чем формат разбирается: деревом, tags-запросом, текстом или
        извлечением библиотекой.

Почему идентификаторы форматов, а не отдельные расширения
    Администратору нужен ОДИН переключатель на семейство: «Word» — это .doc,
    .docx, .docm, .dotx, .dotm. Пять галочек вместо одной — не гибкость, а
    ошибка ожидания: включив «Word», человек не должен думать, все ли пять
    расширений он отметил.

Настройка и значение `*`
    `index_formats` в глобальных настройках — список идентификаторов либо
    единственное значение `*`, означающее «включены все». По умолчанию именно
    `*`, и это не лень: сохранённый полный список молча пропускал бы форматы,
    добавленные позже. `*` означает «все, включая будущие».

Границы
    Реестр ничего не решает за сканирование: он отвечает только на вопрос
    «разрешён ли этот формат». Отбор по `include`/`exclude` и по списку
    `languages` корпуса остаётся там же, где был, — иначе одна настройка
    начала бы перекрывать другую, и причину пропуска файла нельзя было бы
    назвать.
"""

from __future__ import annotations

from dataclasses import dataclass

# Группы в порядке показа. Порядок фиксирован: панель рисует разделы в нём,
# и «языки программирования» не должны прыгать вниз при каждом обновлении.
GROUP_ORDER: tuple[str, ...] = ("code", "document", "data", "unsupported")

GROUP_TITLES: dict[str, str] = {
    "code": "Языки программирования",
    "document": "Документы",
    "data": "Данные, разметка и конфигурация",
    # Отдельная группа нужна честности: формат известен и его можно включить,
    # но разборщика нет. Свалить такие в «документы» значило бы показать
    # выключенный формат как поддерживаемый.
    "unsupported": "Форматы без разборщика",
}

# Способы разбора. Значения попадают в ответ операций и в панель, поэтому
# названия — часть контракта.
STRUCTURE_TITLES: dict[str, str] = {
    "tree": "разбор деревом",
    "tags": "разбор деревом (tags-запрос)",
    "text": "текстовые чанки",
    "extract": "извлечение текста библиотекой",
    "none": "нет разборщика",
}


@dataclass(frozen=True)
class DataFormat:
    """Один формат: идентификатор, расширения и способ разбора."""

    id: str
    title: str
    group: str
    exts: tuple[str, ...] = ()
    # Имена файлов без расширения (Dockerfile, Makefile, CMakeLists.txt).
    names: tuple[str, ...] = ()
    structure: str = "text"
    engine: str = ""
    note: str = ""

    def to_dict(self, enabled: bool) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "group": self.group,
            "group_title": GROUP_TITLES.get(self.group, self.group),
            "exts": list(self.exts),
            "names": list(self.names),
            "structure": self.structure,
            "structure_title": STRUCTURE_TITLES.get(self.structure, self.structure),
            "engine": self.engine,
            "note": self.note,
            "enabled": enabled,
        }


# --- Языки программирования ------------------------------------------------
# `engine` — имя грамматики tree-sitter, если разбор есть. Пустое значение
# означает текстовые чанки: файл попадает в поиск, но узлов и рёбер не даёт.
_CODE: tuple[DataFormat, ...] = (
    DataFormat("php", "PHP", "code", (".php", ".phtml", ".inc"), structure="tree",
               engine="php"),
    DataFormat("css", "CSS", "code", (".css",), structure="tree", engine="css"),
    DataFormat("html", "HTML", "code", (".htm", ".html"), structure="tree",
               engine="html"),
    DataFormat("javascript", "JavaScript", "code",
               (".js", ".mjs", ".cjs", ".jsx"), structure="tree", engine="javascript"),
    DataFormat("typescript", "TypeScript", "code", (".ts", ".mts", ".tsx"),
               structure="tree", engine="typescript"),
    DataFormat("python", "Python", "code", (".py", ".pyi"), structure="tree",
               engine="python"),
    DataFormat("csharp", "C#", "code", (".cs",), structure="tree", engine="csharp"),
    DataFormat("java", "Java", "code", (".java",), structure="tree", engine="java"),
    DataFormat("go", "Go", "code", (".go",), structure="tree", engine="go"),
    DataFormat("ruby", "Ruby", "code", (".rb",), structure="tree", engine="ruby"),
    DataFormat("lua", "Lua", "code", (".lua",), structure="tree", engine="lua"),
    DataFormat("perl", "Perl", "code", (".pl", ".pm", ".t"), structure="tree",
               engine="perl"),
    DataFormat("bash", "Shell (bash, sh, zsh)", "code", (".sh", ".bash", ".zsh"),
               structure="tree", engine="bash"),
    DataFormat("powershell", "PowerShell", "code", (".ps1", ".psm1", ".psd1"),
               structure="tree", engine="powershell"),
    DataFormat("c", "C", "code", (".c", ".h"), structure="tree", engine="c"),
    DataFormat("cpp", "C++", "code", (".cpp", ".cc", ".cxx", ".hpp"), structure="tree",
               engine="cpp"),
    # --- добавлено расширением набора языков ---
    DataFormat("rust", "Rust", "code", (".rs",), structure="tags", engine="rust"),
    DataFormat("kotlin", "Kotlin", "code", (".kt", ".kts"), structure="tags",
               engine="kotlin"),
    DataFormat("swift", "Swift", "code", (".swift",), structure="tags",
               engine="swift"),
    DataFormat("scala", "Scala", "code", (".scala", ".sc"), structure="tags",
               engine="scala"),
    DataFormat("dart", "Dart", "code", (".dart",), structure="tags", engine="dart"),
    DataFormat("r", "R", "code", (".r",), structure="tags", engine="r"),
    DataFormat("fsharp", "F#", "code", (".fs", ".fsi", ".fsx"), structure="tags",
               engine="fsharp"),
    DataFormat("ocaml", "OCaml", "code", (".ml", ".mli"), structure="tags",
               engine="ocaml"),
    DataFormat("solidity", "Solidity", "code", (".sol",), structure="tags",
               engine="solidity"),
    DataFormat("fortran", "Fortran", "code",
               (".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08"),
               structure="tags", engine="fortran"),
    DataFormat("matlab", "MATLAB", "code", (".m",), structure="tags",
               engine="matlab",
               note="расширение .m делят MATLAB и Objective-C; выбрана сторона, "
                    "которая чаще встречается на пользовательских дисках"),
    DataFormat("elm", "Elm", "code", (".elm",), structure="tags", engine="elm"),
    DataFormat("lisp", "Lisp, Scheme, Racket", "code",
               (".lisp", ".lsp", ".cl", ".scm", ".rkt"), structure="tags",
               engine="commonlisp",
               note="разбор деревом обслуживает Common Lisp; Scheme и Racket "
                    "идут текстом"),
    DataFormat("julia", "Julia", "code", (".jl",), engine="tree-sitter julia"),
    DataFormat("elixir", "Elixir", "code", (".ex", ".exs"), engine="tree-sitter elixir"),
    DataFormat("erlang", "Erlang", "code", (".erl", ".hrl"), engine="tree-sitter erlang"),
    DataFormat("haskell", "Haskell", "code", (".hs", ".lhs"), engine="tree-sitter haskell"),
    DataFormat("clojure", "Clojure", "code", (".clj", ".cljs", ".cljc", ".edn"),
               engine="tree-sitter clojure"),
    DataFormat("groovy", "Groovy, Gradle", "code", (".groovy", ".gradle"),
               engine="tree-sitter groovy"),
    DataFormat("vb", "Visual Basic .NET", "code", (".vb",), engine="tree-sitter vb"),
    DataFormat("pascal", "Pascal, Delphi", "code",
               (".pas", ".dpr", ".dpk", ".lpr"), engine="tree-sitter pascal"),
    DataFormat("objc", "Objective-C", "code", (".mm",), engine="tree-sitter objc",
               note="файлы .m отнесены к MATLAB: расширение неразличимо по имени"),
    DataFormat("zig", "Zig", "code", (".zig",), engine="tree-sitter zig"),
    DataFormat("nim", "Nim", "code", (".nim",), engine="tree-sitter nim"),
    DataFormat("crystal", "Crystal", "code", (".cr",), engine="tree-sitter crystal"),
    DataFormat("tcl", "Tcl", "code", (".tcl",), engine="tree-sitter tcl"),
    DataFormat("prolog", "Prolog", "code", (".prolog",),
               engine="tree-sitter prolog"),
    DataFormat("ada", "Ada", "code", (".adb", ".ads"), engine="tree-sitter ada"),
    DataFormat("verilog", "Verilog, SystemVerilog", "code", (".v", ".sv", ".svh"),
               engine="tree-sitter verilog"),
    DataFormat("vhdl", "VHDL", "code", (".vhd", ".vhdl"), engine="tree-sitter vhdl"),
    DataFormat("asm", "Ассемблер", "code", (".asm", ".s", ".nasm"),
               engine="tree-sitter asm"),
    DataFormat("gdscript", "GDScript (Godot)", "code", (".gd",),
               engine="tree-sitter gdscript"),
    DataFormat("vala", "Vala", "code", (".vala",), engine="tree-sitter vala"),
    DataFormat("hcl", "Terraform, HCL", "code", (".tf", ".tfvars"), engine="tree-sitter hcl"),
    DataFormat("dockerfile", "Dockerfile", "code", names=("dockerfile",),
               engine="tree-sitter dockerfile"),
    DataFormat("make", "Makefile", "code", (".mk", ".mak"),
               names=("makefile", "gnumakefile"), engine="tree-sitter make"),
    DataFormat("cmake", "CMake", "code", (".cmake",), names=("cmakelists.txt",),
               engine="tree-sitter cmake"),
    DataFormat("scss", "SCSS", "code", (".scss",), engine="tree-sitter scss"),
    DataFormat("less", "Less", "code", (".less",), engine="tree-sitter less"),
    DataFormat("vue", "Vue", "code", (".vue",), engine="tree-sitter vue"),
    DataFormat("svelte", "Svelte", "code", (".svelte",), engine="tree-sitter svelte"),
    DataFormat("sql", "SQL", "code", (".sql", ".psql", ".mysql"),
               note="грамматика SQL дала ошибки на всех проверенных файлах "
                    "(12 из 12): разбор деревом не включается"),
    DataFormat("batch", "Batch (cmd)", "code", (".bat", ".cmd"),
               note="грамматика batch дала ошибки на всех проверенных файлах "
                    "(7 из 7)"),
    DataFormat("graphql", "GraphQL", "code", (".graphql", ".gql"),
               engine="tree-sitter graphql"),
    DataFormat("proto", "Protocol Buffers", "code", (".proto",),
               engine="tree-sitter proto"),
)

# --- Документы -------------------------------------------------------------
_DOCUMENT: tuple[DataFormat, ...] = (
    DataFormat("word", "Word (.docx, .docm, .dotx и старый .doc)", "document",
               (".docx", ".docm", ".dotx", ".dotm", ".doc"),
               structure="extract",
               engine="python-docx; .doc — собственный разбор OLE/Word 97"),
    DataFormat("excel", "Excel (.xlsx, .xlsm и старый .xls)", "document",
               (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"),
               structure="extract",
               engine="openpyxl; .xls — собственный разбор BIFF8"),
    DataFormat("powerpoint", "PowerPoint (.pptx, .pptm, .ppsx и старый .ppt)",
               "document", (".pptx", ".pptm", ".ppsx", ".potx", ".ppt"),
               structure="extract",
               engine="python-pptx; .ppt — собственный разбор OLE/записей"),
    DataFormat("pdf", "PDF", "document", (".pdf",), structure="extract",
               engine="pypdf"),
    DataFormat("rtf", "RTF", "document", (".rtf",), structure="extract",
               engine="собственный разбор разметки RTF"),
    DataFormat("odf", "OpenDocument (.odt, .ods, .odp)", "document",
               (".odt", ".ods", ".odp", ".odg"), structure="extract",
               engine="собственный разбор zip+XML"),
    DataFormat("epub", "EPUB", "document", (".epub",), structure="extract",
               engine="собственный разбор zip+XHTML"),
    DataFormat("mail", "Почта (.eml, .msg)", "document", (".eml", ".msg"),
               structure="extract",
               engine="стандартная библиотека email; .msg — разбор OLE"),
    DataFormat("wps", "WPS Office (.wps, .et, .dps)", "document",
               (".wps", ".et", ".dps"), structure="extract",
               engine="форматы совместимы с Word/Excel/PowerPoint 97"),
)

# --- Данные, разметка и конфигурация ---------------------------------------
_DATA: tuple[DataFormat, ...] = (
    DataFormat("json", "JSON", "data",
               (".json", ".ndjson", ".jsonl", ".geojson", ".hjson", ".json5")),
    DataFormat("yaml", "YAML", "data", (".yml", ".yaml")),
    DataFormat("toml", "TOML", "data", (".toml",)),
    DataFormat("ini", "INI и свойства", "data",
               (".ini", ".cfg", ".conf", ".properties", ".editorconfig")),
    DataFormat("xml", "XML и проектные файлы", "data",
               (".xml", ".manifest", ".props", ".csproj", ".xsd", ".xsl", ".svg")),
    DataFormat("markdown", "Markdown", "data", (".md", ".markdown", ".rmd")),
    DataFormat("rst", "reStructuredText", "data", (".rst",)),
    DataFormat("text", "Простой текст", "data", (".txt", ".text", ".sln", ".mht",
                                                 ".mhtml", ".pro")),
    DataFormat("csv", "Таблицы как текст (CSV, TSV)", "data",
               (".csv", ".tsv", ".psv")),
    DataFormat("log", "Журналы", "data", (".log", ".out", ".err")),
)

# --- Форматы без разборщика ------------------------------------------------
# Включать их бессмысленно, но и молчать о них нельзя: человек ищет «Pages»
# в списке и должен увидеть, что формат известен, а разборщика нет.
_UNSUPPORTED: tuple[DataFormat, ...] = (
    DataFormat("pages", "Apple Pages, Numbers, Key", "unsupported",
               (".pages", ".numbers", ".key"), structure="none",
               note="закрытый формат iWork (IWA): текстового слоя без "
                    "macOS-разборщика нет"),
    DataFormat("onenote", "Microsoft OneNote", "unsupported", (".one",),
               structure="none", note="двоичный формат OneNote: разборщика нет"),
    DataFormat("chm", "Справка Windows (.chm)", "unsupported", (".chm",),
               structure="none", note="сжатый ITS-контейнер: разборщика нет"),
    DataFormat("djvu", "DjVu", "unsupported", (".djvu",), structure="none",
               note="скан без текстового слоя: нужен OCR, он не выполняется"),
)

FORMATS: tuple[DataFormat, ...] = _CODE + _DOCUMENT + _DATA + _UNSUPPORTED

BY_ID: dict[str, DataFormat] = {f.id: f for f in FORMATS}

# Расширение → формат. Проверка на пересечения обязательна: одно расширение
# не может принадлежать двум форматам, иначе выключение одного из них молча
# включало бы файл через другой.
EXT_FORMAT: dict[str, str] = {}
NAME_FORMAT: dict[str, str] = {}
for _fmt in FORMATS:
    for _ext in _fmt.exts:
        if _ext in EXT_FORMAT:
            raise RuntimeError(
                f"расширение {_ext} заявлено дважды: "
                f"{EXT_FORMAT[_ext]} и {_fmt.id}"
            )
        EXT_FORMAT[_ext] = _fmt.id
    for _name in _fmt.names:
        if _name in NAME_FORMAT:
            raise RuntimeError(
                f"имя файла {_name} заявлено дважды: "
                f"{NAME_FORMAT[_name]} и {_fmt.id}"
            )
        NAME_FORMAT[_name] = _fmt.id

ALL_IDS: tuple[str, ...] = tuple(f.id for f in FORMATS)

# Значение настройки, означающее «включены все, включая добавленные позже».
ALL = "*"


def normalize(enabled) -> set[str]:
    """Привести значение настройки к набору включённых идентификаторов.

    `*` разворачивается в полный список, `None` и пустое значение тоже:
    «настройка не задана» не должно означать «не индексировать ничего».
    Неизвестные идентификаторы отбрасываются — их видит `unknown_ids`.
    """
    if enabled is None:
        return set(ALL_IDS)
    if isinstance(enabled, str):
        parts = [p.strip() for p in enabled.split(",") if p.strip()]
    else:
        parts = [str(p).strip() for p in enabled]
    if not parts or ALL in parts:
        return set(ALL_IDS)
    return {p for p in parts if p in BY_ID}


def unknown_ids(enabled) -> list[str]:
    """Идентификаторы, которых нет в реестре. Нужно проверке настроек."""
    if enabled is None:
        return []
    parts = ([p.strip() for p in str(enabled).split(",")] if isinstance(enabled, str)
             else [str(p).strip() for p in enabled])
    return [p for p in parts if p and p != ALL and p not in BY_ID]


def format_of_ext(ext: str) -> str | None:
    """Формат по расширению (с точкой или без, регистр не важен)."""
    key = ext.lower()
    if key and not key.startswith("."):
        key = "." + key
    return EXT_FORMAT.get(key)


def format_of_path(path: str) -> str | None:
    """Формат по пути: сначала расширение, потом имя файла без расширения.

    Имя проверяется вторым намеренно: `Dockerfile` и `Makefile` расширения не
    имеют, но это не «неизвестный файл» — у них своя грамматика.
    """
    from pathlib import PurePosixPath

    posix = str(path).replace("\\", "/")
    p = PurePosixPath(posix)
    name = p.name.lower()
    # Имя проверяется ПЕРВЫМ. Измерено на CMakeLists.txt: расширение `.txt`
    # отдавало «простой текст» раньше, чем имя успевало сказать «CMake»,
    # и файл сборки индексировался как обычный текст. Тот же порядок нужен
    # Dockerfile и Makefile — у них расширения нет вовсе.
    if name in NAME_FORMAT:
        return NAME_FORMAT[name]
    ext = p.suffix.lower()
    if ext in EXT_FORMAT:
        return EXT_FORMAT[ext]
    # Составные имена вида Dockerfile.prod и Makefile.am: расширения нет либо
    # оно не описано, но основа известна.
    base = name.split(".", 1)[0]
    return NAME_FORMAT.get(base)


def is_enabled(path: str, enabled) -> bool:
    """Разрешён ли файл настройкой форматов.

    Файл неизвестного формата разрешён: реестр не должен молча выбрасывать
    то, чего в нём нет, — иначе новая грамматика «исчезала» бы из индекса до
    того, как её успели описать.
    """
    fmt = format_of_path(path)
    if fmt is None:
        return True
    return fmt in normalize(enabled)


def catalog(enabled) -> list[dict]:
    """Перечень форматов для панели: включённость, группа, расширения."""
    allowed = normalize(enabled)
    return [f.to_dict(f.id in allowed) for f in FORMATS]


def summary(enabled) -> dict:
    """Сводка: сколько форматов включено. Нужна панели и проверкам."""
    allowed = normalize(enabled)
    return {
        "total": len(FORMATS),
        "enabled": sum(1 for f in FORMATS if f.id in allowed),
        "groups": {
            g: {
                "title": GROUP_TITLES[g],
                "total": sum(1 for f in FORMATS if f.group == g),
                "enabled": sum(1 for f in FORMATS if f.group == g and f.id in allowed),
            }
            for g in GROUP_ORDER
            if any(f.group == g for f in FORMATS)
        },
    }


def unassigned_exts(lang_by_ext: dict[str, str]) -> list[str]:
    """Расширения, которые знает карта языков, но не знает реестр форматов.

    Нужна проверке: реестр и карта расширений обязаны покрывать друг друга,
    иначе формат, добавленный в карту, нельзя ни увидеть в панели, ни выключить.
    """
    return sorted(ext for ext in lang_by_ext if ext not in EXT_FORMAT)


def orphan_formats(lang_by_ext: dict[str, str]) -> list[str]:
    """Форматы, у которых нет ни одного расширения из карты языков."""
    known = set(lang_by_ext)
    return sorted(
        f.id for f in FORMATS
        if f.exts and not any(ext in known for ext in f.exts)
    )
