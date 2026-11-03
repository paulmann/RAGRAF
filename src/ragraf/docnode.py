"""Документный слой: примитив `DocumentNode` и грамматика локаторов.

Зачем этот модуль, если извлечение уже есть.
`documents.py` отдаёт ПЛОСКИЙ текст (`Extracted`: статус, вид, текст, причина).
Этого хватает, чтобы документ находился поиском, и НЕ хватает, чтобы у факта был
адрес. Прежний путь клал чанк документа в `_text_chunks` (`indexer.py:1766`), и
`start_line` там — строка ИЗВЛЕЧЁННОГО текста, а не файла. Для PDF это значило
`path:12`, которого в файле нет: свойство было закреплено проверкой
(`tests/test_documents_index.py:9-10`) и работало как «ложный локатор». Локатор,
которого нет в источнике, хуже отсутствующего: клиент идёт по адресу и получает
не тот текст, а «нашлось» при этом честно.

Что здесь есть.
  1. `DocumentNode` — единица документа: вид, локатор, текст, структура,
     метаданные, провенанс, статус извлечения.
  2. Грамматика локаторов — ДАННЫЕ (`LOCATOR_FORMS`) и таблица допустимых форм
     по виду узла (`ALLOWED_FORMS`). Не условия в коде читателей: иначе правило
     «у каждого чанка валидный локатор» проверялось бы тем же кодом, который его
     нарушает.
  3. `problems()` — проверки узла, на которых стоит валидатор фазы.

Чего здесь НЕТ и не должно быть: чтения файлов. Читатель (`docread`) отдаёт узлы,
резальщик (`docchunk`) режет их на чанки, хранилище (`docindex`) пишет. Этот
модуль — только словарь, на котором они договариваются.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import util

#: Закрытый список видов узла. Расширение — правкой этого списка и грамматики,
#: а не появлением нового вида «по месту»: иначе валидатор перестанет быть гейтом.
KINDS: tuple[str, ...] = (
    "document",     # документ целиком (паспорт файла)
    "section",      # раздел: заголовок и его содержимое
    "clause",       # пункт нормативного документа
    "paragraph",    # абзац
    "table",        # логическая таблица
    "table_row",    # строка таблицы
    "slide",        # слайд презентации
    "sheet",        # лист книги
    "record",       # запись (строка лога, элемент CSV, объект JSON)
    "message",      # письмо
    "image",        # изображение
)

#: Виды, для которых `heading_chain` обязателен, если не названа причина.
TEXT_KINDS: frozenset[str] = frozenset({"section", "clause", "paragraph"})

#: Виды, для которых обязателен `table_id`.
TABLE_KINDS: frozenset[str] = frozenset({"table", "table_row"})

#: Статусы извлечения. `not_indexed_by_edition` — не ошибка и не «нет текста»:
#: так помечается формат, который сборка сознательно не разбирает (OCR вне
#: Pro/Enterprise, архивы без явной настройки).
STATUSES: tuple[str, ...] = ("ok", "failed", "degraded", "not_indexed_by_edition")

#: Грамматика локаторов. Порядок проверки важен: `:row=5` обязан проверяться
#: раньше общего `:строка`, иначе числовая форма «съест» именованную.
#:
#: Почему `path:page=3`, а не `path:page`. Двоеточие в адресе уже означает номер
#: строки (`path:12`), и `path:page` неотличим от пути с именем «page» в конце.
#: Знак `=` делает форму однозначной при разборе и не ломает существующие адреса.
LOCATOR_FORMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("line", re.compile(r"^(?P<path>[^#!]+?):(?P<start>\d+)(?:-(?P<end>\d+))?$")),
    ("page", re.compile(r"^(?P<path>[^#!:]+):page=(?P<n>\d+)$")),
    # Имя листа может содержать пробел и знаки препинания: «О выгрузке»,
    # «Прайс (опт)». Прежняя форма запрещала пробел, и валидатор заворачивал
    # ВСЮ ревизию на массовом корпусе (замер 29.10.2026: 123 нарушения из
    # 626 документов, сообщение — локатор `…xlsx:sheet=О выгрузке`).
    # Разделители адреса (`:` и `!`) остаются запрещёнными: иначе адрес
    # перестал бы разбираться однозначно.
    ("sheet", re.compile(
        r"^(?P<path>[^#!:]+):sheet=(?P<sheet>[^!:]+?)"
        r"(?:!(?P<range>[A-Za-z]+\d+(?::[A-Za-z]+\d+)?))?$"
    )),
    # Таблица НА слайде проверяется раньше самого слайда: `#table=1` иначе не
    # разберётся ни одной формой, и адрес таблицы станет ложным.
    ("slidetable", re.compile(
        r"^(?P<path>[^#!:]+):slide=(?P<n>\d+)#table=(?P<m>\d+)$"
    )),
    ("slide", re.compile(r"^(?P<path>[^#!:]+):slide=(?P<n>\d+)(?:#(?P<extra>notes))?$")),
    ("table", re.compile(r"^(?P<path>[^#!]+?):table=(?P<n>\d+)$")),
    ("row", re.compile(r"^(?P<path>[^#!]+?):row=(?P<n>\d+)$")),
    ("rows", re.compile(r"^(?P<path>[^#!]+?):rows=(?P<a>\d+)-(?P<b>\d+)$")),
    ("xpath", re.compile(r"^(?P<path>[^#!]+)#xpath\((?P<xpath>.+)\)$")),
    ("part", re.compile(r"^(?P<path>[^#!]+)#part=(?P<n>\d+)$")),
    ("message", re.compile(r"^(?P<path>[^#!]+)#message$")),
    ("anchor", re.compile(r"^(?P<path>[^#!]+)#(?P<anchor>[A-Za-z0-9_.:\-]+)$")),
    ("pointer", re.compile(r"^(?P<path>[^#!]+)#(?P<pointer>/.*)$")),
    ("inner", re.compile(r"^(?P<path>[^!]+)!(?P<inner>.+)$")),
    ("image", re.compile(r"^(?P<path>[^#!:]+):image$")),
    # Формы офисных форматов (фаза B). Все четыре — часть документа, у которой
    # нет строк файла: абзац пакета OOXML, сноска, колонтитул раздела.
    ("para", re.compile(r"^(?P<path>[^#!:]+)#para=(?P<n>\d+)$")),
    # Фаза C: цитата письма и структура EPUB. Все три формы адресуют то, что
    # повторный разбор находит заново: часть письма, главу, абзац главы.
    ("quote", re.compile(r"^(?P<path>[^#!:]+)#part=(?P<n>\d+)#quote=(?P<m>\d+)$")),
    ("chapterpara", re.compile(
        r"^(?P<path>[^#!:]+)#chapter=(?P<n>\d+)#para=(?P<m>\d+)$"
    )),
    ("chapter", re.compile(r"^(?P<path>[^#!:]+)#chapter=(?P<n>\d+)$")),
    ("footnote", re.compile(r"^(?P<path>[^#!:]+)#footnote=(?P<n>\d+)$")),
    ("header", re.compile(r"^(?P<path>[^#!:]+)#header=(?P<n>\d+)$")),
    ("footer", re.compile(r"^(?P<path>[^#!:]+)#footer=(?P<n>\d+)$")),
    ("document", re.compile(r"^(?P<path>[^#!:=]+)$")),
)

#: Какие формы допустимы для какого вида. Ключ — вид узла, значение — формы.
#: `document` допускает и `line`: паспорт файла может нести адрес первой строки.
ALLOWED_FORMS: dict[str, frozenset[str]] = {
    "document": frozenset({"document", "line", "page", "message"}),
    "section": frozenset({"line", "anchor", "xpath", "page", "slide", "para",
                          "chapter"}),
    "clause": frozenset({"line", "anchor", "xpath", "para", "footnote",
                         "header", "footer"}),
    # `part` у абзаца — это часть письма: тело письма адресуется там же, где
    # вложение и цитата, и адрес разрешим (повторный разбор даёт ту же часть).
    "paragraph": frozenset({"line", "anchor", "xpath", "page", "slide", "para",
                            "quote", "chapterpara", "part"}),
    "table": frozenset({"table", "sheet", "anchor", "xpath", "page", "slide",
                        "slidetable"}),
    "table_row": frozenset({"row", "rows", "sheet", "anchor", "xpath", "line",
                            "slidetable"}),
    "slide": frozenset({"slide"}),
    "sheet": frozenset({"sheet"}),
    # `part` у записи — вложение письма: это запись о части письма, и её адрес
    # тот же, что у самой части.
    "record": frozenset({"line", "rows", "row", "pointer", "xpath", "sheet", "part"}),
    "message": frozenset({"message", "part", "line", "anchor"}),
    "image": frozenset({"image", "page"}),
}

#: Адрес элемента архива — ОБЁРТКА над адресом любого вида: `архив!путь/внутри:12`.
#: Поэтому форма `inner` добавляется ко всем видам сразу, а не перечисляется
#: одиннадцать раз: перечисление разошлось бы с появлением нового вида молча.
ALLOWED_FORMS = {
    kind: forms | frozenset({"inner"}) for kind, forms in ALLOWED_FORMS.items()
}


def form_of(locator: str) -> str | None:
    """Имя формы локатора или None, если строка не является ни одной из форм.

    Возвращает None вместо исключения: вызывающий решает, ошибка это или
    «локатор не наше дело». Отказ в виде исключения здесь превращал бы проверку
    целостности в падение индексации.
    """
    text = str(locator or "").strip()
    if not text:
        return None
    for name, pattern in LOCATOR_FORMS:
        if pattern.match(text):
            return name
    return None


def split_locator(locator: str) -> tuple[str, str, dict[str, Any]]:
    """Разобрать локатор на `(форма, путь, поля)`.

    Неразобранный локатор отдаётся как `("", текст, {})` — вызывающий обязан
    проверить форму, а не полагаться на то, что разбор всегда удаётся.
    """
    text = str(locator or "").strip()
    name = form_of(text)
    if name is None:
        return ("", text, {})
    for candidate, pattern in LOCATOR_FORMS:
        if candidate != name:
            continue
        match = pattern.match(text)
        if match is None:  # недостижимо: имя получено тем же шаблоном
            return ("", text, {})
        fields = {k: v for k, v in match.groupdict().items()
                  if k != "path" and v is not None}
        return (name, match.group("path"), fields)
    return ("", text, {})


def path_of(locator: str) -> str:
    """Путь, к которому относится локатор (пусто, если локатор не разобран)."""
    return split_locator(locator)[1]


def make_line(path: str, start: int, end: int | None = None) -> str:
    """Локатор-строки. Единственная форма, которую строят читатели текста."""
    if end is None or int(end) <= int(start):
        return f"{path}:{int(start)}"
    return f"{path}:{int(start)}-{int(end)}"


def locator_problems(kind: str, locator: str) -> list[str]:
    """Проверить локатор на соответствие виду узла.

    Проверяется СВОЙСТВО адреса, а не его написание в одном месте: форма
    разбирается грамматикой, допустимость — таблицей, наличие пути — отдельно.
    """
    problems: list[str] = []
    text = str(locator or "").strip()
    if not text:
        problems.append("локатор пуст")
        return problems
    if kind not in KINDS:
        problems.append(f"неизвестный вид узла: {kind!r}")
        return problems
    form = form_of(text)
    if form is None:
        problems.append(f"локатор не разобран ни одной формой: {text[:120]!r}")
        return problems
    allowed = ALLOWED_FORMS.get(kind, frozenset())
    if form not in allowed:
        problems.append(
            f"форма локатора {form!r} недопустима для вида {kind!r}; "
            f"допустимы: {', '.join(sorted(allowed)) or '—'}"
        )
    path = path_of(text)
    if not path.strip():
        problems.append(f"в локаторе нет пути: {text[:120]!r}")
    return problems


def doc_id_for(path: str) -> str:
    """Идентификатор документа: стабильный по пути, без времени и случайности.

    По пути, а не по содержимому: документ правится, и `doc_status` обязан
    говорить о том же документе, а не о «новом файле с тем же именем».
    Содержимое различается полем `sha256` в паспорте документа.
    """
    return util.stable_id("doc", util.to_posix(path))


def clean_text(text: str) -> str:
    """Текст узла без NUL и управляющих символов.

    Зачем это на границе узла, а не в читателях. NUL в тексте — не косметика:
    SQLite `length()` и FTS5 считают строку кончившейся на нём, поэтому чанк,
    у которого после NUL ещё 925 609 символов, виден поиску как 7 304
    (замер 29.10.2026 на массовом корпусе: 22 таких чанка, худший — 0,8 %
    текста). Проверка на выходе каждого читателя означала бы тринадцать мест,
    где её можно забыть; здесь — одно.
    """
    if not text:
        return ""
    if "\x00" in text:
        text = text.replace("\x00", " ")
    # Прочие управляющие символы (кроме перевода строки, возврата каретки и
    # табуляции) заменяются пробелом: они ломают и поиск, и вывод в терминал.
    return "".join(
        ch if (ch >= " " or ch in "\n\r\t") else " "
        for ch in text
    )


@dataclass
class DocumentNode:
    """Единица структуры документа.

    Поля повторяют спецификацию слоя. `structure`, `metadata`, `provenance` —
    словари, потому что их состав зависит от формата; обязательные ключи в них
    названы проверками `problems()`, а не типом.
    """

    kind: str
    locator: str
    text: str = ""
    structure: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    extraction_status: str = "ok"
    extraction_warnings: list[str] = field(default_factory=list)
    #: Порядковый номер узла в документе: нужен для детерминированного
    #: `part_of` и для идентификатора чанка.
    index: int = 0

    # ------------------------------------------------------------------
    # Производные поля
    # ------------------------------------------------------------------

    @property
    def heading_chain(self) -> list[str]:
        chain = self.structure.get("heading_chain")
        return list(chain) if isinstance(chain, (list, tuple)) else []

    @property
    def no_heading_reason(self) -> str:
        return str(self.structure.get("no_heading_reason") or "")

    @property
    def table_id(self) -> str:
        return str(self.structure.get("table_id") or "")

    @property
    def path(self) -> str:
        return path_of(self.locator)

    # ------------------------------------------------------------------
    # Проверки узла
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        # Очистка текста при создании узла: свойство «в тексте узла нет NUL»
        # держится на одном месте, а не на добросовестности тринадцати читателей.
        if isinstance(self.text, str):
            object.__setattr__(self, "text", clean_text(self.text))

    def problems(self) -> list[str]:
        """Нарушения контракта узла. Пустой список — узел годен в индекс.

        Проверки 1 и 2 спецификации живут здесь, потому что они про узел, а не
        про хранилище: адрес обязан быть валидным ДО записи, иначе «потом
        проверим» превращается в «тихо записали».
        """
        out: list[str] = []
        if self.kind not in KINDS:
            out.append(f"неизвестный вид узла: {self.kind!r}")
        out.extend(locator_problems(self.kind, self.locator))
        if self.extraction_status not in STATUSES:
            out.append(f"неизвестный статус извлечения: {self.extraction_status!r}")
        # Текст обязателен для всего, кроме явно деградировавших узлов.
        if self.extraction_status == "ok" and not self.text.strip():
            out.append(f"узел {self.locator} без текста при статусе ok")
        if self.kind in TEXT_KINDS:
            if not self.heading_chain and not self.no_heading_reason:
                out.append(
                    f"у текстового узла {self.locator} нет heading_chain "
                    f"и не названа причина (no_heading_reason)"
                )
        if self.kind in TABLE_KINDS and not self.table_id:
            out.append(f"у табличного узла {self.locator} нет table_id")
        return out

    # ------------------------------------------------------------------
    # Сериализация
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "locator": self.locator,
            "text": self.text,
            "structure": dict(self.structure),
            "metadata": dict(self.metadata),
            "provenance": dict(self.provenance),
            "extraction_status": self.extraction_status,
            "extraction_warnings": list(self.extraction_warnings),
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentNode":
        return cls(
            kind=str(data.get("kind") or ""),
            locator=str(data.get("locator") or ""),
            text=str(data.get("text") or ""),
            structure=dict(data.get("structure") or {}),
            metadata=dict(data.get("metadata") or {}),
            provenance=dict(data.get("provenance") or {}),
            extraction_status=str(data.get("extraction_status") or "ok"),
            extraction_warnings=list(data.get("extraction_warnings") or []),
            index=int(data.get("index") or 0),
        )


@dataclass
class ReadResult:
    """Что вернул читатель формата: узлы и паспорт разбора.

    Отдельный тип нужен, чтобы «документ не разобран» нельзя было выразить
    пустым списком узлов: пустой список — это «текста нет», а отказ — это
    `status` и `reason`, и они обязаны доехать до отчёта.
    """

    nodes: list[DocumentNode] = field(default_factory=list)
    status: str = "ok"
    reason: str = ""
    reader: str = ""
    converter: str = ""
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": len(self.nodes),
            "status": self.status,
            "reason": self.reason,
            "reader": self.reader,
            "converter": self.converter,
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }
