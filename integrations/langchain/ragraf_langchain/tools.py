# -*- coding: utf-8 -*-
"""Инструменты RAGRAF дляLangChain-агента: предметные операции, не одна на всё.

Почему не `ragraf_execute(action, payload)`. Схема аргументов — это то, по чему
модель решает, когда и как звать инструмент. Одна «универсальная» операция с
`action: str` и `payload: dict` лишает модель этой подсказки: она не знает ни
допустимых действий, ни формы полезной нагрузки, и ошибается молча. Поэтому —
семь узких инструментов с описанными входами.

Почему связь помечается словом. `op_callers`/`op_impact` разделяют ДОКАЗАННЫЕ
(`stated`) и ВЫВЕДЕННЫЕ (`derived`) связи, и выведенная связь доказательством не
является: в этом дереве замерено 100 % выведенных на одном запросе
(`src/ragraf/facade.py:3188-3290`, поле `provenance` у каждого элемента). Инструмент
обязан сохранить это различие в тексте ответа, иначе эвристика читается как факт.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ._bridge import get_facade

__all__ = [
    "build_tools",
    "format_mcp_output",
    "search_text",
    "symbol_text",
    "callers_text",
    "impact_text",
    "read_text",
    "write_check_text",
    "freshness_text",
]

#: Сколько символов сниппета показывать в ответе инструмента поиска.
SNIPPET_CHARS = 240
#: Предел строк, которые инструмент чтения возвращает за один вызов.
READ_LIMIT = 400


# --- форматирование ответов (общее для инструментов и смоука) ---------------

#: Какой форматтер применять к какой операции (по имени MCP-инструмента).
FORMATTERS = {
    "ragraf_search": "search",
    "ragraf_search_multi": "search",
    "ragraf_symbol": "symbol",
    "ragraf_callers": "callers",
    "ragraf_callees": "callers",
    "ragraf_impact": "impact",
    "ragraf_live_read": "read",
    "ragraf_code": "read",
    "ragraf_write_check": "write_check",
    "ragraf_freshness": "freshness",
}

def _short(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ref(item: dict[str, Any]) -> str:
    path = str(item.get("path") or item.get("fqn") or "?")
    start = item.get("start_line")
    end = item.get("end_line")
    if start is None:
        return path
    return f"{path}:{start}" if end in (None, start) else f"{path}:{start}-{end}"


def search_text(envelope: dict[str, Any]) -> str:
    """Хиты `op_search` в компактный текст: адрес, оценка, причина совпадения."""
    rev = envelope.get("index_rev") or "?"
    hits = envelope.get("result") or []
    if not hits:
        return (f"RAGRAF: ничего не найдено (rev={rev}, provenance="
                f"{envelope.get('provenance')}). Пустой результат — это не ошибка; "
                "уточните запрос или проверьте corpus/исключения.")
    lines = [f"RAGRAF: {len(hits)} хит(ов), rev={rev}, provenance={envelope.get('provenance')}"]
    for hit in hits:
        head = (f"- {_ref(hit)} | score={hit.get('score')} | "
                f"{hit.get('source_kind') or '?'} | {hit.get('why') or ''}").rstrip(" |")
        lines.append(head)
        snippet = hit.get("snippet")
        if snippet:
            lines.append(f"    {_short(snippet, SNIPPET_CHARS)}")
    return "\n".join(lines)


def symbol_text(envelope: dict[str, Any]) -> str:
    items = envelope.get("result") or []
    if not items:
        return (f"RAGRAF: символ не найден (rev={envelope.get('index_rev')}). "
                "Проверьте имя или используйте ragraf_search.")
    lines = [f"RAGRAF: определений {len(items)}, rev={envelope.get('index_rev')}"]
    for item in items:
        lines.append(f"- {item.get('fqn')} [{item.get('kind')}] {_ref(item)}")
        signature = item.get("signature")
        if signature:
            lines.append(f"    {_short(signature, 200)}")
    return "\n".join(lines)


def callers_text(envelope: dict[str, Any]) -> str:
    result = envelope.get("result") or {}
    items = result.get("items") or []
    target = result.get("target") or "?"
    if not items:
        return (f"RAGRAF: вызывающих не найдено для {target} "
                f"(rev={envelope.get('index_rev')}). Ноль связей — не доказательство "
                "изолированности: динамические вызовы статическим анализом не видны.")
    stated = result.get("stated")
    derived = result.get("derived")
    lines = [f"RAGRAF: вызывающие {target} — всего {len(items)} "
             f"(доказано {stated}, выведено {derived}), rev={envelope.get('index_rev')}"]
    for item in items:
        mark = "доказано" if item.get("provenance") == "stated" else "ВЫВЕДЕНО"
        lines.append(f"- {_ref(item)} {item.get('fqn')} — {mark}, "
                     f"уверенность {item.get('confidence')}, "
                     f"доказательство {item.get('evidence') or '—'}")
    return "\n".join(lines)


def impact_text(envelope: dict[str, Any]) -> str:
    result = envelope.get("result") or {}
    target = result.get("target") or "?"
    files = result.get("affected_files") or []
    if not files:
        return (f"RAGRAF: изменение {target} не затрагивает файлов по индексу "
                f"(rev={envelope.get('index_rev')}); выведенных связей "
                f"{result.get('derived')}, доказанных {result.get('stated')}.")
    share = result.get("heuristic_share")
    lines = [f"RAGRAF: изменение {target} затрагивает файлов {result.get('affected_count')} "
             f"(доказано {result.get('stated')}, выведено {result.get('derived')}, "
             f"доля эвристики {share}), rev={envelope.get('index_rev')}"]
    for item in files[:40]:
        if isinstance(item, dict):
            lines.append(f"- {_ref(item)} {item.get('fqn') or ''}".rstrip())
        else:
            lines.append(f"- {item}")
    if len(files) > 40:
        lines.append(f"  … ещё {len(files) - 40} (полный список — операция impact в CLI)")
    return "\n".join(lines)


def read_text(envelope: dict[str, Any], *, limit: int = READ_LIMIT) -> str:
    result = envelope.get("result") or {}
    path = result.get("path") or "?"
    text = result.get("text") or ""
    head = (f"RAGRAF: {path}, строки {result.get('requested')}, всего "
            f"{result.get('total_lines')}")
    if result.get("changed_since_index"):
        head += " | ВНИМАНИЕ: файл изменился после индексации (см. sha256)"
    if not text:
        return head + " — текста нет (файл вне индекса или бинарный)"
    body = text if len(text) <= limit * 120 else text[: limit * 120]
    return head + "\n" + body


def write_check_text(envelope: dict[str, Any]) -> str:
    result = envelope.get("result") or {}
    problems = envelope.get("problems") or []
    table = result.get("table") or "?"
    if problems:
        return (f"RAGRAF: план записи в {table} НЕ пройдёт проверку\n"
                + "\n".join(f"- {item}" for item in problems))
    return (f"RAGRAF: план записи в {table} согласован со схемой "
            f"(колонок {result.get('column_count')}, операция {result.get('operation')})")


def format_mcp_output(name: str, output: Any) -> str:
    """Ответ MCP-инструмента → такой же текст, как у прямой операции.

    ПОЧЕМУ ЭТО НУЖНО. Сервер отдаёт ДВА текстовых блока: краткую сводку и весь
    ответ в виде JSON (`src/ragraf/mcp_server.py:3688-3706`). В сводке хитов нет,
    а в JSON адрес разложен на `path` и `start_line`, то есть строки «путь:строка»
    не существует ни в одном блоке — замерено 03.11.2026: проверка адреса на
    MCP-пути падала именно на этом, хотя поиск отработал.

    Поэтому блок JSON разбирается и рендерится тем же форматтером, что и прямой
    путь: у агента не должно быть двух разных форм одного факта.
    """
    blocks: list[str] = []
    payload: dict[str, Any] | None = None
    if isinstance(output, list):
        for block in output:
            text = block.get("text") if isinstance(block, dict) else str(block)
            if not isinstance(text, str):
                continue
            blocks.append(text)
            if payload is None and text.lstrip().startswith("{"):
                try:
                    candidate = __import__("json").loads(text)
                except ValueError:
                    candidate = None
                if isinstance(candidate, dict):
                    payload = candidate
    elif isinstance(output, str):
        blocks.append(output)
        if output.lstrip().startswith("{"):
            try:
                candidate = __import__("json").loads(output)
            except ValueError:
                candidate = None
            if isinstance(candidate, dict):
                payload = candidate

    kind = FORMATTERS.get(str(name or ""))
    if payload is not None and kind is not None:
        if kind == "search":
            return search_text(payload)
        if kind == "symbol":
            return symbol_text(payload)
        if kind == "callers":
            return callers_text(payload)
        if kind == "impact":
            return impact_text(payload)
        if kind == "read":
            return read_text(payload)
        if kind == "write_check":
            return write_check_text(payload)
        if kind == "freshness":
            return freshness_text(payload)
    # Нет разбираемого JSON (или операция без форматтера): отдаём сводку сервера
    # как есть — лучше непереформатированный ответ, чем выдуманный.
    return "\n".join(blocks) if blocks else str(output)


def freshness_text(envelope: dict[str, Any]) -> str:
    result = envelope.get("result") or {}
    changed = result.get("changed_since_index")
    now_hash = str(result.get("corpus_hash_now") or "")[:12] or "—"
    index_hash = str(result.get("corpus_hash_indexed") or "")[:12] or "—"
    head = (f"RAGRAF: индекс {'УСТАРЕЛ' if changed else 'свеж'} "
            f"(rev={result.get('rev_id')}, "
            f"сумма сейчас {now_hash}, в индексе {index_hash}).")
    # Предупреждение появляется только когда оно верно. Строка про устаревший
    # индекс, оставленная в свежем ответе, читается как сигнал тревоги, а
    # сигнал, который звучит всегда, перестают читать.
    tail = (" Факты описывают прошлое состояние кода — переиндексируйте "
            "(`ensure`)." if changed else " Фактам индекса можно доверять.")
    return head + tail


# --- схемы аргументов -------------------------------------------------------

class SearchArgs(BaseModel):
    query: str = Field(description="Что искать: имя функции, настройку, тему, текст ошибки")
    limit: int = Field(default=8, ge=1, le=50, description="Сколько чанков вернуть")
    kinds: list[str] | None = Field(
        default=None, description="Фильтр вида чанка: code, doc, file; None — все")


class SymbolArgs(BaseModel):
    name: str = Field(description="Имя символа (функции, класса, метода)")
    limit: int = Field(default=5, ge=1, le=50, description="Сколько определений вернуть")


class GraphArgs(BaseModel):
    name: str = Field(description="Полное имя (fqn вида путь:имя) или имя символа")
    depth: int = Field(default=1, ge=1, le=5, description="Глубина обхода связей")
    limit: int = Field(default=20, ge=1, le=200, description="Сколько связей вернуть")


class ReadArgs(BaseModel):
    path: str = Field(description="Путь файла относительно корня корпуса")
    start: int | None = Field(default=None, ge=1, description="Первая строка (1-based)")
    end: int | None = Field(default=None, ge=1, description="Последняя строка включительно")


class WriteCheckArgs(BaseModel):
    table: str = Field(description="Имя таблицы, в которую планируется запись")
    columns: list[str] | None = Field(
        default=None, description="Колонки, которые будут записаны; None — проверить обязательные")
    operation: str = Field(default="insert", description="insert или update")


# --- сборка ----------------------------------------------------------------

def build_tools(config_path: str | None = None, *,
                watch: bool = False,
                telemetry: bool = True,
                agent: str = "langchain") -> list[BaseTool]:
    """Семь инструментов поверх одного фасада RAGRAF.

    Фасад берётся из кэша по конфигурации (`_bridge.get_facade`), поэтому все
    инструменты делят одно соединение с индексом, а не открывают три хранилища
    SQLite на каждый вызов.
    """

    def facade() -> Any:
        return get_facade(config_path, watch=watch, telemetry=telemetry, agent=agent)

    def search(query: str, limit: int, kinds: list[str] | None) -> str:
        return search_text(facade().op_search(query, limit=limit, kinds=kinds,
                                              with_text=True, text_width=600))

    def symbol(name: str, limit: int) -> str:
        return symbol_text(facade().op_symbol(name, limit=limit))

    def callers(name: str, depth: int, limit: int) -> str:
        return callers_text(facade().op_callers(name, depth=depth, limit=limit))

    def impact(name: str, depth: int, limit: int) -> str:
        return impact_text(facade().op_impact(name, depth=depth, limit=limit))

    def read(path: str, start: int | None, end: int | None) -> str:
        return read_text(facade().op_live_read(path, start, end))

    def write_check(table: str, columns: list[str] | None, operation: str) -> str:
        return write_check_text(facade().op_write_check(table, columns,
                                                        operation=operation))

    def freshness() -> str:
        return freshness_text(facade().op_freshness(auto_reindex=False))

    return [
        StructuredTool.from_function(
            func=search, name="ragraf_search", args_schema=SearchArgs,
            description=("Гибридный поиск по индексу корпуса RAGRAF (лексика + вектор). "
                         "Возвращает адреса вида путь:строка — цитируйте их в ответе.")),
        StructuredTool.from_function(
            func=symbol, name="ragraf_symbol", args_schema=SymbolArgs,
            description="Где объявлен символ: файл, строки, сигнатура, вид."),
        StructuredTool.from_function(
            func=callers, name="ragraf_callers", args_schema=GraphArgs,
            description=("Кто вызывает символ по графу кода. Каждая связь помечена "
                         "«доказано» или «ВЫВЕДЕНО» — выведенная не доказательство.")),
        StructuredTool.from_function(
            func=impact, name="ragraf_impact", args_schema=GraphArgs,
            description="Что затронет изменение символа: затронутые файлы и доля эвристики."),
        StructuredTool.from_function(
            func=read, name="ragraf_read", args_schema=ReadArgs,
            description=("Прочитать актуальный файл по строкам; ответ говорит, изменился "
                         "ли файл после индексации.")),
        StructuredTool.from_function(
            func=write_check, name="ragraf_write_check", args_schema=WriteCheckArgs,
            description=("Проверить план INSERT/UPDATE против схемы ДО выполнения SQL: "
                         "несуществующие и обязательные колонки.")),
        StructuredTool.from_function(
            func=freshness, name="ragraf_freshness",
            description="Свеж ли индекс: совпадает ли контрольная сумма корпуса с кодом."),
    ]
