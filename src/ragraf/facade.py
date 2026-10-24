"""Фасад: 17 операций (ФТ-14..ФТ-23) и единый конверт ответа (ФТ-16).

Конверт обязателен для каждого ответа:
    op, index_rev, capability, drift, provenance, limits, result, stale_warnings

Принципы (архитектура §7):
  * агенту НЕ доступен язык запросов к графу — только фиксированные операции (ФТ-15);
  * каждый факт несёт path:line (ФТ-17);
  * impact разделяет доказанные и эвристические связи (ФТ-18);
  * slice отдаёт структуру без тел функций (ФТ-19), тела — только операция code;
  * при отсутствии релевантного результата отдаётся явный «не найдено» + live_read (ФТ-21).
"""

from __future__ import annotations

import json as _json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from . import analysis, db_marks, store, util
from .config import ProjectConfig
from .embeddings import Embedder
from .lexvec import LexVecStore
from .revisions import ControlStore
from .telemetry import CHARS_PER_LINE, CHARS_PER_UNIT, CallRecord, Telemetry, normalize_arg


def watch_hashes(cfg: ProjectConfig) -> tuple[str, str, int, dict[str, str]]:
    """Суммы корпуса и конфигурации (обёртка для операций фасада).

    Вынесено отдельной функцией, чтобы фасад не тянул приватные детали модуля
    сторожа: контракт — четыре значения, и он стабилен.
    """
    from .watch import current_hashes

    return current_hashes(cfg)

DEFINED_LIMITS = ["статический анализ; динамика (значения во время выполнения) не покрыта"]

# Порог достоверности рёбер для обходов (callers / callees / impact / slice /
# references / policy_trace): ребро слабее порога в ответ не попадает.
#
# ЗАЧЕМ ПОРОГ (измеренный дефект B5). `callees` отдавал связь на узел-заглушку
# `cwd` с путём ЧУЖОГО файла `tools/ragraf-mcp-http.py:138`. Заглушка внешней
# цели ровно одна на весь корпус (`extract/generic.py:585`), а ключ таблицы
# `node` — `(rev_id, node_id)` и запись идёт `INSERT OR REPLACE`: у общей
# заглушки оставался путь ПОСЛЕДНЕГО файла, который её упомянул. Рёбра на такие
# заглушки пишет извлекатель с достоверностью 0.4 — «цель не объявлена в этом
# файле, имя угадано» (`extract/generic.py:596`). Замер rev-0073: ВСЕ 28 463
# ребра с достоверностью 0.4 ведут на узлы `kind='external'`.
#
# НА КАКОМ ЗАМЕРЕ ВЫБРАНО ЗНАЧЕНИЕ. Распределение `confidence` по рёбрам
# (rev-0070 из задания, подтверждено на живом rev-0073):
#     0.4  derived calls_heuristic — 28 179 (rev-0073: 28 463) ← «имя без файла»
#     0.6  derived calls_heuristic —  1 202 (rev-0073:  1 134) ← один кандидат
#     0.6  derived extends         —     11
#     0.8  derived extends         —      1
#     0.8/0.9/1.0 stated           —  3 222 (rev-0073: 3 291 calls + contains и др.)
# Рёбер 0.5 и 0.7 в графе НЕТ. Значит 0.5 — единственная точка отсечения,
# попадающая в пустоту распределения: она отделяет «выведено по имени, файла
# нет» (0.4) от «выведено по имени с единственным кандидатом или по
# наследованию» (0.6). Порог 0.7 отсекал бы уже и проверенные выводы, порог 0.4
# не отсекал бы ничего.
#
# ЧЕГО ПОРОГ НЕ ТРОГАЕТ. Доказанные рёбра (`provenance='stated'`) не отсекаются
# никогда, даже при низкой достоверности: снять производную связь — снизить шум,
# снять доказанную — потерять факт. Сравнение нестрогое, поэтому привязка к
# методу базового класса с достоверностью 0.5 (`cpg.py`, MRO) остаётся.
#
# ГРАНИЦА ДОВЕРИЯ: `graph_algo.expand` (обход на матрице, раздел 7 паспорта)
# этот порог пока НЕ применяет — совпадение двух исполнителей обхода проверено
# по направлению и списку видов рёбер, а не по достоверности.
MIN_EDGE_CONFIDENCE = 0.5

# Срок жизни кэша ответа `catalog` (шаг 0.4 плана). Измерено 23.10.2026:
# полный ответ стоил 242 255 мс, потому что пересчитывал контрольные суммы
# 36 корпусов. За 60 с состав реестра практически не меняется, а любая
# правка файла реестра кэш обесценивает немедленно (сверка mtime).
CATALOG_CACHE_TTL_SEC = 60.0



def _edge_passes_floor(provenance: Any, confidence: Any) -> bool:
    """Пропускать ли ребро в обход по достоверности (см. `MIN_EDGE_CONFIDENCE`).

    Правило одно на весь модуль: оно применяется и в SQL обхода (`_bfs`), и на
    выходе операции (`_graph_walk`), чтобы порог не зависел от одной строки SQL
    и не разошёлся между операциями.
    """
    if str(provenance or "") == "stated":
        return True
    try:
        return float(confidence or 0.0) >= MIN_EDGE_CONFIDENCE
    except (TypeError, ValueError):
        # Нечисловая достоверность — не повод выдать связь за проверенную.
        return False


def _snippet_head(raw: str, width: int) -> str:
    """Фрагмент чанка: служебный заголовок плюс первая строка кода, в границах width.

    Формат текста чанка (замерено на rev-0031): строка 0 — заголовок вида
    «function indexed_hashes (src/ragraf/watch.py:151)», строки с 1 — сам код,
    соответствующий `chunk_map.start_line`. Заголовок один давал агенту адрес и вид
    символа, но не содержание: `def f(...)` без тела не отвечает на вопрос
    «тот ли это фрагмент». Заголовок плюс первая строка кода отвечает, и это
    остаётся дешёвым (две строки вместо всего чанка на 1 672 символа).

    Обрезка — по границе слова в пределах первой строки кода: идентификатор,
    разрезанный посередине (`set_te`), агент читает как другое имя.
    """
    lines = raw.splitlines()
    if not lines:
        return ""
    head = lines[0].strip()
    body = next((ln for ln in lines[1:] if ln.strip()), "")
    out = f"{head} — {body.strip()}" if body else head
    if len(out) > width:
        cut = out.rfind(" ", 0, width)
        out = out[: cut if cut > width // 2 else width] + "…"
    return out


def _payload_weight(entry: dict) -> int:
    """Фактическая длина полезной нагрузки ОДНОЙ записи repo_map, в символах.

    Считается тем же сериализатором, которым ответ уходит наружу (UTF-8, без
    экранирования не-ASCII): «символов» на диске и «символов» в ответе — разные
    величины (замер: 4 360 803 байта = 3 389 418 символов, ×1,29 из-за
    кириллицы), и бюджет обязан считаться во вторых, потому что именно они
    попадают в контекст.
    """
    import json as _json

    return len(_json.dumps(entry, ensure_ascii=False))


def _payload_weight_list(entries: list[dict]) -> int:
    """Длина списка записей ровно в том виде, в каком он уходит в ответ.

    Отличие от суммы весов записей — разделители: между записями есть `,`,
    и на 78 файлах это +77 символов. Замер rev-0032: бюджет 6 400 при заявленных
    6 389 и фактических 6 417 — именно из-за непосчитанных разделителей.
    Бюджет, который «почти» соблюдён, не худший случай: агент считает себя
    в пределах лимита, когда уже нет. Поэтому считается целый список.
    """
    import json as _json

    if not entries:
        return 0
    return len(_json.dumps(entries, ensure_ascii=False))


def _drop_nested(spans: list[tuple[str, int, int, str | None]]) -> list[tuple[str, int, int, str | None]]:
    """Убрать спаны, ЦЕЛИКОМ вложенные в другой спан (без частичных пересечений).

    Замер rev-0031 на `src/ragraf/facade.py`: 61 спан, из них 50 вложенных —
    51 метод внутри класса-контейнера `Facade`. Сумма строк со вложенными —
    6 482 при файле в 2 264 строки: 51 метод посчитан дважды (как часть класса
    и сам по себе), и именно это давало Σtext = 180 507 при файле 100 354 (×1,80).

    Границы важны: у `to_dict` и `close` конец СПАДАЕТ с концом контейнера
    (`to_dict` 208-227 внутри `Envelope` 194-227; `close` 2549-2552 внутри
    `Facade` 299-2552), поэтому строгое «конец меньше» их пропускал — дубль
    оставался в выдаче. Здесь вложенность считается нестрого (<=), но только
    при РАЗНЫХ границах: два одинаковых спана — не вложенность, а повтор,
    и лишний из них убирается отдельно по порядку следования.
    """
    keep: list[bool] = [True] * len(spans)
    for i, a in enumerate(spans):
        for j, b in enumerate(spans):
            if i == j or not keep[j]:
                continue
            if a[0] != b[0]:
                continue
            same = a[1] == b[1] and a[2] == b[2]
            if same:
                if j < i:
                    keep[j] = False
            elif a[1] <= b[1] and b[2] <= a[2]:
                keep[j] = False
    return [s for s, k in zip(spans, keep) if k]

LANG_OF_EXT = {".php": "php", ".md": "markdown", ".sql": "sql"}

# Кэш признака DEFAULT по (ревизия, таблица) и корень корпуса для чтения миграций.
_DEFAULT_CACHE: dict[str, set[str]] = {}
_CURRENT_ROOT: str | None = None


def _parse_array_shapes(docblock: str) -> dict[str, dict]:
    """Разобрать psalm-описания массивов из docblock'а: `array{key:type, key?:type}`.

    Возвращает {имя_параметра: {ключи}} — например для
    `@param array{action:string, user_id?:int} $data` будет
    {'data': {'action': {'type': 'string', 'required': True},
              'user_id': {'type': 'int', 'required': False}}}.

    Знак `?` после имени ключа означает необязательность — именно это отличает
    контракт от простого перечисления. Разбор нужен потому, что сигнатура
    `array $data` для агента бесполезна: она не говорит, какие ключи обязательны.
    """
    import re as _re

    out: dict[str, dict] = {}
    # Один @param на строку; массив может переноситься, поэтому берём многострочно.
    for m in _re.finditer(
        r"@param\s+array\s*\{(?P<body>[^}]*)\}\s*\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
        docblock,
        _re.DOTALL,
    ):
        keys: dict[str, dict] = {}
        # Из многострочного docblock в текст попадают ведущие символы комментария
        # ("* from:?string"): без очистки они становятся частью имени ключа.
        raw_body = m.group("body")
        raw_body = raw_body.replace("*", " ")
        body = " ".join(raw_body.split())
        for part in body.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            key, _, typ = part.partition(":")
            key = key.strip()
            optional = key.endswith("?")
            key = key.rstrip("?")
            if not key:
                continue
            keys[key] = {"type": typ.strip() or "mixed", "required": not optional}
        if keys:
            out[m.group("name")] = keys
    return out


#: Ключи, под которыми в ответах лежат СПИСКИ ФАКТОВ.
#:
#: Список пополняется ВМЕСТЕ с новой операцией, и это не формальность:
#: измерено на `search_multi` — ответ с 19 семействами и ссылками `path:line`
#: давал в сводке `usage` НОЛЬ фактов, потому что сборщик знал только старые
#: ключи (`references`, `files`, ...). Ноль фактов у операции, которая отдаёт
#: ссылки, делает метрику эффекта недостоверной — а по ней решают, окупается
#: ли индекс (пункт P2-4 ТЗ).
FACT_LIST_KEYS = (
    "references", "sql_literals", "affected_files", "stated", "derived",
    "files", "symbols", "columns", "steps", "nodes", "edges", "items", "hits",
    "matches", "direct", "indirect",
    # Ответы операций P1: поиск по нескольким запросам, копии, точки входа,
    # мёртвый код. Контейнеры (`queries`, `families`, `groups`) здесь НЕ
    # перечислены: это не факты, а оболочки — обход заходит внутрь них сам
    # и считает `hits`, `members`, `evidence`.
    "members", "member_details", "evidence",
    "referenced_in_text", "referenced_outside_hits", "entrypoints", "refuted",
    "referenced_by", "referenced_by_code", "referenced_by_text",
    # Ответы P2: сверка «код ↔ база» (что не нашлось, что не используется, кто
    # пишет), дифф ревизий (что добавилось/исчезло, кто потерял вызывающих) и
    # заметки. Без этих ключей новая операция снова показывала бы ноль фактов.
    "used_missing", "unused", "name_links", "write_paths", "case_mismatch",
    "changed", "added", "removed", "sources", "dynamic_sql",
)

#: Отбор чанков для сверки «код ↔ база» (db_cross). Фразовый запрос FTS по
#: ключевым словам SQL: полный проход текста на BWO (101 957 чанков) занимал
#: 41 с и делал операцию непригодной для вопроса «перед тем как писать SQL».
#: Лимит нужен как страховка от корпуса, где SQL встречается в каждом файле:
#: способ отбора и число просмотренных чанков возвращаются в ответе, поэтому
#: усечение видно, а не молчит.
SV_SQL_FTS_QUERY = "from OR insert OR update OR delete OR join"
SV_SQL_CHUNK_LIMIT = 40000
#: Чанк крупнее этого размера в разбор SQL не берётся. Замер на BWO: из 273 МБ
#: текста 71 % приходится на сотню чанков, а три самых больших — 110 МБ, 45 МБ
#: и 35 МБ (выгрузки и минифицированные библиотеки). Разбор их стоит десятки
#: секунд и даёт мусорные имена (`tables`, `country` из демо-данных DataTables),
#: а разработчик такие файлы не правит. Пропуск называется числом в ответе.
SV_SQL_MAX_CHUNK_CHARS = 400_000
#: Бюджет символов на текстовый проход: второя страховка (первая — размер чанка).
SV_SQL_TEXT_BUDGET = 20_000_000


def _collect_fact_items(value, out: list[dict], depth: int = 0) -> None:
    """Собрать словари-факты из вложенного ответа (до четырёх уровней).

    Глубина ограничена: ответы операций вложены не более чем на четыре уровня
    (`families → members → evidence`), а безлимитный обход рискует уйти в
    служебные структуры и посчитать одно и то же дважды.
    """
    if depth > 4:
        return
    if isinstance(value, dict):
        for key, val in value.items():
            if key in FACT_LIST_KEYS and isinstance(val, list):
                # Список фактов: его элементы считаются фактами, и внутрь них
                # обход идёт отдельно — там лежат вложенные ссылки
                # (`members[].evidence[]` с путём и строкой). Повторного счёта
                # не будет: каждый словарь встречается в ответе один раз.
                dicts = [x for x in val if isinstance(x, dict)]
                out.extend(dicts)
                for item in dicts:
                    _collect_fact_items(item, out, depth + 1)
                continue
            if isinstance(val, (dict, list)):
                _collect_fact_items(val, out, depth + 1)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                out.append(item)
                # Внутрь элемента тоже заходим: у семейства копий ссылки
                # лежат в `members[].evidence[]`, и без этого «фактов 5, со
                # строкой 0» — то есть ссылки есть, а проверить их нельзя.
                _collect_fact_items(item, out, depth + 1)
            elif isinstance(item, (list, dict)):
                _collect_fact_items(item, out, depth + 1)


def _answer_sources(items: list[dict], *, limit: int = 8) -> tuple[list[dict], int]:
    """Ссылки `path:line` для текстового ответа — и полное их число.

    Возврат: (первые `limit` ссылок, сколько их всего). Число нужно отдельно:
    ответ «показаны 8 из 37» честнее, чем молчаливые 8.
    """
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    total = 0
    for item in items:
        path = str(item.get("path") or item.get("evidence_path") or "")
        line = item.get("line", item.get("start_line", item.get("evidence_line")))
        if not path:
            continue
        key = (path, int(line or 0))
        total += 1
        if key in seen or len(out) >= limit:
            continue
        seen.add(key)
        out.append({"path": path, "line": int(line or 0),
                    "label": str(item.get("name") or item.get("fqn")
                                 or item.get("kind") or "")})
    return out, total


def _answer_text(title: str, sources: list[dict], total: int, *,
                 notes: list[str] | None = None) -> str:
    """Сформулированный ответ: заголовок, ссылки `файл:строка`, оговорки.

    Почему это отдельный режим, а не украшение. Замер по журналу обращений:
    средний ответ операции — около 1,5 КБ структурного JSON, из которого агент
    всё равно собирает фразу сам. Режим `answer` отдаёт готовую фразу и ссылки,
    укладываясь в 1–2 КБ (требование P2-2 ТЗ), и при этом не теряет
    проверяемости: каждая ссылка — `файл:строка`.
    """
    lines = [f"**{title}**"]
    if not sources:
        lines.append("Ничего не найдено: это ответ индекса, а не отказ операции.")
    for src in sources:
        label = f" — {src['label']}" if src.get("label") else ""
        lines.append(f"- `{src['path']}:{src['line']}`{label}")
    if total > len(sources):
        lines.append(f"…и ещё {total - len(sources)}; полный список — без `answer=true`.")
    for note in notes or []:
        lines.append(f"> {note}")
    return "\n".join(lines)


def summarize_result(data: dict | None) -> tuple[int, int, int]:
    """Сводка по результату операции: (фактов, фактов со строкой, символов).

    Факт — элемент результата, к которому может относиться ссылка на код.
    Фактом со строкой считается тот, у которого есть и путь, и номер строки:
    именно такие ответы агент может проверить по коду, а непроверяемые для нас
    бесполезны (ФТ-17).

    Символы считаются по сериализованному результату: это объём, который агент
    получил из индекса вместо чтения файлов.
    """
    if not data:
        return 0, 0, 0

    try:
        chars = len(_json.dumps(data.get("result"), ensure_ascii=False))
    except Exception:
        chars = 0

    # Разбор результата: либо список фактов, либо словарь с несколькими списками
    # (у impact, table_usage, repo_map факты лежат под разными ключами), либо
    # вложенная структура новых операций (families → members → evidence).
    items: list[dict] = []
    res = data.get("result")
    if isinstance(res, list):
        items = [x for x in res if isinstance(x, dict)]
    elif isinstance(res, dict):
        _collect_fact_items(res, items)

    spans = 0
    for item in items:
        path = item.get("path") or item.get("evidence_path") or item.get("file")
        line = item.get("line", item.get("start_line", item.get("evidence_line")))
        if path and line is not None:
            spans += 1
    return len(items), spans, chars


def _count_by(items) -> dict[str, int]:
    """Сколько раз встретилось каждое значение (детерминированный словарь)."""
    out: dict[str, int] = {}
    for it in items:
        key = str(it)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _activity_verdict(kn, path: str) -> str:
    """Короткий вердикт о файле: точка входа / рабочий / копия / мёртвый.

    Формулировки намеренно разделяют «достижим» и «есть входящие ссылки»:
    файл может быть достижим только потому, что его подключает другой
    мёртвый файл, и тогда называть его рабочим нельзя.
    """
    if kn.is_entrypoint(path):
        return "точка входа"
    if path in kn.reachable:
        return "достижим из точки входа"
    if kn.incoming(path):
        return "есть входящие ссылки, но из точек входа не достижим"
    return "мёртвый: нет входящих ссылок и достижимости"


@dataclass
class Envelope:
    op: str
    index_rev: str | None
    capability: str
    drift: dict
    provenance: str
    limits: list[str] = field(default_factory=lambda: list(DEFINED_LIMITS))
    result: Any = None
    stale_warnings: list[dict] = field(default_factory=list)
    found: bool = True
    error: str | None = None
    extra: dict = field(default_factory=dict)
    freshness: dict | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "op": self.op,
            "index_rev": self.index_rev,
            "capability": self.capability,
            "drift": self.drift,
            "provenance": self.provenance,
            "limits": self.limits,
            "result": self.result,
            "stale_warnings": self.stale_warnings,
            "found": self.found,
        }
        if self.error:
            out["error"] = self.error
        # Свежесть идёт рядом с ревизией: агент должен одновременно видеть,
        # на какую ревизию опирается ответ и совпадает ли она с кодом сейчас.
        if self.freshness is not None:
            out["freshness"] = self.freshness
        out.update(self.extra)
        return out


def _column_has_default(rev: str, table: str, column: str) -> bool:
    """Есть ли у колонки значение по умолчанию (по тексту SQL-миграций корпуса).

    Признак нужен, чтобы не объявлять обязательными колонки, которым СУБД сама
    подставляет значение (`Deleted DEFAULT 0`, `Status DEFAULT 'draft'`) — иначе
    проверка выдавала бы ложные «обязательные», и агент перестал бы ей доверять.

    Источник — файлы миграций корпуса, а не граф: SQL-текст в графе отдельным
    видом чанков не хранится (в нём только comment и docblock — замерено),
    а подключение к базе запрещено: индексация работает без СУБД.

    Разбор кэшируется по (ревизия, таблица): список колонок одной таблицы
    проверяется за один проход чтения файла.
    """
    key = f"{rev}|{table.lower()}"
    if key in _DEFAULT_CACHE:
        return column.lower() in _DEFAULT_CACHE[key]

    import re as _re
    from pathlib import Path as _Path

    # Карта колонок с DEFAULT по таблице: собирается один раз на таблицу.
    defaults: set[str] = set()
    autoincrement: set[str] = set()
    try:
        cfg_root = _CURRENT_ROOT
        if cfg_root:
            for path in sorted(_Path(cfg_root).glob("migrations/*.sql")):
                text = path.read_text(encoding="utf-8", errors="replace")
                m = _re.search(
                    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?"
                    + _re.escape(table) + r"[`\"]?\s*\(",
                    text, _re.IGNORECASE,
                )
                if not m:
                    continue
                # Тело таблицы — до парной закрывающей скобки.
                depth, i = 0, m.end() - 1
                while i < len(text):
                    if text[i] == "(":
                        depth += 1
                    elif text[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                body = text[m.end():i]
                # Признак AUTO_INCREMENT: в разборе миграций колонка с ним
                # не получает ключ PRI (ключ задаётся отдельной строкой
                # `PRIMARY KEY (ID)`), поэтому проверяется текст строки.
                for line in body.splitlines():
                    cm = _re.match(r"\s*[`\"]?([A-Za-z_][A-Za-z0-9_]*)[`\"]?\s", line)
                    if not cm:
                        continue
                    col_name = cm.group(1).lower()
                    if _re.search(r"\bAUTO_INCREMENT\b", line, _re.IGNORECASE):
                        autoincrement.add(col_name)
                    if _re.search(r"\bDEFAULT\b", line, _re.IGNORECASE):
                        defaults.add(col_name)
    except Exception:
        defaults = set()

    # Колонки с AUTO_INCREMENT тоже считаются «заполняемыми СУБД»: их значение
    # генерирует база, и требовать их от агента нельзя (иначе проверка давала бы
    # ложное «обязательная колонка ID не задана» — найдено прогоном).
    _DEFAULT_CACHE[key] = defaults | autoincrement
    return column.lower() in _DEFAULT_CACHE[key]


class Facade:
    """Все операции чтения. Индекс не изменяется (ФТ-24)."""

    def __init__(self, cfg: ProjectConfig, tool: str = "unknown",
                 telemetry: bool = True, watch: bool = True,
                 agent: str = "unknown", agent_kind: str = "unknown",
                 task: str = "", token_id: str = "",
                 token_label: str = "") -> None:
        """Фасад операций чтения.

        Параметры `tool` и `agent` РАЗНЫЕ и не заменяют друг друга:
          * `tool` — канал вызова: mcp, cli, admin, dev, selftest;
          * `agent` — имя агента, который работает с RAGRAF
            (например `AutoClaw`, `MD.Viewer`, `RdpAudit`);
          * `agent_kind` — семейство: `autoclaw`, `external` или `internal`.

        Разделение нужно для статистики: владелец хочет видеть как каждого
        агента отдельно, так и все агенты AutoClaw вместе. Раньше поле `tool`
        несло обе роли сразу, и отфильтровать по агенту было невозможно.
        """
        self.cfg = cfg
        self.tool = tool
        self.agent = agent
        self.agent_kind = agent_kind
        # Метка задачи: нужна, чтобы собрать вклад RAGRAF в одну задачу
        # пользователя (см. команду `/RAGRAF` в SagaAI). Если клиент её не задал,
        # берётся активный сеанс гейта: иначе CLI и MCP-вызовы попадали в
        # «(без задачи)», и метрика «доля задач с обращением к индексу» (§3.4)
        # считала бы провал там, где индексом пользовались. В документах корпуса z
        # четыре отчёта подряд фиксируют «инструментов `ragraf_*` в сеансе нет —
        # работал через CLI» — то есть MCP-только метрика показывала бы ноль.
        if not task:
            try:
                from . import gate as gate_mod

                # Порядок: названная задача (клиент её не передаёт — метку задаёт
                # агент вызовом `ragraf_task`), затем активный сеанс гейта.
                task = (gate_mod.state().current_task(str(cfg.root))
                        or gate_mod.active_task(cfg.root) or None)
            except Exception:  # noqa: BLE001 — привязка задачи не имеет права падать
                task = None
        self.task = task
        # TOKEN-ATTRIBUTION-PATCH: выданный доступ, которым пришёл вызов. Пусто — без него
        # (CLI, тесты, прежние записи).
        self.token_id = token_id
        self.token_label = token_label
        # Корень корпуса нужен разбору DEFAULT в миграциях — запоминается на модуль.
        global _CURRENT_ROOT
        _CURRENT_ROOT = str(cfg.root)
        self.control = ControlStore(cfg)
        self.graph = store.connect(cfg.graph_db, readonly=True)
        self.lexvec = LexVecStore(cfg)
        # Операции чтения НЕ скачивают модель: только кэш (иначе обычный запрос агента
        # неожиданно тянет сотни мегабайт из сети).
        self.embedder = Embedder(cfg, allow_download=False)
        self._rev_cache: dict[str, Any] | None = None
        # Отпечаток файла ревизии (время правки, размер): по нему видно, что
        # ревизию сменил ДРУГОЙ процесс, — см. `rev_info`.
        self._rev_stat: tuple[int, int] | None = None
        self._drift_cache: dict | None = None
        self._freshness: dict | None = None
        # Когда свежесть проверена последний раз: окно кэша дешёвой проверки.
        # Без него серия вызовов подряд обходила бы дерево корпуса каждый раз.
        self._fresh_checked_at: float = 0.0
        # Кэш центральности графа на ревизию: считается один раз, а не на
        # каждый вызов карты репозитория.
        self._graph_cache: dict[str, Any] | None = None
        self.category = ""
        self.watch_enabled = watch

        # Сторож контрольных сумм: перед работой сверяется, совпадает ли индекс
        # с кодом, и при расхождении выполняется реиндексация. Требование
        # владельца: агент не должен получать факты о прошлом состоянии кода,
        # не зная об этом. Проверка кэшируется на несколько секунд, поэтому
        # серия вызовов не сканирует корпус каждый раз.
        if self.watch_enabled:
            # Дешёвая сверка по отпечатку, а полная — только если он разошёлся.
            # Иначе создание фасада читало бы весь корпус при каждом запуске.
            self._ensure_fresh()

        # Телеметрия: журнал обращений для замера эффекта от внедрения.
        # Отключается парметром telemetry=False для модельных прогонов тестов,
        # чтобы проверки не засоряли статистику.
        self.telemetry = (
            Telemetry(self.control, tool=tool, agent=agent,
                      agent_kind=agent_kind, task=task,
                      token_id=token_id, token_label=token_label)
            if telemetry else None
        )
        if self.telemetry is not None:
            self._install_telemetry()
        # Сторож на каждый вызов ставится ВСЕГДА, независимо от журнала: это
        # разные обязанности. Свежесть — гарантия ответа, журнал — статистика.
        self._install_guard()

    def _centrality(self, rev: str) -> dict[str, Any]:
        """Центральность узлов по файлам (PageRank на разрежённой матрице).

        Зачем: архитектура (ARCHITECTURE.md, §7, операция `repo_map`) требует
        ранжировать файлы «по связности и по „центральности“ в графе», а код
        сортировал их только по числу символов. Это разные величины: файл с
        многими мелкими символами обгонял файл, на который опирается вся
        остальная кодовая база.

        Считается лениво и кэшируется на ревизию: граф и результат не меняются
        в пределах ревизии (инвариант ФТ-24), поэтому второй вызов бесплатен.
        Любая ошибка расчёта не роняет операцию: карта репозитория не должна
        становиться недоступной из-за отсутствия torch. В этом случае
        возвращается пустая карта оценок и причина — вызывающая сторона обязана
        сказать об этом в `limits`, а не выдать понижение за норму.
        """
        cached = self._graph_cache
        if cached is not None and cached.get("rev") == rev:
            return cached
        result: dict[str, Any] = {
            "rev": rev, "scores": {}, "scores_ordered": [], "status": None, "error": ""
        }
        try:
            from . import graph_algo

            arrays = graph_algo.load_graph(self.graph, rev)
            scores, compute = graph_algo.pagerank(arrays, self.cfg)
            ordered = graph_algo.centrality_by_file(arrays, scores)
            result["scores"] = dict(ordered)
            result["scores_ordered"] = ordered
            result["status"] = compute.to_dict()
            result["nodes"] = arrays.n
            result["edges"] = arrays.m
        except Exception as exc:  # noqa: BLE001 — карта не должна падать из-за графа
            result["error"] = f"{type(exc).__name__}: {exc}"
        self._graph_cache = result
        return result

    def graph_compute_state(self) -> dict[str, Any]:
        """Состояние графового вычислителя — для `index_status`, не для правки.

        Показывает, где считаются графовые алгоритмы и почему (карта, порог
        окупаемости, отсутствие torch), плюс время и объём графа. Без этого
        настройка `graph_gpu` оставалась бы значением без наблюдаемого эффекта.
        """
        rev = self.current_rev()
        if not rev:
            return {}
        data = self._centrality(rev)
        out: dict[str, Any] = {
            "nodes": data.get("nodes", 0),
            "edges": data.get("edges", 0),
            "files_ranked": len(data.get("scores") or {}),
        }
        if data.get("status"):
            out.update(data["status"])
        if data.get("error"):
            out["error"] = data["error"]
        return out

    def _install_telemetry(self) -> None:
        """Обернуть ВСЕ операции фасада записью в журнал.

        Сделано обёрткой, а не правкой 17 методов: так нельзя забыть добавить
        запись в новую операцию. Обёртка ничего не меняет в результате и при
        любой своей ошибке возвращает исходный ответ как есть.
        """
        for name in list(vars(type(self))):
            if not name.startswith("op_") or name.startswith("op__"):
                continue
            method = getattr(self, name)
            if not callable(method):
                continue
            setattr(self, name, self._wrap_op(name, method))

    def _wrap_op(self, name: str, method):
        """Обёртка одной операции: замер времени и запись в журнал."""

        def wrapped(*args, **kwargs):
            t0 = time.perf_counter()
            error: str | None = None
            try:
                data = method(*args, **kwargs)
            except Exception as exc:   # ошибка операции пишется в журнал и прокидывается
                error = f"{type(exc).__name__}"
                self._record(name, 0.0, {}, error, args, kwargs)
                raise
            self._record(name, (time.perf_counter() - t0) * 1000.0, data, None, args, kwargs)
            return data

        return wrapped

    def _install_guard(self) -> None:
        """Обернуть ВСЕ операции серверной проверкой свежести.

        Почему обёртка, а не вызов внутри каждой операции. Требование владельца:
        устаревший индекс не должен попадать в ответ НИКОГДА, и это гарантия
        сервера, а не правило поведения агента, которое можно не выполнить.
        Процесс MCP живёт долго: правка кода в середине сессии до следующего
        явного `ensure` иначе остаётся невидимой, и ответ приходит о прошлом
        состоянии кода (измерено — ради этого и заведён сторож).

        `op_freshness` пропускается: это и есть сама проверка, оборачивать её
        значило бы делать двойную работу.
        """
        for name in list(vars(type(self))):
            if not name.startswith("op_") or name.startswith("op__"):
                continue
            if name == "op_freshness":
                continue
            method = getattr(self, name)
            if not callable(method):
                continue
            setattr(self, name, self._wrap_guard(name, method))

    def _wrap_guard(self, name: str, method):
        """Обёртка одной операции: сверка свежести ПЕРЕД работой."""

        def wrapped(*args, **kwargs):
            self._ensure_fresh()
            return method(*args, **kwargs)

        return wrapped

    def _ensure_fresh(self) -> None:
        """Свежесть перед операцией: сначала дешёвый отпечаток, потом полная сверка.

        Порядок по цене, а не по важности. `watch.quick_check` отвечает «вход не
        менялся» по путям, размерам и времени правки, НЕ читая файлы корпуса.
        Если доказать не удалось — идёт обычный `_run_guard()`: полная сверка
        контрольных сумм и, при расхождении, переиндексация.

        Результат дешёвой проверки кэшируется на то же окно, что и полная
        (`FRESHNESS_TTL_SEC`): серия вызовов подряд не должна обходить дерево
        каждый раз, но правка, сделанная агентом, обязана быть замечена в
        пределах окна.
        """
        if not self.watch_enabled:
            return
        try:
            from . import watch as watch_mod

            cached = self._freshness or {}
            if (cached.get("verified_by") == "fingerprint"
                    and (time.time() - self._fresh_checked_at) < watch_mod.FRESHNESS_TTL_SEC):
                return
            quick = watch_mod.quick_check(self.cfg)
            if quick is not None:
                # Смена ревизии ДРУГИМ процессом видна и на дешёвом пути: если
                # отпечаток говорит о другой ревизии, кэш ревизии и соединения
                # обязаны обновиться, иначе фасад ответит прошлым состоянием
                # (см. `rev_info`).
                quick_rev = str(getattr(quick, "rev_id", "") or "")
                if quick_rev and quick_rev != (self.current_rev() or ""):
                    self._reopen_stores()
                    self._rev_cache = None
                    self._rev_stat = None
                self._freshness = quick.to_dict()
                self.category = quick.category
                self._fresh_checked_at = time.time()
                return
        except Exception as exc:  # noqa: BLE001 — дешёвый путь не имеет права падать
            # Не смогли доказать дёшево — это не ошибка, а повод пойти полным
            # путём. Молчать нельзя: причина видна в stderr.
            print(f"[ragraf-facade] дешёвая сверка не удалась: {type(exc).__name__}", file=sys.stderr)
        self._run_guard()

    def _record(self, name: str, duration_ms: float, data: dict, error: str | None,
                args: tuple = (), kwargs: dict | None = None) -> None:
        """Записать одну операцию в журнал (никогда не ломает ответ).

        Первичный аргумент сохраняется в виде хеша и короткого дайджеста:
        без него нельзя отличить запрос схемы одной таблицы от другой, а значит
        нельзя измерить эффект улучшений отчёта.
        """
        if self.telemetry is None:
            return
        # Хранилище только для чтения (чужой каталог без права записи): запись
        # невозможна, и утверждать, что обращение записано, нельзя. Панель
        # читает такую разработку ради статистики, а не ради её пополнения.
        if getattr(self.control, "readonly", False):
            return
        try:
            facts, spans, chars = summarize_result(data)
            saved_chars = self._saved_chars(data)
            # Первичный аргумент — самая содержательная часть вызова: имя
            # символа, таблицы, поисковый запрос. Для операций без аргумента
            # (index_status, repo_map) он пуст — это норма.
            primary = ""
            if args:
                first = args[0]
                if isinstance(first, (str, int)):
                    primary = str(first)
            arg_hash, arg_digest = normalize_arg(primary)
            # Метрики P2-4: ответ с пометкой устаревания и честное «не найдено»
            # без ошибки. Считаются здесь, потому что только обёртка видит
            # ПОЛНЫЙ ответ (`stale_warnings` и `found`).
            stale = bool(data.get("stale_warnings")) if data else False
            # Ответ, отданный по ревизии, которая уже не активна, — это тот же
            # stale, даже если `stale_warnings` пуст: измерено 14.10.2026, когда
            # долгоживущий сервер отвечал по `rev-0106` при активной `rev-0120`.
            # Без этой проверки метрика `stale_served = 0` остаётся зелёной и не
            # осматривает самый опасный класс ответов.
            if not stale and data:
                answered = str(data.get("index_rev") or "")
                active = self.current_rev() or ""
                if answered and active and answered != active:
                    stale = True
                    data.setdefault("stale_warnings", []).append({
                        "path": "",
                        "reason": "ответ отдан по неактивной ревизии",
                        "suggest": f"активная ревизия {active}, ответ по {answered}",
                    })
            missed = bool(data) and not bool(data.get("found")) and not error
            self.telemetry.record(
                CallRecord(
                    op=name[3:] if name.startswith("op_") else name,
                    rev_id=data.get("index_rev") if data else self.current_rev(),
                    capability=str(data.get("capability") or self.capability()) if data else self.capability(),
                    duration_ms=round(duration_ms, 3),
                    found=bool(data.get("found")) if data else False,
                    fact_count=facts,
                    facts_with_span=spans,
                    result_chars=chars,
                    error=error,
                    tool=self.tool,
                    arg_hash=arg_hash,
                    stale=stale,
                    missed=missed,
                    arg_digest=arg_digest,
                    replaces_chars=saved_chars,
                )
            )
        except Exception:
            pass

    def _saved_chars(self, data: dict, limit: int = 200) -> int:
        """Сколько символов агент не прочитал благодаря этому ответу (шаг 0.6).

        Считается по ФАКТИЧЕСКИ выданным путям: уникальные `path`/`file` из
        ответа, размер каждого файла. Это нижняя оценка экономии — файл целиком
        обычно больше процитированного фрагмента, поэтому завышения здесь быть
        не может, только занижение.

        Оценка намеренно грубая и помечена как оценка: размер берётся в байтах
        (для UTF-8 кириллица даёт больше байт, чем символов). Выдавать её за
        точное число символов нельзя, и в отчёте она названа «символы (по
        размеру файла)».
        """
        if not data:
            return 0
        paths: list[str] = []
        seen: set[str] = set()

        def walk(node, depth: int = 0) -> None:
            if depth > 6 or len(seen) >= limit:
                return
            if isinstance(node, dict):
                for key in ("path", "file", "source_path", "doc_path"):
                    value = node.get(key)
                    if isinstance(value, str) and value and value not in seen:
                        seen.add(value)
                        paths.append(value)
                for value in node.values():
                    walk(value, depth + 1)
            elif isinstance(node, list):
                for value in node[:limit]:
                    walk(value, depth + 1)

        try:
            walk(data)
        except Exception:  # noqa: BLE001 — оценка не имеет права ломать ответ
            return 0
        if not paths:
            return 0
        from pathlib import Path as _Path

        root = _Path(self.cfg.root)
        total = 0
        for raw in paths:
            candidate = _Path(raw)
            if not candidate.is_absolute():
                candidate = root / raw
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
        return int(total)

    def record_external_call(self, op: str, data: dict, duration_ms: float) -> None:
        """Записать в журнал операцию, выполненную ВНЕ фасада (никогда не ломает ответ).

        Зачем. `ensure` и `use_project` живут в MCP-сервере: `ensure` умеет
        переиндексировать корпус, а фасад только читает готовый индекс, и
        `use_project` меняет конфиг самого сервера. Обёртка телеметрии
        навешивается на методы `op_*` фасада, поэтому эти две операции
        в журнал НЕ попадали вовсе — измерено 13.10.2026: в сводке `usage`
        есть `index_status` и `catalog`, а `ensure` с `use_project` отсутствуют,
        хотя вызывались из того же процесса. Прямое следствие: нельзя было
        ответить на вопрос «соблюдают ли агенты правило первого шага», потому что
        первый шаг и был невидим.

        Запись идёт той же точкой `Telemetry.record`, чтобы сводка по агенту,
        задачам и операциям оставалась единой.
        """
        if self.telemetry is None:
            return
        try:
            self._record(f"op_{op}", duration_ms, data or {}, None)
        except Exception:  # noqa: BLE001 — учёт не должен ломать ответ операции
            pass

    # ---------------- слой баз данных ----------------

    def op_db_schema(self, table: str | None = None, database: str | None = None,
                     limit: int = 200) -> dict:
        """23. Структура живых баз: таблицы, колонки, типы, индексы.

        Источник — живая СУБД, а не миграции. Расхождение с `op_schema`
        (которая читает миграции) само по себе находка: оно показывает, что
        миграции и база разошлись.
        """
        from . import db_ops

        env = self.make_env("db_schema")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = db_ops.schema(self.graph, rev, table=table, database=database,
                             limit=max(1, min(int(limit), 500)))
        env.result = {"tables": rows, "info": db_ops.summary(self.graph, rev)}
        env.found = bool(rows)
        if not rows:
            env.error = ("слой баз не собран для этой ревизии: включите сбор в "
                         "разделе «Базы данных» панели")
        return env.to_dict()

    def op_db_table(self, table: str, database: str | None = None) -> dict:
        """24. Одна таблица подробно: колонки, индексы, связи, скрытые поля."""
        from . import db_ops

        env = self.make_env("db_table")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        detail = db_ops.table_detail(self.graph, rev, table, database)
        if detail is None:
            env.found = False
            env.error = f"таблица не найдена в слое баз: {table}"
            return env.to_dict()
        env.result = detail
        env.found = True
        return env.to_dict()

    def op_db_relations(self, table: str) -> dict:
        """25. Связи таблицы: объявленные и выведенные по именам — раздельно."""
        from . import db_ops

        env = self.make_env("db_relations")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        data = db_ops.relations(self.graph, rev, table)
        env.result = data
        env.found = bool(data.get("stated") or data.get("derived"))
        if not env.found:
            env.extra["note"] = ("связей не найдено: у таблицы нет объявленных "
                                 "внешних ключей и не нашлось совпадений по именам")
        return env.to_dict()

    def op_db_sample(self, table: str, database: str | None = None) -> dict:
        """26. Пример строк таблицы: снимок с пометкой устаревания."""
        from . import db_ops

        env = self.make_env("db_sample")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        data = db_ops.sample(self.graph, rev, table, database)
        if data is None:
            env.found = False
            env.error = f"пример для таблицы не найден: {table}"
            return env.to_dict()
        env.result = data
        env.found = True
        return env.to_dict()

    def op_db_drift(self, previous_rev: str | None = None) -> dict:
        """27. Что изменилось: между ревизиями И между миграциями и базой (P1-4).

        Два разных вопроса в одном ответе, и они не заменяют друг друга:
          * `drift` — что изменилось в ЖИВОЙ базе между двумя ревизиями индекса;
          * `migrations_vs_live` — какие таблицы и колонки описаны миграциями,
            но отсутствуют в живой базе (миграция не применена) и наоборот.

        Второй вопрос до этой правки не задавался никем: `db_drift` сравнивал
        две ревизии, `db_cross` — живую базу с именами таблиц из кода, а
        «миграции против базы» не проверял ни один инструмент.
        """
        from . import db_ops

        env = self.make_env("db_drift")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        prev = previous_rev or self._previous_rev(rev) or rev
        data = db_ops.drift(self.graph, rev, prev)
        live = db_ops.migrations_vs_live(self.graph, rev)
        env.result = {"from": prev, "to": rev, "drift": data,
                      "migrations_vs_live": live}
        env.found = True
        if prev == rev:
            env.extra["note"] = ("предыдущая ревизия не найдена: сравнение пустое, "
                                 "это не значит «ничего не менялось»")
        if not live["plausible"]:
            env.limits.append(live["note"])
        env.extra["migrations_only"] = len(live["only_in_migrations"])
        env.extra["live_only"] = len(live["only_live"])
        return env.to_dict()

    def op_db_cross(self) -> dict:
        """28. Сверка «код ↔ база»: что не сходится и кто что делает.

        Имена таблиц из кода приходят с ДОКАЗАТЕЛЬСТВАМИ: рёбра `@table:` —
        факт разбора языка (имя, вид доступа, `path:line`), текстовый образец
        SQL — эвристика для корпусов без рёбер. Смешивать их молча нельзя,
        поэтому у каждого имени назван источник.

        Отдельно называется СЛЕПАЯ ЗОНА: места, где имя таблицы собирается во
        время выполнения (`FROM $table`, `FROM " . $t . "`). Без них ответ
        «таблица в коде не встречается» читался бы как «можно удалять», хотя
        статический разбор там просто бессилен.
        """
        from . import db_ops

        env = self.make_env("db_cross")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        facts, detail = self._code_table_facts(rev)
        data = db_ops.cross_check(self.graph, rev, facts)
        env.result = {
            **data,
            "dynamic_sql": detail["dynamic"],
            "code_names": detail["names_in_code"],
        }
        env.found = bool(data.get("used_missing") or data.get("unused"))
        env.extra["used_sources"] = {
            "tables_in_code": detail["names_in_code"],
            "names_from_edges": detail["from_edges"],
            "edge_mentions": detail["edge_mentions"],
            "names_from_text_only": detail["from_text_only"],
            "edges_used": detail["edges_used"],
            "chunks_scanned": detail["chunks_scanned"],
            "chunks_skipped_large": detail["chunks_skipped_large"],
            "text_chars_scanned": detail["text_chars_scanned"],
            "dynamic_sites": detail["dynamic_sites"],
            "note": (
                "имена из рёбер — факт разбора кода (с видом доступа); имена "
                "«только из текста» — текстовый образец SQL, он может поймать имя "
                "в комментарии; «dynamic_sql» — места, где имя таблицы вычисляется "
                "во время выполнения и статически неизвестно"
            ),
        }
        if not detail["edges_used"]:
            env.limits.append(
                "рёбер @table: в ревизии нет (корпус собран до их появления): "
                "имена таблиц взяты только текстовым образцом — поможет переиндексация"
            )
        if detail["dynamic_sites"]:
            env.limits.append(
                f"мест с именем таблицы, вычисляемым во время выполнения: "
                f"{detail['dynamic_sites']} (полный список — в `dynamic_sql`); "
                f"по ним статический вывод «таблица не используется» недопустим"
            )
        if detail["chunks_skipped_large"]:
            env.limits.append(
                f"чанков крупнее {SV_SQL_MAX_CHUNK_CHARS} символов пропущено: "
                f"{detail['chunks_skipped_large']} — это выгрузки и минифицированные "
                f"библиотеки; SQL в них не разбирается намеренно"
            )
        if detail["text_scan_capped"]:
            env.limits.append(
                f"текстовый проход остановлен по бюджету {SV_SQL_TEXT_BUDGET} "
                f"символов: часть файлов не просмотрена, имена таблиц в них не "
                f"проверены"
            )
        if data.get("case_mismatch"):
            env.limits.append(
                f"имён, совпавших с базой только по регистру: "
                f"{len(data['case_mismatch'])} — на регистрозависимой базе такой "
                f"запрос таблицу не найдёт"
            )
        return env.to_dict()

    def _previous_rev(self, rev: str) -> str | None:
        """Предыдущая ревизия по номеру: порядок задаёт нумерация, не время.

        Таблица `rev_history` лежит в CONTROL-хранилище, а не в графе: первая
        редакция спрашивала её у графа и падала с `no such table: rev_history`
        (измерено). Это разные базы, и запрос обязан идти в свою.
        """
        try:
            row = self.control.conn.execute(
                "SELECT rev_id FROM rev_history WHERE rev_id < ? "
                "ORDER BY rev_id DESC LIMIT 1", (rev,)).fetchone()
        except Exception:
            return None
        if not row:
            return None
        try:
            return str(row["rev_id"])
        except Exception:
            return str(row[0])

    def _code_table_facts(self, rev: str) -> tuple[dict[str, dict], dict]:
        """Таблицы в коде С ДОКАЗАТЕЛЬСТВАМИ: что за имя, откуда и что с ним делают.

        Два источника, и они не равноценны:

          * **рёбра `@table:`** — их ставит извлекатель языка (PHP/Perl/SQL)
            после разбора литерала: имя, вид доступа (`select`, `insert`,
            `update`, `delete`, `ddl`) и способ вызова вычислены грамматикой.
            Это ФАКТ из кода;
          * **текстовый образец** — разбор законченных SQL-конструкций по
            сохранённому тексту чанков. Нужен для корпусов, собранных до
            появления рёбер, и как дополнение там, где извлекатель литерал не
            увидел. Это ЭВРИСТИКА (замер: 73 ложные находки на 86 таблиц, см.
            ловушку 31) — поэтому имена «только из текста» отдаются отдельно.

        Зачем ФАКТЫ, а не имена. Ответ «таблица X не найдена в базе» бесполезен,
        если агент не может проверить, откуда взялось имя: строка в комментарии
        и настоящий `INSERT INTO` выглядят одинаково. Поэтому каждое имя несёт
        `path:line` и текст конструкции, а для рёбер — ещё и вид доступа.

        Возврат: (`facts` по имени в нижнем регистре, разбор по источникам).
        Каждый факт: `{"name", "edges": [{path, line, access, via, sql}],
        "text": [{path, line, sql}]}`.
        """
        facts: dict[str, dict] = {}

        def fact(name: str) -> dict:
            key = name.lower()
            item = facts.get(key)
            if item is None:
                item = {"name": name, "edges": [], "text": []}
                facts[key] = item
            return item

        # --- 1. Рёбра: факт разбора кода ---
        edges_used = 0
        try:
            for row in self.graph.execute(
                "SELECT dst, props, evidence_path, evidence_line FROM edge"
                " WHERE rev_id = ? AND dst >= '@table:' AND dst < '@tablf'",
                (rev,),
            ).fetchall():
                name = str(row["dst"])[len("@table:"):].strip()
                if not name or name.startswith("_"):
                    continue
                props: dict = {}
                raw = row["props"]
                if raw:
                    try:
                        props = _json.loads(raw) if isinstance(raw, str) else dict(raw)
                    except Exception:
                        props = {}
                access = str(props.get("access") or props.get("write") or "").strip()
                item = fact(name)
                item["edges"].append({
                    "path": str(row["evidence_path"] or ""),
                    "line": row["evidence_line"],
                    "access": access,
                    "via": str(props.get("via") or ""),
                    "sql": str(props.get("sql") or "")[:160],
                })
                edges_used += 1
        except Exception:
            pass

        # --- 2. Текст чанков: имена, доказательства и СЛЕПЫЕ ЗОНЫ ---
        #
        # Отбор чанков идёт через FTS, а не полным проходом: на BWO полный
        # проход 101 957 чанков занимал 41 с, и операция становилась
        # непригодной для вопроса «перед тем как писать SQL». Фразовый отбор по
        # ключевым словам SQL берёт только те чанки, где запрос вообще может
        # быть, и стоит доли секунды.
        #
        # Слой баз (`db://…`) исключён сознательно: это описания таблиц, а не
        # код. Если считать их «кодом», каждая таблица «используется» сама
        # собой, и сверка «код ↔ база» теряет смысл.
        text_names: set[str] = set()
        dynamic: list[dict] = []
        chunks = 0
        skipped_large = 0
        scanned_chars = 0
        capped = False
        method = "fts"
        sql = (
            "SELECT t.text, m.path, COALESCE(m.start_line, 1) AS start_line,"
            " COALESCE(m.lang, '') AS lang"
            " FROM ft_chunk f"
            " JOIN chunk_map m ON m.rev_id = f.rev_id AND m.chunk_id = f.chunk_id"
            " JOIN chunk_text t ON t.rev_id = f.rev_id AND t.chunk_id = f.chunk_id"
            " WHERE f.rev_id = ? AND ft_chunk MATCH ?"
            " AND m.path NOT LIKE 'db://%'"
            # Порядок — от МАЛЕНЬКИХ чанков к большим: тогда бюджет символов
            # тратится на файлы, которые разработчик правит, а не на выгрузку
            # на 110 МБ, которая всё равно будет пропущена по размеру.
            " ORDER BY LENGTH(t.text) ASC LIMIT ?"
        )
        try:
            rows = self.lexvec.conn.execute(
                sql, (rev, SV_SQL_FTS_QUERY, SV_SQL_CHUNK_LIMIT)).fetchall()
        except Exception:
            # FTS может быть недоступен (старое хранилище): тогда честный
            # полный проход, но с ограничением и названным способом отбора.
            method = "scan"
            try:
                rows = self.lexvec.conn.execute(
                    "SELECT t.text, m.path, COALESCE(m.start_line, 1) AS start_line,"
                    " COALESCE(m.lang, '') AS lang"
                    " FROM chunk_text t LEFT JOIN chunk_map m"
                    " ON m.rev_id = t.rev_id AND m.chunk_id = t.chunk_id"
                    " WHERE t.rev_id = ? AND t.path NOT LIKE 'db://%' LIMIT ?",
                    (rev, SV_SQL_CHUNK_LIMIT)).fetchall()
            except Exception:
                rows = []
        for row in rows:
            text = row["text"] or ""
            if not text:
                continue
            if len(text) > SV_SQL_MAX_CHUNK_CHARS:
                skipped_large += 1
                continue
            if scanned_chars >= SV_SQL_TEXT_BUDGET:
                capped = True
                break
            scanned_chars += len(text)
            chunks += 1
            path = str(row["path"] or "")
            start_line = int(row["start_line"] or 1)
            lang = str(row["lang"] or "")
            for item in analysis.sql_table_names(text, start_line, lang=lang):
                rec = fact(item["name"])
                # Повтор того же места не добавляется: один и тот же литерал
                # попадает в несколько окон чанка при перехлёсте.
                if any(e["path"] == path and e["line"] == item["line"]
                       for e in rec["text"]):
                    continue
                rec["text"].append({"path": path, "line": item["line"],
                                    "sql": item["sql"]})
                text_names.add(item["name"].lower())
            for site in analysis.dynamic_sql_sites(text, start_line, lang=lang):
                dynamic.append({"path": path, **site})
        detail = {
            "from_edges": sum(1 for f in facts.values() if f["edges"]),
            "names_in_code": len(facts),
            "edge_mentions": edges_used,
            "from_text_only": len([
                n for n, f in facts.items() if f["text"] and not f["edges"]
            ]),
            "edges_used": edges_used > 0,
            "chunks_scanned": chunks,
            "chunks_skipped_large": skipped_large,
            "text_chars_scanned": scanned_chars,
            "text_scan_capped": capped,
            "text_method": method,
            "dynamic_sites": len(dynamic),
            "dynamic": dynamic[:100],
        }
        return facts, detail

    def _code_table_names(self, rev: str) -> tuple[set[str], dict]:
        """Совместимость: только множество имён (без доказательств).

        Оставлено как тонкая обёртка: у операции есть второй потребитель —
        проверка осмысленности сравнения, которой доказательства не нужны.
        """
        facts, detail = self._code_table_facts(rev)
        return {str(f["name"]) for f in facts.values()}, detail

    def usage_summary(self, since_session: bool = False,
                      agent: str | None = None,
                      agent_kind: str | None = None,
                      task: str | None = None,
                      scope: str = "",
                      session_id: str | None = None) -> dict:
        """Сводка использования (операция 18: статистика эффекта от внедрения).

        Три режима, заданных владельцем:
          * без параметров — общая по всем агентам;
          * `agent="AutoClaw"` — только этот агент;
          * `agent_kind="autoclaw"` — все агенты AutoClaw вместе.

        Плюс область (`scope`), которой не хватало интегратору (B7):
          * `scope="session"` — только эта сессия процесса (или `session_id`);
          * `scope="agent"` — только агент этого процесса;
          * `scope="all"` (по умолчанию) — весь журнал.

        Поле `calls_in_session` заполняется в режиме сессии: именно им
        проверяется утверждение «журнал видит мои вызовы». Если запись в журнал
        срывалась, в ответе будет ненулевое `write_failures` с причиной —
        потеря не выдаётся за «почти не пользовались».

        ВАЖНО: чтение сводки НЕ зависит от флага telemetry. Раньше здесь
        стояла проверка `if self.telemetry is None`, и любой вызов с
        telemetry=False (например диагностика или проверка) получал ответ
        «телеметрия отключена» — то есть нельзя было ПРОЧИТАТЬ статистику,
        не записав новую сессию. Это разные действия, и смешивать их нельзя:
        проверка статистики не должна её менять.

        Поэтому сводка читается прямо из журнала через ControlStore.
        """
        # Сводка читается независимо от того, пишется ли текущая сессия.
        if self.telemetry is not None:
            return self.telemetry.summary(
                since_session=since_session, agent=agent,
                agent_kind=agent_kind, task=task,
                scope=scope, session_id=session_id,
            )
        # Телеметрия выключена: сводка всё равно читается из журнала —
        # проверка статистики не должна её менять.
        #
        # Раньше здесь создавался «читатель» без вызова _ensure_schema, и это
        # давало падение `no such column: task` на корпусе, где за последнюю
        # миграцию ни один вызов не писал телеметрию: схема журнала создаётся
        # и дополняется только в Telemetry.__init__, а сводка сразу
        # обращается к колонкам agent/agent_kind/task. Замер: на корпусе
        # z.batteryservice.ru usage через фасад с telemetry=False падал,
        # хотя с telemetry=True тот же вызов работал — то есть ответ
        # зависел от служебного флага, а не от данных.
        #
        # Поэтому схема и догоняющая миграция выполняются явно (только они,
        # без старта сессии): чтение статистики не должно добавлять запись
        # в тот журнал, который она же и показывает.
        from .telemetry import Telemetry

        reader = Telemetry.for_reading(self.control)
        return reader.summary(
            since_session=since_session, agent=agent,
            agent_kind=agent_kind, task=task,
            scope=scope, session_id=session_id,
        )

    # ---------------- сторож и реестр ----------------

    def op_freshness(self, force: bool = False) -> dict:
        """20. Свежесть индекса: совпадает ли контрольная сумма корпуса с кодом.

        Смысл операции — дать агенту явный ответ, можно ли доверять фактам
        прямо сейчас. При force=true проверка идёт минуя кэш: это нужно сразу
        после правки кода, чтобы убедиться, что реиндексация сработала.
        """
        env = self.make_env("freshness")
        try:
            from . import watch as watch_mod

            fresh = watch_mod.check(self.cfg, auto_reindex=True, force=force)
            self._freshness = fresh.to_dict()
            env.result = fresh.to_dict()
            # Свежесть подмешивается в сам ответ: операция обязана сообщать
            # состояние индекса так же, как и любая другая — чтобы наличие
            # поля freshness было единообразным во всех ответах.
            env.freshness = self._freshness
            env.found = True
        except Exception as exc:
            env.found = False
            env.error = f"{type(exc).__name__}: {exc}"
        return env.to_dict()

    def op_route(self, query: str, limit: int = 10, verify: bool = True) -> dict:
        """39. Маршрут: где лежат материалы и какой локальный индекс отвечает.

        Операция намеренно не читает содержимое файлов и не цитирует документы.
        Её ответ — кандидаты с причиной выбора и сведениями о локальном индексе.
        Провенанс `registry`, а не `index`: факты приходят из реестра разработок
        и карты метаданных, и выдавать их за факты индекса нельзя.
        """
        env = self.make_env("route", provenance="registry")
        from .route import route as build_route

        data = build_route(query, limit=limit, verify=verify)
        env.result = data
        env.found = bool(data.get("candidates"))
        env.freshness = self._freshness
        if not env.found:
            env.extra["suggest"] = ["sources", "map", "ensure"]
            env.extra["note"] = (
                "ни одного совпадения по имени/пути: карта может быть не построена, "
                "а вопрос — требовать поиска по содержимому (`search`)"
            )
        return env.to_dict()

    def op_neighbors(self, query: str = "", name: str = "", limit: int = 20,
                     per_corpus: int = 5, stack: str | list[str] | None = None,
                     min_shared: int = 1, with_text: bool = True,
                     verify: bool = True,
                     kinds: list[str] | None = None,
                     answer: bool = False) -> dict:
        """40. Соседние контуры: готовая реализация того же стека.

        Зачем операция. `search` отвечает только по текущему корпусу, `route`
        говорит «где лежит», не читая содержимого. Поэтому функция, уже
        работающая в соседнем проекте на том же стеке, писалась заново: агент
        не мог узнать о её существовании. Здесь поиск идёт по индексам
        СОСЕДНИХ корпусов, отобранных по ФАКТИЧЕСКОМУ стеку.

        Границы, которые нельзя размывать:
          * чужой корпус только читается (`readonly`), не переиндексируется и
            не меняется; отставший индекс помечается `stale` с причиной,
            восстановление — отдельное действие `ensure <корень>`;
          * у каждого хита СВОЯ `index_rev` — ревизия корпуса-источника;
            `index_rev` конверта относится только к текущему корпусу;
          * вектор применяется лишь там, где модель соседа совпадает с текущей:
            на чужой геометрии ранжирование было бы ложным, хотя выглядело бы
            работающим.
        """
        env = self.make_env("neighbors")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        from . import neighbors as neighbors_mod

        text = str(query or "").strip()
        vector = None
        if text and self.embedder.available():
            try:
                vector = self.embedder.embed_one(text)
            except Exception:  # noqa: BLE001 — без вектора остаётся лексика
                vector = None
        data = neighbors_mod.find(
            self.cfg, rev, query=text, name=str(name or ""), limit=limit,
            per_corpus=per_corpus, stack=stack, min_shared=min_shared,
            with_text=with_text, verify=verify, vector=vector,
            mine=neighbors_mod.lang_profile(self.graph, rev),
            kinds=kinds,
        )
        limits = data.pop("limits", [])
        env.limits.extend(limits)
        env.result = data
        env.found = bool(data.get("hits"))
        if not env.found:
            env.extra["suggest"] = ["search", "symbol", "route"]
            env.extra["note"] = (
                "готовой реализации в соседях не найдено: в skipped названо, "
                "кого не спросили и почему; стек можно задать явно (`stack`)"
            )
        if answer:
            items = [
                {"path": h.get("file") or h.get("path"), "line": h.get("start_line"),
                 "name": f"{h.get('corpus')}: {((h.get('symbol') or {}).get('fqn')) or h.get('source_kind')}"}
                for h in data.get("hits") or []
            ]
            self._answer_mode(env, "Готовые реализации в соседних корпусах того же стека", items)
        return env.to_dict()

    def op_catalog(self) -> dict:
        """21. Реестр разработок: что индексировано, когда, с какими суммами.

        Ответ на вопрос «разделены ли разработки»: у каждой записи свой корень,
        своя ревизия, своя контрольная сумма. Физически разработки разделены
        каталогами `.ragraf`, реестр лишь даёт сводную картину.
        """
        from .catalog import Catalog, Entry

        env = self.make_env("catalog")
        catalog = Catalog()
        cached = self._catalog_cache_read(catalog.path)
        if cached is not None:
            env.result = cached
            env.freshness = self._freshness
            env.found = bool(cached.get("entries"))
            return env.to_dict()
        summary = catalog.summary()
        # По каждой разработке дополнительно сверяется состояние её корпуса.
        #
        # Замер 23.10.2026: прежняя редакция звала `watch_hashes` (полная сверка
        # контрольной суммы) по КАЖДОЙ из 36 записей, включая корпус AutoClaw на
        # 54 235 файлов, — ответ стоил 242 255 мс. Здесь та же мысль, но по
        # порядку цены: сначала отпечаток входа (путь+размер+mtime), и только
        # если он разошёлся — полная сверка. Совпал — состояние названо
        # `hash_matches=True` с пометкой `verified_by="fingerprint"`, потому что
        # содержимое доказано отпечатком, а не хешем.
        def _verify(row: dict) -> dict:
            """Состояние одного корпуса: отпечаток, при расхождении — хеш."""
            out: dict = {"hash_matches": None}
            entry = Entry.from_dict(row)
            if not entry.root or not entry.config:
                return out
            # Сверять нечего: ревизии нет, значит индекс не строился. Прежняя
            # редакция в этом случае считала полную сумму корпуса — для записи
            # AutoClaw (54 235 файлов) это стоило минуты на каждый вызов.
            if not entry.rev_id:
                out["verified_by"] = "not_indexed"
                return out
            try:
                from pathlib import Path as _Path

                from . import corpus as _corpus
                from . import watch as _watch
                from .config import load as _load

                cfg_path = _Path(entry.config)
                if not cfg_path.exists():
                    return {"check_error": "конфиг не найден"}
                peer = _load(cfg_path)
                saved = _watch.read_fingerprint(peer)
                fingerprint_now, files_now, _bytes = _corpus.stat_fingerprint(peer)
                out["files_now"] = files_now
                if saved and saved.get("fingerprint") and \
                        fingerprint_now == saved.get("fingerprint") and entry.corpus_hash:
                    out["hash_matches"] = True
                    out["verified_by"] = "fingerprint"
                else:
                    # Отпечаток разошёлся — содержимое уже не то, что
                    # проиндексировано. Полная сверка хешей здесь отвечала бы на
                    # тот же вопрос за чтение всех файлов корпуса; она остаётся
                    # в `drift` и `check`, где нужен ещё и перечень изменённых
                    # файлов. Здесь достаточно констатации.
                    out["hash_matches"] = bool(entry.corpus_hash) and False
                    out["verified_by"] = "fingerprint_changed" 
                if entry.config_hash:
                    out["config_matches"] = bool(peer.config_hash == entry.config_hash)
            except Exception as exc:  # noqa: BLE001
                out["check_error"] = type(exc).__name__
            return out

        # Обход корпусов независим, поэтому идёт параллельно: обход дерева —
        # ввод-вывод, и 36 последовательных обходов складывались в минуты.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            verdicts = list(pool.map(_verify, summary["entries"]))
        for row, verdict in zip(summary["entries"], verdicts):
            row.update(verdict)
        env.result = summary
        self._catalog_cache_write(catalog.path, summary)
        # Реестр тоже сообщает состояние своего индекса в том же поле — контракт
        # ответа единый для всех операций.
        env.freshness = self._freshness
        env.found = bool(summary["entries"])
        if not env.found:
            env.extra["suggest"] = ["index"]
            env.extra["note"] = "реестр пуст: ни одна разработка не зарегистрирована"
        return env.to_dict()

    # ---------------- кэш ответа catalog (шаг 0.4) ----------------

    def _catalog_cache_path(self) -> "object":
        from pathlib import Path as _Path

        return _Path(self.cfg.index_dir) / "catalog_snapshot.json"

    def _catalog_cache_read(self, catalog_path) -> dict | None:
        """Кэш ответа `catalog`, годный только для того же файла реестра.

        Реестр правится тремя путями (CLI, сторож, ensure) — все они идут через
        `Catalog.record_run`/`save`, а `save` обновляет `updated_at` и время
        правки файла. Поэтому признак годности здесь двойной: TTL и неизменность
        файла реестра. Правка реестра любым путём кэш обесценивает.
        """
        try:
            from pathlib import Path as _Path

            from . import util as _util

            data = _util.read_json(self._catalog_cache_path(), default=None)
            if not isinstance(data, dict):
                return None
            if (time.time() - float(data.get("ts") or 0)) > CATALOG_CACHE_TTL_SEC:
                return None
            try:
                mtime = int(_Path(catalog_path).stat().st_mtime_ns)
            except OSError:
                return None
            if int(data.get("catalog_mtime_ns") or 0) != mtime:
                return None
            entries = data.get("entries")
            if not isinstance(entries, dict) or not isinstance(entries.get("entries"), list):
                return None
            out = dict(entries)
            for row in out["entries"]:
                row["cache"] = "catalog"
            return out
        except Exception:  # noqa: BLE001 — кэш не имеет права ломать ответ
            return None

    def _catalog_cache_write(self, catalog_path, summary: dict) -> None:
        """Сохранить ответ для повторных вызовов. Ошибка записи не критична."""
        try:
            from pathlib import Path as _Path

            from . import util as _util

            mtime = int(_Path(catalog_path).stat().st_mtime_ns)
            _util.write_json(self._catalog_cache_path(), {
                "ts": time.time(),
                "catalog_mtime_ns": mtime,
                "entries": summary,
            })
        except Exception:  # noqa: BLE001
            pass

    def _run_guard(self) -> None:
        """Сверить контрольные суммы и при необходимости переиндексировать.

        Ошибка сторожа НЕ ломает работу: если проверка или реиндексация не
        удалась, ответ помечается «индекс устарел» с причиной, и агент сам
        решает, доверять ли фактам. Молчаливая выдача старых фактов запрещена
        (ФТ-28, I3): именно она порождает выводы о коде, которого уже нет.
        """
        try:
            from . import watch as watch_mod

            fresh = watch_mod.check(self.cfg, auto_reindex=True)
            self.category = fresh.category
            self._freshness = fresh.to_dict()
            self._fresh_checked_at = time.time()
            if fresh.reindexed:
                # Индекс перестроен — соединение переоткрывается, иначе фасад
                # продолжит читать прежние файлы базы и отдаст старые факты.
                try:
                    self.graph.close()
                except Exception:
                    pass
                self.graph = store.connect(self.cfg.graph_db, readonly=True)
                self._rev_cache = None
                self._drift_cache = None
        except Exception as exc:
            self._freshness = {
                "checked": False,
                "stale": True,
                "warning": f"проверка свежести не выполнена: {type(exc).__name__}",
            }

    # ---------------- конверт ----------------

    def rev_info(self) -> dict:
        """Информация об активной ревизии — с проверкой, не сменилась ли она.

        ПОЧЕМУ С ПРОВЕРКОЙ. `rev.json` читался один раз и кэшировался навсегда;
        сбрасывался кэш только тогда, когда ПОЛНАЯ проверка свежести сообщала
        `reindexed=True`. Быстрый путь по отпечатку входа возвращался раньше и
        кэш не трогал — поэтому долгоживущий процесс (MCP-сервер) после
        переиндексации ДРУГИМ процессом продолжал отвечать прошлым состоянием.
        Замер 14.10.2026: фасад видел `rev-0120`, когда на диске уже был
        `rev-0121`; если данные старой ревизии успевали удалиться ротацией,
        ответы становились пустыми (`rev: null`, `counts: 0`), а
        `index_status` при этом объявлял `healthy: true` — то есть «зелёная
        проверка, которая ничего не осмотрела».

        Цена проверки — один `stat` файла на вызов; перечитывание происходит
        только при смене времени правки или размера.
        """
        path = self.cfg.rev_file
        try:
            stat = os.stat(path)
            stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if self._rev_cache is None:
            self._rev_cache = util.read_json(path, default=None) or {}
            self._rev_stat = stamp
            return self._rev_cache
        if stamp != self._rev_stat:
            # Ревизия сменилась в другом процессе: переоткрываем соединения,
            # иначе фасад продолжит читать прежние файлы хранилищ.
            self._reopen_stores()
            self._rev_cache = util.read_json(path, default=None) or {}
            self._rev_stat = stamp
        return self._rev_cache

    def _reopen_stores(self) -> None:
        """Переоткрыть хранилища после смены ревизии другим процессом."""
        try:
            self.graph.close()
        except Exception:
            pass
        self.graph = store.connect(self.cfg.graph_db, readonly=True)
        try:
            self.lexvec.close()
            self.lexvec = LexVecStore(self.cfg)
        except Exception:
            pass
        # Кэши, привязанные к ревизии, больше не верны.
        self._drift_cache = None
        self._graph_cache = None
        self._fresh_checked_at = 0.0

    def current_rev(self) -> str | None:
        rev = self.rev_info().get("rev_id")
        return str(rev) if rev else None

    def capability(self) -> str:
        info = self.rev_info()
        cap = info.get("capability")
        return str(cap) if cap else "degraded"

    def capability_joern_used(self) -> bool:
        """Использовался ли граф свойств кода при индексации этой ревизии.

        Нужно, чтобы объяснить отказ операции, не называя режим наугад: у
        неполного режима (`partial`) и у полного отказа (`degraded`) причины
        разные, и текст сообщения обязан их различать.
        """
        return bool(self.rev_info().get("joern_used"))

    def drift_summary(self) -> dict:
        if self._drift_cache is not None:
            return self._drift_cache
        rev = self.current_rev()
        if not rev:
            self._drift_cache = {"files": 0, "symbols": 0, "entries": 0}
            return self._drift_cache
        rows = self.control.conn.execute(
            """SELECT path, symbols_added, symbols_removed, symbols_changed
               FROM drift_entry WHERE index_rev = ?""",
            (rev,),
        ).fetchall()
        paths = {str(r["path"]) for r in rows}
        symbols = 0
        for r in rows:
            for col in ("symbols_added", "symbols_removed", "symbols_changed"):
                val = r[col]
                if val:
                    try:
                        symbols += len(util.loads(val))
                    except Exception:
                        pass
        self._drift_cache = {"files": len(paths), "symbols": symbols, "entries": len(rows)}
        return self._drift_cache

    def stale_paths(self) -> dict[str, dict]:
        rev = self.current_rev()
        if not rev:
            return {}
        rows = self.control.conn.execute(
            """SELECT path, kind, ts, id FROM drift_entry
               WHERE index_rev = ? ORDER BY id DESC""",
            (rev,),
        ).fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            p = str(r["path"])
            if p in out:
                continue
            out[p] = {"path": p, "reason": f"{r['kind']} после {rev}", "since": r["ts"],
                      "entry_id": int(r["id"]), "suggest": "live_read"}
        return out

    def make_env(self, op: str, provenance: str = "index") -> Envelope:
        return Envelope(
            op=op,
            index_rev=self.current_rev(),
            capability=self.capability(),
            drift=self.drift_summary(),
            provenance=provenance,
            freshness=self._freshness,
        )

    def attach_stale(self, env: Envelope, paths: list[str]) -> Envelope:
        """Пометка stale для затронутых путей (ФТ-28). Никогда не молчаливая (I3)."""
        stale = self.stale_paths()
        for p in paths:
            hit = stale.get(util.to_posix(p))
            if hit and hit not in env.stale_warnings:
                env.stale_warnings.append(hit)
        return env

    # ---------------- операции ----------------

    def op_index_status(self) -> dict:
        """13. Состояние ревизии, покрытие, деградация, размер журнала.

        Быстрый путь (шаг 0.3 плана). Полный расчёт стоит измеренные 4 630 мс:
        `graph_compute_state` поднимает torch (2 401 мс на импорт) и считает
        PageRank по графу, `_quality` читает сэмпл векторов. Между тем состав
        ответа меняется ТОЛЬКО при индексации, поэтому он записывается снимком
        рядом с ревизией, а снимок признаётся годным лишь при совпадении
        ревизии и времени правки обоих хранилищ. Не совпало — считается заново,
        и снимок перезаписывается: устаревший статус не отдаётся никогда.
        """
        env = self.make_env("index_status")
        rev = self.current_rev()
        if not rev:
            env.found = False
            env.error = "индекс не создан: выполните ragraf index"
            return env.to_dict()
        cached = self._status_snapshot_read(rev)
        if cached is not None:
            cached["freshness"] = self._freshness
            return cached
        cov = self._coverage(rev)
        row = self.graph.execute(
            "SELECT rev_id, parent_rev, created_at, state, capability, joern_used,"
            " file_count, node_count, edge_count, corpus_hash, config_hash, extractor_versions"
            " FROM rev WHERE rev_id = ?",
            (rev,),
        ).fetchone()
        stages = [
            {"stage": r["stage"], "seconds": r["seconds"], "items": r["items"], "note": r["note"]}
            for r in self.graph.execute(
                "SELECT stage, seconds, items, note FROM stage_timing WHERE rev_id = ?"
                " ORDER BY rowid",
                (rev,),
            ).fetchall()
        ]
        lex_counts = self.lexvec.counts(rev)
        env.result = {
            "rev": dict(row) if row else None,
            "coverage": cov,
            "stages": stages,
            "lexvec": lex_counts,
            "vector_available": self.lexvec.vec_ok,
            "embedding_model": self.cfg.embedding.model,
            "drift": self.drift_summary(),
            "project": self.cfg.project,
            "root": util.to_posix(self.cfg.root),
            # Графовый вычислитель: где считаются алгоритмы и почему. Настройка
            # `graph_gpu` без этого поля была бы значением без наблюдаемого
            # эффекта — а так видно, сработала карта, сработал порог или помешал
            # отсутствующий torch.
            "graph_compute": self.graph_compute_state(),
            # U7: показатели качества индекса. Зачем: три дефекта на полигоне
            # (нулевые оценки векторов, чанк в 11 331 токен, потеря docblock)
            # выглядели как «пустой результат», а не как ошибка, и обнаруживались
            # только случайно. Здесь они видны сразу.
            "quality": self._quality(rev),
        }
        env.extra["counts"] = {
            "files": int(row["file_count"]) if row else 0,
            "nodes": int(row["node_count"]) if row else 0,
            "edges": int(row["edge_count"]) if row else 0,
        }
        # «Зелёная проверка, которая ничего не осмотрела» — это провал, а не
        # здоровье. Ситуация наблюдалась 14.10.2026: ревизия сменена другим
        # процессом, данные старой удалены ротацией, и `index_status` отвечал
        # `rev: null`, `ft_chunk: 0`, `counts: 0` — и одновременно
        # `quality.healthy: true`. Теперь пустая ревизия называется сломанной.
        counts = env.extra["counts"]
        empty_rev = (
            (row is None)
            or (counts["files"] == 0 and counts["nodes"] == 0)
            or int((lex_counts or {}).get("ft_chunk") or 0) == 0
        )
        if empty_rev:
            quality = env.result.get("quality") or {}
            problems = list(quality.get("problems") or [])
            problems.append(
                "ревизия без данных: файлов "
                f"{counts['files']}, узлов {counts['nodes']}, чанков "
                f"{int((lex_counts or {}).get('ft_chunk') or 0)} — проверьте "
                "активную ревизию и переиндексацию (`check`); возможно, процесс "
                "держит ревизию, данные которой удалены ротацией"
            )
            quality["problems"] = problems
            quality["healthy"] = False
            env.result["quality"] = quality
            env.limits.append(problems[-1])
        payload = env.to_dict()
        self._status_snapshot_write(rev, payload)
        return payload

    # ---------------- снимок index_status (шаг 0.3) ----------------

    def _status_storages_stamp(self) -> dict:
        """Время правки хранилищ: по нему снимок признаётся годным."""
        from pathlib import Path as _Path

        stamp: dict = {}
        for path in (self.cfg.graph_db, self.cfg.lexvec_db):
            try:
                stamp[_Path(path).name] = int(_Path(path).stat().st_mtime_ns)
            except OSError:
                stamp[_Path(path).name] = 0
        return stamp

    def _status_snapshot_read(self, rev: str) -> dict | None:
        """Снимок статуса, если он относится к ТЕКУЩЕЙ ревизии и хранилищам.

        Возвращает None при любом сомнении: снимок — ускорение, а не источник
        правды. Молчаливая выдача снимка от прежней ревизии была бы тем самым
        «зелёным отчётом, который ничего не осмотрел».
        """
        try:
            from pathlib import Path as _Path

            from . import util as _util

            path = _Path(self.cfg.index_dir) / "index_status_snapshot.json"
            data = _util.read_json(path, default=None)
            if not isinstance(data, dict):
                return None
            if str(data.get("rev") or "") != str(rev or ""):
                return None
            if data.get("storages") != self._status_storages_stamp():
                return None
            payload = data.get("payload")
            if not isinstance(payload, dict):
                return None
            payload = dict(payload)
            payload["source"] = "snapshot"
            payload["snapshot_at"] = data.get("written_at")
            return payload
        except Exception:  # noqa: BLE001 — снимок не имеет права ломать статус
            return None

    def _status_snapshot_write(self, rev: str, payload: dict) -> None:
        """Записать снимок статуса. Ошибка записи не ломает ответ."""
        try:
            from pathlib import Path as _Path

            from . import util as _util

            clean = dict(payload)
            clean.pop("freshness", None)
            path = _Path(self.cfg.index_dir) / "index_status_snapshot.json"
            _util.write_json(path, {
                "rev": rev,
                "storages": self._status_storages_stamp(),
                "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "payload": clean,
            })
        except Exception:  # noqa: BLE001
            pass

    def _quality(self, rev: str, samples: int = 64) -> dict:
        """Показатели качества индекса — то, что отличает «пусто» от «сломано».

        Что считается:
          * vectors: нормы векторов (p50/max) и признак нормированности. Если
            нормы далеки от единицы — модель не нормирует векторы, и формула
            перевода расстояния в оценку обязана это учитывать (дефект №1);
          * chunks: размеры чанков, число превысивших лимит и число файлов
            без символов — признаки размытой выдачи (дефект №3);
          * signatures: сколько функций разобрано, сколько сигнатур обрезано и
            сколько имеет нетипизированные параметры (U3, дефект №8);
          * docblocks: сколько docblock'ов попало в индекс — без них контракт
            параметров недостижим (U2).

        Все значения замеряются по графу и лексическому хранилищу.
        """
        out: dict = {}

        # --- векторы ---
        try:
            import math
            import struct

            lv = self.lexvec
            vec_ok = bool(lv.vec_ok)
            norms: list[float] = []
            if vec_ok:
                rows = lv.conn.execute(
                    "SELECT embedding FROM vec_chunk LIMIT ?", (samples,)
                ).fetchall()
                for r in rows:
                    blob = r["embedding"]
                    if isinstance(blob, memoryview):
                        blob = blob.tobytes()
                    n = len(blob) // 4
                    if n <= 0:
                        continue
                    vals = struct.unpack(f"<{n}f", blob[: n * 4])
                    norms.append(math.sqrt(sum(v * v for v in vals)))
            norms.sort()
            p50 = norms[len(norms) // 2] if norms else None
            out["vectors"] = {
                "available": vec_ok,
                "sampled": len(norms),
                "norm_p50": round(p50, 4) if p50 is not None else None,
                "norm_max": round(norms[-1], 4) if norms else None,
                # Нормы около 1 → векторы нормированы, косинус считается упрощённо.
                "normalized": bool(norms) and p50 is not None and abs(p50 - 1.0) < 0.05,
                "note": ("нормы далеки от 1: перевод расстояния в оценку обязан "
                         "учитывать нормы"
                         if p50 is not None and abs(p50 - 1.0) >= 0.05 else ""),
            }
        except Exception as exc:
            out["vectors"] = {"available": False, "error": type(exc).__name__}

        # --- чанки ---
        # ВАЖНО: chunk_map и ft_chunk лежат в ЛЕКСИЧЕСКОЙ базе, а не в графе.
        # Первая версия запрашивала chunk_map через self.graph — запрос падал
        # с OperationalError, и блок чанков возвращал только «error».
        # В интерфейсе это выглядело как «нет данных» вместо показателя,
        # то есть дефект был невидим (найдено при снятии снимка панели).
        #
        # ВТОРОЕ УТОЧНЕНИЕ про единицу измерения. Сначала размер брался как
        # LENGTH(ft_chunk.code_tokens), и это давало чанки «на 44 тысячи токенов»
        # при лимите 8 000. Причина: code_tokens — ТОКЕНИЗИРОВАННАЯ форма текста
        # для поиска FTS5, она в несколько раз длиннее исходника. Честная мера —
        # колонка chunk_map.tokens: это оценка размера самого чанка, и именно
        # она сравнивается с лимитом при чанкинге.
        try:
            row = self.lexvec.conn.execute(
                """SELECT COUNT(*) AS total,
                          COALESCE(MAX(tokens), 0) AS max_tok,
                          SUM(CASE WHEN tokens > 8000 THEN 1 ELSE 0 END) AS over,
                          COALESCE(AVG(tokens), 0) AS avg_tok
                     FROM chunk_map WHERE rev_id = ?""",
                (rev,),
            ).fetchone()
            file_only = self.lexvec.conn.execute(
                """SELECT COUNT(DISTINCT path) AS c FROM chunk_map
                    WHERE rev_id = ? AND source_kind = 'file'""",
                (rev,),
            ).fetchone()
            out["chunks"] = {
                "total": int(row["total"] or 0) if row else 0,
                "max_tokens": int(row["max_tok"] or 0) if row else 0,
                "avg_tokens": int(row["avg_tok"] or 0) if row else 0,
                "over_budget": int(row["over"] or 0) if row else 0,
                "files_without_symbols": int(file_only["c"] or 0) if file_only else 0,
                "budget_tokens": 8000,
                "measure": "chunk_map.tokens: оценка размера чанка при чанкинге",
            }
        except Exception as exc:
            out["chunks"] = {"error": type(exc).__name__}

        # --- сигнатуры и docblock'и ---
        try:
            sym_total = self.graph.execute(
                "SELECT COUNT(*) AS c FROM node WHERE rev_id = ?"
                " AND kind IN ('function','method')",
                (rev,),
            ).fetchone()["c"]
            trunc = self.graph.execute(
                """SELECT COUNT(*) AS c FROM node WHERE rev_id = ?
                    AND kind IN ('function','method')
                    AND props LIKE '%' || char(34) || 'signature_truncated' || char(34) || ': true%'""",
                (rev,),
            ).fetchone()["c"]
            untyped = self.graph.execute(
                """SELECT COUNT(*) AS c FROM node WHERE rev_id = ?
                    AND kind IN ('function','method')
                    AND props LIKE '%' || char(34) || 'type' || char(34) || ': null%'""",
                (rev,),
            ).fetchone()["c"]
            doc_total = self.graph.execute(
                "SELECT COUNT(*) AS c FROM text_chunk WHERE rev_id = ? AND text_kind = 'docblock'",
                (rev,),
            ).fetchone()["c"]
            out["signatures"] = {
                "functions": int(sym_total or 0),
                "truncated": int(trunc or 0),
                "with_untyped_params": int(untyped or 0),
                "docblocks": int(doc_total or 0),
                "note": ("есть обрезанные сигнатуры: текстовая форма теряет параметры, "
                         "используйте signature_params" if trunc else ""),
            }
        except Exception as exc:
            out["signatures"] = {"error": type(exc).__name__}

        # Сводка проблем: клиент должен видеть состояние одним взглядом.
        problems: list[str] = []
        v = out.get("vectors") or {}
        c = out.get("chunks") or {}
        s = out.get("signatures") or {}
        # Ненормированные векторы — НЕ проблема: перевод расстояния в оценку в
        # lexvec уже учитывает нормы (q_norm, c_norm). Первая версия этой
        # проверки помечала такие векторы как неисправность и показывала
        # «здоровье: нет» на обоих рабочих корпусах, где оценки поиска
        # при этом верные (замер: разброс 0.0576, максимум 0.6627).
        # Такое предупреждение хуже отсутствия: администратор начинает искать
        # несуществующую поломку.
        if not v.get("available"):
            problems.append("векторный поиск недоступен: расширение vec0 не загружено")
        if (c.get("over_budget") or 0) > 0:
            problems.append(f"чанков сверх лимита: {c['over_budget']}")
        if (s.get("truncated") or 0) > 0:
            problems.append(f"обрезанных сигнатур: {s['truncated']}")
        if c.get("error"):
            problems.append(f"размеры чанков не прочитаны: {c['error']}")
        if v.get("error"):
            problems.append(f"нормы векторов не прочитаны: {v['error']}")
        out["problems"] = problems
        out["healthy"] = not problems
        # Ненормированность сообщается отдельным полем как СПРАВКА: это не
        # дефект, но полезно знать, что формула обязана учитывать нормы.
        if v.get("available") and v.get("normalized") is False:
            out["notes"] = [
                "векторы не нормированы (норма p50 "
                f"{v.get('norm_p50')}): оценка близости считается через нормы "
                "запроса и чанка, что и делает lexvec"
            ]
        return out

    def op_symbol(self, name: str, path: str | None = None, limit: int = 20,
                  answer: bool = False) -> dict:
        """2. Символ: вид, файл, строки, сигнатура."""
        env = self.make_env("symbol")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = self._symbol_rows(rev, name, path, limit)
        if not rows:
            env.found = False
            env.extra["suggest"] = ["search", "live_read"]
            env.result = []
            if answer:
                self._answer_mode(env, f"Символ «{name}» не найден", [])
            return env.to_dict()
        env.result = rows
        # Контракт параметров из docblock'а (U2). Без этого вызывать функцию,
        # принимающую массив, приходится наугад: сигнатура "array $data" не
        # сообщает состав ключей, а docblock с psalm-описанием
        # `array{action:string, ...}` лежит в индексе рядом.
        # Дефект №8 (TypeError на z_agr_acl_audit) был именно из-за этого.
        self._attach_docblocks(rev, rows)
        # Пустые пути отбрасываются: после правки B5 у узлов kind='external'
        # пути нет вовсе (`indexer._build_facts`), а пустая строка в
        # `stale_paths` дала бы пометку «устарело» без файла.
        self.attach_stale(env, [r["path"] for r in rows if r.get("path")])
        if answer:
            items = [
                {"path": r.get("path"), "line": r.get("start_line"),
                 "name": f"{r.get('kind')} {r.get('name')}"}
                for r in rows
            ]
            self._answer_mode(env, f"Символ «{name}»", items)
        return env.to_dict()

    def _attach_docblocks(self, rev: str, rows: list[dict]) -> None:
        """Добавить к символам их docblock и разобранный контракт параметров.

        Docblock в индексе привязан к узлу ФАЙЛА (owner kind = file), поэтому
        раньше он не был достижим по имени функции. Здесь привязка делается по
        позиции: берётся последний docblock в том же файле, который заканчивается
        не позже начала символа (тот же приём, что уже применяется к комментариям).

        Разбор `array{...}` покрывает psalm-формат @param — основной способ
        описания массивов в этом проекте. Если описания нет, добавляется поле
        `params_hint` с прямым указанием, что состав массива в индексе отсутствует:
        молчаливая неполнота недопустима.
        """
        import re as _re

        for row in rows:
            path = str(row.get("path") or "")
            start = int(row.get("start_line") or 0)
            if not path or start <= 0:
                continue
            try:
                blocks = self.graph.execute(
                    """SELECT raw, start_line, end_line FROM text_chunk
                        WHERE rev_id = ? AND path = ? AND text_kind = 'docblock'
                          AND end_line < ?
                        ORDER BY end_line DESC LIMIT 1""",
                    (rev, path, start),
                ).fetchall()
            except Exception:
                continue
            if not blocks:
                continue
            raw = str(blocks[0]["raw"])
            row["doc"] = raw.strip()[:1200]
            shape = _parse_array_shapes(raw)
            if shape:
                row["array_shape"] = shape
            else:
                row["params_hint"] = (
                    "состав параметров-массивов в индексе не описан: проверьте docblock "
                    "или тело функции перед вызовом"
                )

    def op_references(self, fqn: str, limit: int = 100) -> dict:
        """3. Все места использования символа.

        Список видов связи включает не только вызовы: ключ перевода
        (`uses_key`), пакетная переменная Perl (`reads_var`), обращение к
        таблице (`queries`) и маршрут (`routes`) — это тоже ИСПОЛЬЗОВАНИЯ, и
        без них вопрос «где ещё это встречается» не получал ответа для
        узлов, у которых нет вызовов (P1-3).
        """
        env = self.make_env("references")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        node_ids = [r["node_id"] for r in self._symbol_rows(rev, fqn, None, 5)]
        if not node_ids:
            env.found = False
            env.result = []
            env.extra["suggest"] = ["symbol", "search"]
            return env.to_dict()
        marks = ",".join("?" for _ in node_ids)
        rows = self.graph.execute(
            f"""SELECT src, kind, provenance, evidence_path, evidence_line, confidence, dst
                FROM edge WHERE rev_id = ? AND dst IN ({marks})
                  AND kind IN ('calls','calls_heuristic','instantiates','extends','implements',
                               'references','overrides','uses_key','reads_var','queries','routes')
                ORDER BY evidence_path, evidence_line LIMIT ?""",
            (rev, *node_ids, limit),
        ).fetchall()
        out = []
        for r in rows:
            owner = self.graph.execute(
                "SELECT node_id, kind, name, fqn, path, start_line, end_line FROM node"
                " WHERE rev_id = ? AND node_id = ?",
                (rev, r["src"]),
            ).fetchone()
            out.append(
                {
                    "from": dict(owner) if owner else {"node_id": r["src"]},
                    "kind": r["kind"],
                    "provenance": r["provenance"],
                    "evidence": f"{r['evidence_path']}:{r['evidence_line']}",
                    "confidence": r["confidence"],
                }
            )
        if not out:
            env.found = False
            env.extra["suggest"] = ["search", "live_read"]
        env.result = out
        self.attach_stale(env, [str(r["evidence_path"]) for r in rows if r["evidence_path"]])
        return env.to_dict()

    def op_callers(self, fqn: str, depth: int = 1, hard_only: bool = False,
                   limit: int = 200, answer: bool = False) -> dict:
        """4. Кто вызывает."""
        return self._graph_walk(fqn, depth, hard_only, "callers", limit,
                                answer=answer)

    def op_callees(self, fqn: str, depth: int = 1, hard_only: bool = False, limit: int = 200) -> dict:
        """5. Кого вызывает."""
        return self._graph_walk(fqn, depth, hard_only, "callees", limit)

    def op_impact(self, target: str, mode: str = "full", depth: int = 3, limit: int = 500,
                  answer: bool = False) -> dict:
        """6. Что затронет изменение. Разделение доказанных и эвристических связей (ФТ-18)."""
        env = self.make_env("impact")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)

        # Цель может быть таблицей БД, файлом или символом.
        table_target = None
        if target.startswith("table:") or target.lower() in self._table_names(rev):
            table_target = target.split(":", 1)[-1].lower()

        if table_target:
            return self._impact_table(env, rev, table_target, mode)

        rows = self._symbol_rows(rev, target, None, 5)
        if not rows:
            env.found = False
            env.error = f"символ не найден: {target}"
            env.extra["suggest"] = ["symbol", "search", "live_read"]
            if answer:
                self._answer_mode(env, f"Символ «{target}» не найден", [])
            return env.to_dict()

        roots = [r["node_id"] for r in rows]
        affected = self._bfs(rev, roots, depth, reverse=True, limit=limit)

        hard: list[dict] = []
        soft: list[dict] = []
        files: dict[str, dict] = {}
        for item in affected:
            if item["provenance"] == "stated":
                hard.append(item)
            else:
                soft.append(item)
            p = item.get("path")
            if p:
                slot = files.setdefault(p, {"path": p, "stated": 0, "derived": 0})
                slot[item["provenance"]] += 1

        env.result = {
            "target": rows[0]["fqn"],
            "mode": mode,
            "affected_files": sorted(files.values(), key=lambda x: (-x["stated"], x["path"])),
            "stated": hard,
            "derived": soft,
            "affected_count": {"stated": len(hard), "derived": len(soft)},
        }
        if mode == "hard":
            env.result["derived"] = []
        self.attach_stale(env, [f["path"] for f in env.result["affected_files"]])
        if answer:
            items = [
                {"path": f.get("path"), "line": 1,
                 "name": f"затронуто: доказанных {f.get('stated', 0)}, "
                         f"выводов {f.get('derived', 0)}"}
                for f in env.result["affected_files"]
            ]
            self._answer_mode(
                env, f"Правка `{rows[0]['fqn']}` затронет файлов: "
                     f"{len(items)} (доказанных связей {len(hard)}, выводов {len(soft)})",
                items,
                notes=["связи `derived` — вывод по имени/порядку, а не факт"],
            )
        return env.to_dict()

    def op_slice(self, fqn: str, direction: str = "back", limit: int = 200) -> dict:
        """7. Структурный срез: узлы+рёбра+строки, БЕЗ тел функций (ФТ-19)."""
        env = self.make_env("slice")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = self._symbol_rows(rev, fqn, None, 3)
        if not rows:
            env.found = False
            env.error = f"символ не найден: {fqn}"
            return env.to_dict()
        if self.capability() != "full":
            # Сообщение называет ФАКТИЧЕСКИЙ режим, а не «degraded» по умолчанию.
            # Прежде текст был зашит, и ревизия в режиме `partial` (свойства кода
            # не построены, но подтверждённые вызовы в графе есть) получала
            # сообщение «режим degraded» — отчёт расходился со своими данными.
            # Порядок причин: сначала признак использования CPG, потом режим.
            cap = self.capability()
            env.found = False
            env.error = (
                f"срез недоступен: ревизия в режиме {cap}: полные свойства кода "
                "(PDG/DDG) не построены"
            )
            env.extra["capability"] = cap
            env.extra["capability_reason"] = (
                "Joern не использован при индексации"
                if not self.capability_joern_used()
                else f"экспорт графа свойств кода не удался (режим {cap})"
            )
            return env.to_dict()

        roots = [r["node_id"] for r in rows]
        reverse = direction == "back"
        steps = self._bfs(rev, roots, 4, reverse=reverse, limit=limit)
        env.result = {
            "target": rows[0]["fqn"],
            "direction": direction,
            "nodes": steps,
            "node_count": len(steps),
            "note": "тела символов не включены: используйте операцию code",
        }
        self.attach_stale(env, [s["path"] for s in steps if s.get("path")])
        return env.to_dict()

    def op_code(self, fqn: str | None = None, path: str | None = None,
                style: str = "compact", limit: int = 5,
                entry_ids: str | None = None, start: int | None = None,
                end: int | None = None, source: str = "auto",
                include_containers: bool = False) -> dict:
        """8. Тела символов — только по явному запросу.

        Изменения по итогам замеров rev-0030 (каждое закрывает измеренный дефект):

        * `entry_ids` — адрес второго шага. Хит поиска отдаёт `chunk_id` и
          `node_id`, но раньше их НЕЛЬЗЯ было использовать: вызов с одним
          из них отвечал «нужен параметр fqn или path», и агент шёл искать
          файл заново. Теперь идентификатор хита разрешается в спаны.

        * `start`/`end` — окно внутри найденных спанов. Раньше эти параметры
          принимались, но игнорировались: `code --path X --start 151 --end 190`
          отвечал тем же, что без диапазона.

        * Двойной счёт. Замер: `code --path src/ragraf/facade.py` отдавал
          Σtext = 180 507 при файле в 100 354 символа (×1,80): класс-контейнер
          `Facade 243-2264` шёл целиком И все его 51 метод отдельно. Теперь
          спаны, вложенные в другой спан, не выдаются — вместо них идёт
          счётчик `nested_dropped`. Флаг `include_containers=True` возвращает
          прежнее поведение тому, кому нужен файл целиком одним спаном.

        * Молчаливый обрез. Лимит применялся к запросу (`LIMIT ?`), и о том,
          что спанов больше, ответ молчал. Теперь в нём `truncated` и
          `spans_total`.

        * Источник текста. По умолчанию текст берётся ИЗ ИНДЕКСА (таблица
          чанков), а не с диска: тогда чтение не зависит от того, менялся ли
          файл после индексации. Если текста в индексе нет, берётся диск, и это
          видно в поле `text_source` каждого спана: `index` либо `disk`.
        """
        env = self.make_env("code", provenance="live")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        spans: list[tuple[str, int, int, str | None]] = []
        unresolved: list[str] = []
        via_node = 0
        via_chunk = 0

        if entry_ids:
            import re as _re

            raws = [x for x in _re.split(r"[,\s]+", str(entry_ids)) if x]
            spans, unresolved, via_node, via_chunk = self._resolve_entry_ids(rev, raws)
        elif fqn:
            for r in self._symbol_rows(rev, fqn, None, limit):
                spans.append((str(r["path"]), int(r["start_line"]), int(r["end_line"]), r["fqn"]))
        elif path:
            rows = self.graph.execute(
                """SELECT path, start_line, end_line, fqn FROM node
                   WHERE rev_id = ? AND path = ? AND kind IN ('function','method','class')
                   ORDER BY start_line, end_line""",
                (rev, util.to_posix(path)),
            ).fetchall()
            all_spans = [
                (str(r["path"]), int(r["start_line"]), int(r["end_line"]), r["fqn"])
                for r in rows
            ]
            spans = _drop_nested(all_spans) if not include_containers else all_spans
            dropped_nested = len(all_spans) - len(spans)
            env.extra["spans_total"] = len(all_spans)
            env.extra["nested_dropped"] = dropped_nested
            if include_containers:
                env.limits.append(
                    "include_containers=true: вложенные спаны выданы, но контейнер "
                    "сокращён до своей шапки (строки до первого дочернего спана), "
                    "чтобы один и тот же код не приехал дважды"
                )
            elif dropped_nested:
                env.limits.append(
                    f"вложенные спаны не выданы: {dropped_nested} из {len(all_spans)} "
                    "(класс-контейнер и его методы давали текст дважды); "
                    "запросить их можно через include_containers=true"
                )
            if len(spans) > limit:
                env.extra["truncated"] = True
                env.extra["spans_returned"] = limit
                env.limits.append(
                    f"спанов в файле больше лимита: показано {limit} из {len(spans)}; "
                    "увеличьте limit или задайте окно start/end"
                )
                spans = spans[:limit]
            # Нумерация после отсечения: индексы нужны для адресации повторного
            # вызова, и они должны соответствовать тому, что реально выдано.
            env.extra["spans"] = [
                {"index": i, "path": p, "start_line": s, "end_line": e, "fqn": f}
                for i, (p, s, e, f) in enumerate(spans)
            ]
        else:
            env.found = False
            env.error = "нужен параметр fqn, path или entry_ids"
            return env.to_dict()

        if entry_ids and not spans:
            env.found = False
            env.error = "идентификаторы не разрешились в спаны"
            env.extra["unresolved"] = unresolved
            env.extra["suggest"] = ["search", "symbol"]
            return env.to_dict()
        if entry_ids:
            env.extra["resolved"] = {
                "by_node_id": via_node,
                "by_chunk_id": via_chunk,
                "unresolved": unresolved,
            }

        # Текст спанов. Берём из индекса; там, где его нет, — с диска.
        # Вложенность: спан, внутри которого есть ДРУГИЕ выданные спаны, отдаётся
        # только своей «шапкой» — строками до первого дочернего спана. Иначе
        # класс-контейнер повторил бы все свои методы, и один и тот же код приехал
        # бы дважды (замер: Σtext 180 507 при файле 100 354).
        from_index: dict[int, tuple[int, str]] = {}
        if source != "disk":
            for i, (rel, span_start, span_end, _name) in enumerate(spans):
                children = [
                    s for j, (_p2, s, _e2, _f2) in enumerate(spans)
                    if j != i and s > span_start and spans[j][0] == rel
                ]
                cut = min(children) - 1 if children else span_end
                if cut > span_end:
                    cut = span_end
                got = self.lexvec.text_for_span(rev, rel, span_start, cut)
                if got is not None:
                    from_index[i] = got

        out = []
        stale = self.stale_paths()
        for idx, (rel, span_start, span_end, name) in enumerate(spans):
            lo = span_start if start is None else max(span_start, int(start))
            hi = span_end if end is None else min(span_end, int(end))
            if hi < lo:
                out.append({
                    "path": rel,
                    "symbol": name,
                    "start_line": span_start,
                    "end_line": span_end,
                    "error": "запрошенное окно вне спана",
                })
                continue
            text = None
            text_source = "disk"
            covered = 0
            got = from_index.get(idx)
            if got is not None:
                chunk_start, chunk_text = got
                lines = chunk_text.splitlines()
                off_lo = lo - chunk_start
                off_hi = hi - chunk_start + 1
                if 0 <= off_lo < len(lines):
                    seg = lines[off_lo:max(off_hi, off_lo + 1)]
                    if seg:
                        text = "\n".join(seg)
                        text_source = "index"
            if text is None:
                abs_path = self.cfg.root / rel
                if not abs_path.exists():
                    out.append({"path": rel, "error": "файл отсутствует в рабочем дереве"})
                    continue
                lines = util.decode_text(abs_path.read_bytes(), rel).splitlines()
                seg = lines[max(0, lo - 1) : hi]
                text = "\n".join(seg)
                covered = len(seg)
            else:
                covered = len(text.splitlines())
            wanted = max(0, hi - lo + 1)
            if style == "signatures":
                text = text.splitlines()[0] if text else ""
                covered = min(covered, 1)
            changed = rel in stale
            item = {
                "index": idx,
                "path": rel,
                "symbol": name,
                "start_line": span_start,
                "end_line": span_end,
                "window": {"start": lo, "end": hi},
                "changed_since_index": changed,
                "text_source": text_source,
                "text": text,
            }
            # Пробелы в индексе — не ошибка, но и не то же самое, что полный текст:
            # модульный код между символами чанком не является (замер rev-0029: чанки
            # покрывают 65 311 из 86 177 строк). Помечаем явно, чтобы агент знал,
            # что видит не весь файл, а только его индексированные куски.
            if covered < wanted:
                item["gaps"] = {
                    "lines_in_index": covered,
                    "lines_requested": wanted,
                    "note": "в индексе нет чанков для части строк (модульный код между символами)",
                }
                env.extra.setdefault("gaps", 0)
                env.extra["gaps"] = int(env.extra["gaps"]) + 1
            out.append(item)
        env.result = out
        # Провенанс — по фактическому источнику текста, а не по способу вызова.
        # Раньше `code` безусловно объявлял `live`, даже когда читал файл,
        # а не индекс; теперь значение соответствует тому, откуда взято
        # содержимое: index (из индекса), live (всё с диска), mixed (и то и другое).
        real = [o for o in out if o.get("text_source")]
        if not real:
            env.provenance = "live"
        elif all(o["text_source"] == "index" for o in real) and not any(
            o.get("changed_since_index") for o in real
        ):
            env.provenance = "index"
        elif all(o["text_source"] == "disk" for o in real):
            env.provenance = "live"
        else:
            env.provenance = "mixed"
        if any(o.get("text_source") == "disk" for o in real):
            env.limits.append(
                "часть текста взята с диска: в индексе нет сохранённого текста этих чанков"
            )
        self.attach_stale(env, [o["path"] for o in out if o.get("path")])
        return env.to_dict()

    def op_repo_map(self, scope: str | None = None, budget_tokens: int = 2000,
                    detail: str = "normal") -> dict:
        """9. Карта репозитория: модули → файлы → ключевые символы, в бюджете токенов."""
        env = self.make_env("repo_map")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = self.graph.execute(
            """SELECT path, COUNT(*) AS symbols FROM node
               WHERE rev_id = ? AND kind IN ('class','function','method')
               GROUP BY path ORDER BY symbols DESC""",
            (rev,),
        ).fetchall()

        # Ранжирование. Архитектура требует центральности в графе; число
        # символов — запасной вариант, когда графовый расчёт недоступен.
        # Разница не косметическая: центральный файл мог иметь мало символов,
        # но от него зависит остальной код, и именно его нужно показать первым.
        central = self._centrality(rev)
        scores: dict[str, float] = central.get("scores") or {}
        if scores:
            rows = sorted(
                rows, key=lambda r: (-scores.get(str(r["path"]), 0.0), str(r["path"]))
            )
            ranking = "centrality"
        else:
            ranking = "symbols"
            if central.get("error"):
                env.limits.append(
                    "центральность в графе не посчитана, ранг по числу символов: "
                    + str(central["error"])
                )
        files: list[dict] = []
        budget_chars = max(400, int(budget_tokens * 3.2))
        used = 0
        # Замер rev-0030: `chars_used` считался по СИНТЕТИЧЕСКОЙ строке
        # «путь (N симв.): имена[строки]», которой в ответе нет — в JSON уходит
        # структурный список `top`. При бюджете 2000 токенов заявлено 6 304
        # символа (якобы 98% бюджета), а фактический результат — 30 772 символа,
        # в 4,9 раза больше заявленного, и самый тяжёлый ответ корпуса
        # (24 655 симв./вызов). Агент, доверившись заявленному числу, считал
        # ответ уложившимся в бюджет, хотя он был вдвое больше запрошенного.
        #
        # Теперь считаем фактическую полезную нагрузку: сумму сериализованной
        # записи файла. Заявленное число обязано быть не меньше фактического,
        # иначе это не бюджет, а метка.
        for r in rows:
            p = str(r["path"])
            if scope and not p.startswith(util.to_posix(scope)):
                continue
            top = self.graph.execute(
                """SELECT name, kind, start_line, fqn FROM node
                   WHERE rev_id = ? AND path = ? AND kind IN ('class','function','method')
                   ORDER BY kind, start_line LIMIT ?""",
                (rev, p, 4 if detail == "normal" else 8),
            ).fetchall()
            entry = {
                "path": p,
                "line": 1,   # файл начинается со строки 1: ссылка всегда локализуема
                "symbols": int(r["symbols"]),
                "top": [dict(t) for t in top],
            }
            weight = _payload_weight_list(files + [entry])
            if files and weight > budget_chars:
                break
            used = weight
            files.append(entry)
        env.result = {
            "scope": scope or "весь корпус",
            "files": files,
            "files_included": len(files),
            "files_total": len(rows),
            "chars_used": used,
            "budget_chars": budget_chars,
            "budget_tokens": budget_tokens,
            "budget_note": (
                "chars_used — фактический объём полезной нагрузки (тот же формат, "
                "в котором уходит список файлов); объём ответа больше на его оболочку"
            ),
            # Чем ранжировали и почему — часть контракта: «порядок файлов» без
            # указания способа нельзя ни повторить, ни опровергнуть.
            "ranking": ranking,
            "ranking_note": (
                "по центральности в графе (PageRank на разрежённой матрице)"
                if ranking == "centrality"
                else "по числу символов: графовая центральность недоступна"
            ),
        }
        if scores:
            env.result["top_centrality"] = [
                {"path": p, "score": round(v, 8)} for p, v in central["scores_ordered"][:10]
            ]
            env.result["graph_compute"] = central.get("status")
        env.extra["budget_ok"] = used <= budget_chars
        return env.to_dict()

    def op_search(self, query: str, limit: int = 20, kinds: list[str] | None = None,
                  with_text: bool = False, text_width: int = 160,
                  answer: bool = False) -> dict:
        """1. Гибридный поиск: лексика + вектор (RRF), каждый хит с провенансом.

        `with_text=True` добавляет к каждому хиту поле `snippet` — начало сырого
        текста чанка ИЗ ИНДЕКСА. Замер rev-0030: без этого поля за текстом
        приходилось идти вторым вызовом (`code`/`live_read`), то есть 20 хитов
        стоили 20 чтений файлов. Текст берётся одним пакетным запросом к
        хранилищу чанков (lexvec.chunk_texts), а не циклом по хитам.

        Текста может не быть (прежняя ревизия, чужой корпус): тогда поле не
        появляется вовсе, а число таких хитов названо в `limits` — отсутствие
        текста видно, а не замаскировано пустой строкой.
        """
        env = self.make_env("search")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        vector = None
        if self.embedder.available():
            try:
                vector = self.embedder.embed_one(query)
            except Exception:
                vector = None
        # Фильтр по виду чанка применяется ДО обрезки лимитом. Иначе
        # `--kind file` отбрасывал уже выбранную десятку (в неё попадали
        # образцы баз), и документы выглядели ненайденными. Замерено на
        # temp-корпусе: text.pdf был 40-м при limit=60 и нулём при limit=10.
        fetch = limit if not kinds else max(int(limit) * 20, 200)
        hits = self.lexvec.search_hybrid(rev, query, vector, limit=fetch)
        if kinds:
            wanted = set(kinds)
            hits = [h for h in hits if h.source_kind in wanted][:max(1, int(limit))]
        out = []
        stale = self.stale_paths()
        for h in hits:
            item = {
                "chunk_id": h.chunk_id,
                "path": h.path,
                "node_id": h.node_id,
                "start_line": h.start_line,
                "end_line": h.end_line,
                "source_kind": h.source_kind,
                "score": h.score,
                "why": h.why,
            }
            # Роль документа отдаётся только там, где она есть. Поле не
            # добавляется пустым: агент должен видеть признак, а не догадываться,
            # что пустая строка означает «не документ».
            if getattr(h, "doc_role", ""):
                from .docrole import ROLE_TITLES

                item["doc_role"] = h.doc_role
                item["doc_role_title"] = ROLE_TITLES.get(h.doc_role, h.doc_role)
            if h.path in stale:
                item["stale"] = True
            out.append(item)
        env.result = out
        env.found = bool(out)
        if with_text and out:
            texts = self.lexvec.chunk_texts(rev, [str(o["chunk_id"]) for o in out])
            missing = 0
            for item in out:
                raw = texts.get(str(item["chunk_id"]))
                if raw is None:
                    missing += 1
                    continue
                item["snippet"] = _snippet_head(raw, text_width)
            env.extra["text_from_index"] = True
            env.extra["snippet_width"] = int(text_width)
            if missing:
                env.limits.append(
                    f"сниппет недоступен для {missing} из {len(out)} хитов: "
                    "в индексе нет сохранённого текста этих чанков"
                )
        if not out:
            env.extra["suggest"] = ["repo_map", "symbol", "live_read"]
        env.extra["vector_used"] = bool(vector)
        env.extra["vector_available"] = self.lexvec.vec_ok
        self.attach_stale(env, [h.path for h in hits])
        if answer:
            self._answer_mode(env, f"Найдено по запросу «{query}»", out)
        return env.to_dict()

    def _answer_mode(self, env, title: str, items: list[dict],
                     notes: list[str] | None = None) -> None:
        """Заменить структурный результат готовым ответом (P2-2).

        Оболочка (`op`, `index_rev`, `capability`, `found`) остаётся: это
        контракт, по которому клиент понимает, на какую ревизию опирается ответ.
        Убирается СЛУЖЕБНОЕ из `result`: там остаются текст ответа и ссылки.
        """
        sources, total = _answer_sources(items)
        text = _answer_text(title, sources, total, notes=notes)
        env.extra["answer_mode"] = True
        env.extra["answer_chars"] = len(text)
        env.extra["answer_sources"] = sources
        env.result = {"answer": text, "sources": sources,
                      "sources_total": total}
        if len(text) > 2048 and env.limits:
            env.limits.append(
                f"ответ длиннее 2 КБ ({len(text)} символов): сузьте запрос или "
                f"уменьшите limit"
            )

    def op_search_multi(self, queries: list[str], limit: int = 20,
                        scope: str | None = None, regex: bool = False,
                        answer: bool = False) -> dict:
        """1a. Поиск «одним ответом»: несколько запросов, копии, кодировки (P1-1).

        Зачем операция. Один и тот же текст живёт в проекте в десяти местах:
        «Allowed formats» — это восемь копий `php/Wizard/Files*.php`,
        `upload.php` и `templates/Files.html`. Агент искал их грепом по
        одному запросу за раз (2–4 обращения) и всё равно не знал, какой
        файл РАБОЧИЙ, а какой копия, — это выяснялось отдельным чтением JS.
        Здесь на это отвечает один вызов.

        Что делает:
          1. по каждому запросу — ЛИТЕРАЛЬНЫЙ поиск по тексту чанков
             (не по токенам: строка `'MCH', 'MOL'` токенами не ищется),
             с проверкой точным сравнением и с предварительным отбором по FTS;
          2. кодировка файла называется в каждом хите: часть файлов BWO в
             CP1251, и без этого поля нельзя понять, почему раньше русская
             строка не находилась (P1-1, `textdec.py`);
          3. хиты группируются по СЕМЕЙСТВАМ ФАЙЛОВ (`Files.php`…`Files7.php`),
             и у каждого файла названа роль: точка входа / активный (на него
             ссылаются из кода или из разметки) / копия, на которую никто не
             ссылается. Роль подкреплена ссылкой `path:line`;
          4. семейства с активным файлом идут раньше прочих.

        Параметр `regex=True` включает регулярное выражение: предварительный
        отбор берёт из него самую длинную буквенную часть, а точное совпадение
        проверяется регуляркой (за это отвечает `LiteralSearch`).
        """
        env = self.make_env("search_multi")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if isinstance(queries, str):
            queries = [queries]
        clean = [str(q) for q in (queries or []) if str(q).strip()]
        if not clean:
            env.found = False
            env.error = "не передано ни одного запроса"
            env.extra["suggest"] = ["search"]
            return env.to_dict()

        started = time.perf_counter()
        # Сначала сам поиск, и только потом разбор структуры — ПО НАЙДЕННЫМ
        # файлам. Порядок важен: разбор ссылок и точек входа для всего корпуса
        # (на BWO это 142 тысячи рёбер и 141 тысяча записей реестра) занимал
        # больше пяти секунд, тогда как для ответа нужны лишь те файлы, где
        # совпадение нашлось.
        files_basic = analysis.files_index(self.graph, rev)

        per_query: list[dict] = []
        hit_paths: dict[str, int] = {}
        methods: dict[str, int] = {}
        for q in clean:
            hits, method = self.lexvec.literal_hits(
                rev, q, limit=limit, path_prefix=scope, regex=regex
            )
            methods[method] = methods.get(method, 0) + 1
            items = []
            for h in hits:
                path = str(h["path"])
                info = files_basic.get(path)
                item = {
                    "path": path,
                    "line": h["line"],
                    "chunk_id": h["chunk_id"],
                    "source_kind": h["source_kind"],
                    "lang": h["lang"] or (info.lang if info else ""),
                    # Кодировка — не украшение: по ней объясняется, почему файл
                    # в CP1251 раньше не находился по русской строке.
                    "encoding": info.encoding if info else "",
                    "text": h["text"],
                }
                items.append(item)
                hit_paths[path] = hit_paths.get(path, 0) + 1
            per_query.append({
                "query": q,
                "regex": bool(regex),
                "method": method,
                "hits": items,
                "hit_count": len(items),
                "files": len({i["path"] for i in items}),
            })

        kn = analysis.knowledge(self.graph, self.lexvec.conn, rev, with_entrypoints=False,
                                root=self.cfg.root, targets=set(hit_paths))
        # Точки входа — без прохода по разметке: для ответа о копиях хватает
        # роутов, суперглобальных переменных и CLI, а полный проход по тексту
        # корпуса стоил бы секунд (приёмка P1-1 — ответ за 2 с).
        entrypoints = analysis.detect_entrypoints(self.graph, self.lexvec.conn, rev,
                                                  kn.files, kn.refs, text_scan=False,
                                                  root=self.cfg.root,
                                                  targets=set(hit_paths))

        families, group_notes = self._group_hits(hit_paths, kn, entrypoints, rev,
                                                 all_files=files_basic)
        env.result = {
            "scope": scope or "весь корпус",
            "queries": per_query,
            "families": families,
            "summary": {
                "queries": len(clean),
                "hits": sum(q["hit_count"] for q in per_query),
                "files": len(hit_paths),
                "families": len(families),
                "methods": dict(sorted(methods.items())),
                "seconds": round(time.perf_counter() - started, 3),
            },
        }
        env.found = bool(hit_paths)
        if not env.found:
            env.extra["suggest"] = ["search", "repo_map", "symbol"]
            env.limits.append(
                "совпадений нет: проверьте написание строки либо ищите по смыслу через search"
            )
        env.extra["grouping"] = (
            "хиты сгруппированы по семействам файлов (одно имя с разными номерами); "
            "роль файла подтверждена ссылками кода или разметки"
        )
        for note in group_notes:
            env.limits.append(note)
        self.attach_stale(env, sorted(hit_paths)[:200])
        if answer:
            items: list[dict] = []
            for q in per_query:
                for h in q.get("hits", []):
                    items.append({"path": h.get("path"), "line": h.get("line"),
                                  "name": f"[{q.get('query')}]"})
            self._answer_mode(
                env, f"Литеральный поиск по {len(queries)} запросам: "
                     f"{sum(q['hit_count'] for q in per_query)} совпадений",
                items,
            )
        return env.to_dict()

    def op_diff(self, since: str | None = None, limit: int = 50) -> dict:
        """P2-1. Что изменилось между ревизиями: файлы, символы, рёбра, регрессии.

        Зачем операция. Дрейф был только у слоя баз, а вопрос «после серии правок
        ничего не сломалось?» задавался глазами по диффу. Здесь на него отвечают
        факты ИНДЕКСА: что добавилось и исчезло, кто потерял вызывающих
        («эта функция больше нигде не вызывается») и какая таблица перестала
        заполняться.

        Границы доверия названы в ответе: сравниваются факты двух ревизий, и если
        одна из них собрана с `capability=degraded`, часть рёбер отсутствовала —
        тогда «исчезло» может означать «не было разобрано».
        """
        from . import code_diff

        env = self.make_env("diff")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        prev = (since or "").strip() or self._previous_rev(rev)
        if not prev:
            env.found = False
            env.error = ("предыдущая ревизия не найдена: сравнить не с чем. "
                         "Укажите `since` явно.")
            env.extra["suggest"] = ["index_status", "drift_report"]
            return env.to_dict()
        data = code_diff.code_diff(self.graph, prev, rev,
                                   limit=max(1, min(200, int(limit or 50))))
        env.result = data
        env.found = not data.get("same")
        if data.get("same"):
            env.extra["note"] = data.get("note")
        for rev_name in ("from", "to"):
            mode = (data.get("modes") or {}).get(rev_name) or {}
            if str(mode.get("capability") or "") not in ("", "full"):
                env.limits.append(
                    f"ревизия {mode.get('rev_id')} собрана в режиме "
                    f"{mode.get('capability')}: часть связей отсутствовала, и "
                    f"«исчезло» здесь не всегда означает «удалено из кода»"
                )
        env.extra["regressions"] = {
            "callers_lost": (data.get("callers_lost") or {}).get("count", 0),
            "writes_lost": (data.get("writes_lost") or {}).get("count", 0),
        }
        return env.to_dict()

    def op_note(self, text: str, task: str = "", tags: list[str] | None = None,
                path: str = "") -> dict:
        """P2-3. Записать заметку о решении: короткое знание между сессиями.

        Зачем. Найденное в одной сессии («в этой базе часть колонок генерируемые —
        писать нельзя») следующая сессия переоткрывает заново: знание хранится
        только в переписке. Заметка живёт в control-хранилище корпуса, то есть
        рядом с журналом и ревизиями, и не попадает в индекс кода.

        Заметка — не факт о коде: она не проверяется индексом. Поэтому в ответе
        чтения (`notes`) у каждой записи видны автор, время и задача, а поле
        `source` прямо говорит `note` — чтобы её нельзя было принять за ребро
        графа или строку файла.
        """
        from .notes import NotesStore

        env = self.make_env("note")
        text = (text or "").strip()
        if not text:
            env.found = False
            env.error = "текст заметки пуст: записывать нечего"
            return env.to_dict()
        store_obj = NotesStore(self.control)
        try:
            record = store_obj.add(text, task=task, tags=tags or [],
                                   path=path, agent=self.agent or "",
                                   rev_id=self.current_rev() or "")
        except Exception as exc:  # noqa: BLE001 — журнал заметок не должен ломать работу
            env.found = False
            env.error = f"заметку записать не удалось: {type(exc).__name__}: {exc}"
            return env.to_dict()
        env.result = record
        env.found = True
        env.extra["note_count"] = store_obj.count(task=task)
        return env.to_dict()

    def op_notes(self, task: str | None = None, query: str | None = None,
                 limit: int = 50) -> dict:
        """P2-3. Прочитать заметки: по задаче и/или по подстроке текста."""
        from .notes import NotesStore

        env = self.make_env("notes")
        store_obj = NotesStore(self.control)
        try:
            records = store_obj.list(task=task, query=query,
                                     limit=max(1, min(500, int(limit or 50))))
        except Exception as exc:  # noqa: BLE001
            env.found = False
            env.error = f"заметки прочитать не удалось: {type(exc).__name__}: {exc}"
            return env.to_dict()
        env.result = records
        env.found = bool(records)
        env.extra["source"] = "note"
        env.extra["note"] = (
            "заметки — знание АГЕНТА, а не факт индекса: они не проверены кодом, "
            "поэтому не заменяют `search`/`symbol`"
        )
        env.extra["total"] = store_obj.count(task=task)
        if not records:
            env.extra["suggest"] = ["note"]
        return env.to_dict()

    def op_gate(self, payload: dict | str | None = None, mode: str | None = None) -> dict:
        """P3-1 (§3.2). Решение гейта по вызову инструмента: allow / ask / deny.

        Гейт ставится хуком DSH `PreToolUse` и получает `tool_name`, `tool_input`,
        `cwd`, `session_id`. Операция отвечает тем же решением, что и хук, и
        записывает его в состояние гейта — иначе «проверка гейта» разошлась бы с
        тем, что гейт делает на самом деле.

        Границы: гейт не переписывает команду (протокол хуков не применяет
        `updatedInput`), не читает историю переписки и не переиндексирует корпус —
        только дешёвая проверка свежести.
        """
        from . import gate as gate_mod

        env = self.make_env("gate")
        data = payload
        if isinstance(data, str):
            try:
                data = _json.loads(data)
            except Exception as exc:  # noqa: BLE001
                env.found = False
                env.error = f"payload не разобран как JSON: {type(exc).__name__}"
                return env.to_dict()
        if not isinstance(data, dict) or not data:
            env.found = False
            env.error = (
                "нужен payload вызова: {\"tool_name\": ..., \"tool_input\": ..., "
                "\"cwd\": ..., \"session_id\": ...}"
            )
            return env.to_dict()
        try:
            decision = gate_mod.handle_payload(data, mode=mode)
        except Exception as exc:  # noqa: BLE001 — гейт не имеет права падать
            env.found = False
            env.error = f"гейт не смог решить: {type(exc).__name__}: {exc}"
            return env.to_dict()
        state = gate_mod.state().summary(limit=5)
        env.result = {
            **decision.to_dict(),
            "hook": {
                "event": "PreToolUse",
                "stdout": decision.hook_response()[0],
                "exit_code": decision.hook_response()[1],
                "stderr": decision.stderr(),
            },
            "counters": state["counters"],
            "note": (
                "решение гейта записано в состояние; `deny` мост хуков применяет "
                "кодом возврата 2 (причина — в stderr), `ask` — формой "
                "hookSpecificOutput.permissionDecision"
            ),
        }
        env.found = True
        env.extra["verdict"] = decision.verdict
        return env.to_dict()

    def op_task_set(self, name: str | None = None, *, clear: bool = False) -> dict:
        """P3-3. Назвать текущую задачу (`RAGRAF_TASK` без клиента).

        Зачем операция, если есть переменная окружения. Метка `RAGRAF_TASK`
        задаётся при СТАРТЕ процесса, а клиент запускает MCP-сервер один раз на
        сеанс: назвать задачу в его окружении по ходу нельзя. Поэтому метку
        задаёт сам агент — одним вызовом, а состояние общее для процессов
        (его читают и MCP-сервер, и CLI, и хук), поэтому одна названная задача
        собирает все обращения к индексу, каким бы путём они ни шли.

        Пустое имя (или `clear=true`) снимает метку: обращения пойдут без задачи,
        а не под чужой.
        """
        from . import gate as gate_mod

        env = self.make_env("task_set")
        state_obj = gate_mod.state()
        previous = state_obj.current_task(str(self.cfg.root))
        wanted = "" if clear else str(name or "").strip()
        if not wanted:
            record = state_obj.set_current_task("", root=str(self.cfg.root))
            env.result = {
                "task": "",
                "previous": previous,
                "cleared": bool(previous),
                "state_file": str(gate_mod.state_path()),
            }
            env.found = True
            env.extra["note"] = (
                "метка задачи снята: обращения к индексу пойдут без задачи "
                "(в отчёте это видно как 0 по любой метке)"
            )
            return env.to_dict()
        record = state_obj.set_current_task(wanted, root=str(self.cfg.root),
                                           session_id=self.task or "")
        env.result = {
            "task": record["name"],
            "previous": previous,
            "changed": previous != record["name"],
            "root": record["root"],
            "ttl_seconds": int(gate_mod.TASK_TTL_SEC),
            "state_file": str(gate_mod.state_path()),
            "env_var": "RAGRAF_TASK",
        }
        env.found = True
        env.extra["note"] = (
            "метка действует для этого корпуса и живёт "
            f"{int(gate_mod.TASK_TTL_SEC)} с; она перебивает метку сеанса, но "
            "уступает переменной окружения RAGRAF_TASK, если та задана"
        )
        return env.to_dict()

    def op_task_stats(self, task: str | None = None, fmt: str = "markdown",                      limit: int = 10) -> dict:
        """P3-2 (§3.4). Строка отчёта по задаче: чем подтверждена работа.

        Отчёт собирается из трёх независимых источников и называет их прямо:
        журнал обращений (телеметрия), состояние гейта (решения по вызовам
        инструментов) и заметки. Если по задаче обращений не было, отчёт говорит
        об этом числом `0`, а не молчанием: «ноль обращений» — это находка, а не
        отсутствие данных.
        """
        from . import gate as gate_mod
        from .notes import NotesStore

        env = self.make_env("task_stats")
        resolved = (task or self.task or "").strip()
        summary = self.usage_summary(task=resolved or None)
        if summary.get("error"):
            env.found = False
            env.error = str(summary["error"])
            return env.to_dict()
        figures = {
            "task": resolved,
            "calls": int(summary.get("calls_total") or 0),
            "calls_with_facts": int(summary.get("calls_with_facts") or 0),
            "facts": int(summary.get("facts_total") or 0),
            "hit_rate": summary.get("hit_rate"),
            "saved_calls": int(summary.get("saved_calls") or 0),
            "stale_served": int(summary.get("stale_served") or 0),
            "misses": int(summary.get("misses") or 0),
            "avg_ms": summary.get("avg_ms"),
            "by_op": summary.get("by_op") or {},
            "by_task": summary.get("by_task") or {},
        }
        gate = gate_mod.state().summary(limit=500)
        recent_all = [d for d in gate["recent"] if not resolved or d.get("task") == resolved]
        recent = recent_all[:max(1, min(50, int(limit or 10)))]
        gate_figures = {
            "counters": gate["counters"],
            "decisions_for_task": len(recent_all),
            "ask": sum(1 for d in recent_all if d.get("verdict") == "ask"),
            "deny": sum(1 for d in recent_all if d.get("verdict") == "deny"),
            "sessions": gate["sessions"],
            "write_failures": gate["write_failures"],
        }
        try:
            notes_total = NotesStore(self.control).count(task=resolved or None)
        except Exception:  # noqa: BLE001
            notes_total = 0
        figures["notes"] = notes_total
        figures["gate"] = gate_figures

        lines = [f"# Отчёт по задаче: {resolved or '(метка не задана)'}", ""]
        lines.append(f"* индекс: ревизия `{self.current_rev() or '—'}`, "
                     f"режим `{self.capability()}`")
        lines.append(f"* обращений к индексу: **{figures['calls']}**, "
                     f"с фактами {figures['calls_with_facts']}, "
                     f"фактов {figures['facts']}, попаданий "
                     f"{figures['hit_rate'] if figures['hit_rate'] is not None else '—'}")
        lines.append(f"* заменено чтения файлов: **{figures['saved_calls']}**, "
                     f"ответов по устаревшей ревизии: {figures['stale_served']}, "
                     f"среднее время вызова: "
                     f"{figures['avg_ms'] if figures['avg_ms'] is not None else '—'} мс")
        lines.append(f"* заметок по задаче: {notes_total}")
        lines.append("")
        lines.append("## Гейт использования индекса")
        lines.append(f"* решений по задаче: {gate_figures['decisions_for_task']} "
                     f"(запрос подтверждения {gate_figures['ask']}, "
                     f"отказов {gate_figures['deny']})")
        lines.append(f"* всего решений: {gate_figures['counters'].get('total', 0)} "
                     f"(allow {gate_figures['counters'].get('allow', 0)}, "
                     f"ask {gate_figures['counters'].get('ask', 0)}, "
                     f"deny {gate_figures['counters'].get('deny', 0)})")
        if recent:
            lines.append("")
            for d in recent[:5]:
                when = time.strftime("%H:%M:%S", time.localtime(float(d.get("ts") or 0)))
                lines.append(f"* {when} `{d.get('tool')}` → **{d.get('verdict')}** "
                             f"({d.get('rule')}): {str(d.get('reason') or '')[:120]}")
        if not figures["calls"]:
            lines.append("")
            lines.append("> Обращений к индексу по этой задаче НЕ БЫЛО: факты, если "
                         "они использовались, взяты чтением файлов.")
        markdown = "\n".join(lines) + "\n"

        env.result = {
            "task": resolved,
            "markdown": markdown,
            "figures": figures,
            "report": markdown if str(fmt).lower() == "markdown" else figures,
        }
        env.found = True
        env.extra["note"] = (
            "отчёт собран по журналу обращений, состоянию гейта и заметкам; "
            "числа проверяемы: `usage --task <метка>`"
        )
        return env.to_dict()

    def _group_hits(self, hit_paths: dict[str, int], kn, entrypoints: dict,
                    rev: str, all_files: dict | None = None) -> tuple[list[dict], list[str]]:
        """Сгруппировать файлы с совпадениями по семействам и назвать их роль.

        Роль определяется ДВУМЯ независимыми признаками, и оба попадают в
        ответ: входящие ссылки кода (include/require и пути, упомянутые
        строкой — их собирает индексатор) и признак точки входа (роут,
        суперглобальные переменные, CLI). Ссылки и отвечают на вопрос «какой
        из копий рабочий»: в шаблоне BWO стоит
        `var BWO_FILES_ENDPOINT = "/php/Wizard/Files7.php"`.

        Семейство бывает ШИРЕ найденных файлов: `Files7.php` подключён из
        `templates/Files.html`, но искомой строки в нём может не быть. Поэтому
        после разбора найденных файлов ссылки спрашиваются ещё раз — по
        остальным членам тех же семейств: без этого ответ называл бы
        «активным» не самый подключённый файл, а самый подключённый из
        совпавших.
        """
        by_family: dict[str, list[str]] = {}
        for path in hit_paths:
            by_family.setdefault(analysis.family_key(path), []).append(path)

        # Члены тех же семейств, которых нет среди найденных.
        extra_refs: dict[str, list] = {}
        if all_files:
            extra: set[str] = set()
            for path in all_files:
                if path in hit_paths:
                    continue
                if analysis.family_key(path) in by_family:
                    extra.add(path)
            extra = set(sorted(extra)[:80])
            if extra:
                extra_refs = analysis.graph_refs(
                    self.graph, rev, kn.files, targets=extra
                )[0]

        # Роли определяются по ГРАФУ и точкам входа. Текстовые ссылки
        # (`BWO_FILES_ENDPOINT = "/php/Wizard/Files7.php"`) попадают в граф на
        # этапе индексации (`file_ref_edges`), поэтому отдельный поиск по
        # тексту здесь не нужен: измерено на BWO — он стоит 0,5 с на файл и
        # уводил ответ за две секунды, которые требует приёмка. У операции
        # `duplicates` такой поиск остаётся: там он делается для одного
        # главного файла группы, а не для всех найденных.
        literal_used = 0
        out: list[dict] = []
        for key, members in sorted(by_family.items()):
            members = sorted(members)
            graph_refs = {p: kn.incoming(p) for p in members}
            details = []
            for path in members:
                code_refs = graph_refs[path]
                text_refs: list = []
                # Роль отвечает на РАЗНЫЕ вопросы, и смешивать их нельзя:
                #   * на файл ссылаются из кода или разметки — это и есть
                #     «рабочий файл», только он и считается главным;
                #   * файл обслуживает запрос напрямую (`$_GET`), но на него
                #     никто не ссылается — он не копия, но и не подтверждён
                #     как рабочий.
                # Порядок проверок именно такой: у копий `Files*.php` признак
                # «читает $_GET» есть у ВСЕХ восьми, и ранжирование по нему
                # выбирало главным первый по алфавиту, а не подключённый.
                if code_refs:
                    role = "active"
                elif path in entrypoints:
                    role = "entrypoint"
                else:
                    role = "copy"
                evidence = [
                    {"path": r.path, "line": r.line, "kind": r.kind, "text": r.text}
                    for r in (list(code_refs) + list(text_refs))[:5]
                ]
                details.append({
                    "path": path,
                    "role": role,
                    "hits": hit_paths.get(path, 0),
                    "incoming_code_refs": len(code_refs),
                    "referenced_in_text": [
                        {"path": r.path, "line": r.line, "text": r.text}
                        for r in text_refs[:5]
                    ],
                    "evidence": evidence,
                    "lang": kn.files.get(path).lang if path in kn.files else "",
                })
            ranking = {"active": 0, "entrypoint": 1, "copy": 2}
            # Порядок внутри роли: больше подтверждённых ссылок → больше
            # совпадений поиска → путь. Число ссылок важнее числа совпадений:
            # на BWO `Files7.php` подключён из семи шаблонов (в том числе
            # `templates/Files.html`), а `Files.php` — из пяти; решать по
            # количеству найденных строк значило бы выбирать главного файла
            # по тому, сколько раз строка попала в его окна.
            details.sort(key=lambda d: (ranking.get(d["role"], 3),
                                        -d["incoming_code_refs"], -d["hits"], d["path"]))
            active = details[0]["path"] if details else ""
            copies = [d["path"] for d in details if d["role"] == "copy"]
            also_referenced = [d["path"] for d in details[1:] if d["role"] == "active"]
            # Подключённые копии ВНЕ найденных совпадений: они не хиты, но
            # именно они отвечают на вопрос «какой файл семейства рабочий».
            outside = [
                {
                    "path": p,
                    "incoming_code_refs": len(items),
                    "evidence": [
                        {"path": r.path, "line": r.line, "kind": r.kind, "text": r.text}
                        for r in items[:3]
                    ],
                }
                for p, items in sorted(extra_refs.items())
                if analysis.family_key(p) == key
            ]
            item = {
                "family": key,
                "members": details,
                "active": active,
                "copies": copies,
                "also_referenced": also_referenced,
                "referenced_outside_hits": outside,
            }
            if len(members) == 1:
                item["note"] = "одно вхождение семейства: копий рядом нет"
            elif details[0]["role"] == "active":
                first = details[0]
                why = first["evidence"][0] if first["evidence"] else None
                note = f"активен {active}" + (
                    f": ссылка {why['path']}:{why['line']}" if why else ""
                )
                if also_referenced:
                    note += f"; подключены также: {', '.join(also_referenced)}"
                if copies:
                    note += f"; не подключены: {', '.join(copies)}"
                item["note"] = note
            else:
                item["note"] = (
                    "ни на один файл семейства нет ссылок: активного нет, "
                    "все — кандидаты на проверку вручную"
                )
                item["copies"] = []
            if outside:
                best = outside[0]
                item["note"] += (
                    f". Семейство шире найденного: на {best['path']} ссылаются"
                    + (f" из {best['evidence'][0]['path']}:{best['evidence'][0]['line']}"
                       if best.get("evidence") else "")
                )
            out.append(item)
        notes: list[str] = []
        if not any(kn.incoming(p) for p in hit_paths):
            # Ни одна ссылка не подтверждена графом — это надо назвать прямо,
            # а не выдавать «копия» за доказанный факт. Ссылки строкой
            # попадают в граф при индексации; если индекс построен прежней
            # версией, их там нет, и поможет переиндексация.
            notes.append(
                "подтверждённых ссылок в графе нет ни на один найденный файл: "
                "если код ссылается на них строкой, индекс построен до появления "
                "разбора таких ссылок — поможет переиндексация"
            )
        out.sort(key=lambda g: (0 if g["active"] and g["copies"] else 1, -sum(m["hits"] for m in g["members"]), g["family"]))
        return out, notes

    def op_live_read(self, path: str, start: int | None = None, end: int | None = None,
                     context: int = 5) -> dict:
        """16. Чтение актуального файла с выделением отличий от индекса (ФТ-31)."""
        env = self.make_env("live_read", provenance="live")
        rev = self.current_rev()
        # Пустой путь раньше превращался в корень корпуса: `root / ""` — это
        # каталог, он существует, а `read_bytes()` на каталоге давал
        # PermissionError. Наружу уходила «ошибка выполнения» без объяснения,
        # хотя причина — отсутствующий адрес. Проверка пути и того, что это
        # файл, ставится до чтения.
        if not (path or "").strip():
            env.found = False
            env.error = "не указан путь файла"
            env.extra["suggest"] = ["repo_map", "search"]
            return env.to_dict()
        abs_path = self.cfg.root / util.to_posix(path)
        try:
            resolved = abs_path.resolve()
            resolved.relative_to(self.cfg.root.resolve())
        except (ValueError, OSError):
            env.found = False
            env.error = f"путь вне корпуса: {path}"
            return env.to_dict()
        if not resolved.exists():
            env.found = False
            env.error = f"файл не найден: {path}"
            return env.to_dict()
        if resolved.is_dir():
            # Каталог — не файл: говорим об этом прямо, а не отдаём отказ
            # доступа, который читается как проблема прав.
            env.found = False
            env.error = f"это каталог, а не файл: {path}"
            env.extra["suggest"] = ["repo_map"]
            return env.to_dict()

        data = resolved.read_bytes()
        sha_now = util.sha256_bytes(data)
        sha_index = None
        if rev:
            r = self.graph.execute(
                "SELECT sha256, lines FROM file WHERE rev_id = ? AND path = ?",
                (rev, util.to_posix(path)),
            ).fetchone()
            sha_index = str(r["sha256"]) if r else None

        text = util.decode_text(data, path)
        extract_kind = ""
        # Документ читается тем же извлекателем, что и при индексации: иначе
        # номера строк в `path:line` и в `live_read` разошлись бы, и сверка
        # «изменился ли файл» показывала бы ложное расхождение.
        try:
            from . import documents as _documents

            suffix = resolved.suffix.lower()
            if suffix in _documents.SUPPORTED or suffix in _documents.LEGACY:
                doc = _documents.extract(resolved)
                extract_kind = doc.kind
                if doc.status == "ok":
                    text = doc.text
                else:
                    text = ""
                    env.limits.append(f"текст документа не извлечён: {doc.reason}")
        except Exception as exc:  # noqa: BLE001 — live_read не должен падать из-за извлекателя
            env.limits.append(f"извлечение документа недоступно: {type(exc).__name__}: {exc}")
        lines = text.splitlines()
        if start is None:
            start = 1
        if end is None:
            end = min(len(lines), start + 400)
        seg = lines[max(0, start - 1) : end]
        env.result = {
            "path": util.to_posix(path),
            "requested": {"start": start, "end": end},
            "total_lines": len(lines),
            "text": "\n".join(seg),
            "sha256_now": sha_now,
            "sha256_index": sha_index,
            "changed_since_index": sha_index is not None and sha_now != sha_index,
        }
        if sha_index is None:
            env.extra["in_index"] = False
            env.limits.append("файл отсутствует в индексе: вывод опирается только на текущее содержимое")
        else:
            env.extra["in_index"] = True
        if extract_kind:
            env.extra["extract_kind"] = extract_kind
            env.extra["extract_note"] = (
                "строки — извлечённый текст документа (маркеры страницы/листа/слайда), "
                "а не строки двоичного файла"
            )
        if env.result["changed_since_index"]:
            env.stale_warnings.append(
                {"path": util.to_posix(path), "reason": f"файл изменён после {rev}",
                 "since": self.drift_summary().get("last_ts"), "suggest": "live_read"}
            )
            env.provenance = "mixed"
        return env.to_dict()

    def op_drift_report(self, since: int | None = None, limit: int = 500) -> dict:
        """14. Журнал расхождений."""
        env = self.make_env("drift_report")
        rev = self.current_rev()
        sql = "SELECT id, index_rev, ts, path, kind, old_hash, new_hash, symbols_added," \
              " symbols_removed, symbols_changed, edges_invalidated, detected_by, commit_sha," \
              " ack, note FROM drift_entry"
        params: list[Any] = []
        where = []
        if rev:
            where.append("index_rev = ?")
            params.append(rev)
        if since is not None:
            where.append("id > ?")
            params.append(since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.control.conn.execute(sql, tuple(params)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            for col in ("symbols_added", "symbols_removed", "symbols_changed"):
                if item.get(col):
                    try:
                        item[col] = util.loads(item[col])
                    except Exception:
                        item[col] = []
            item["ack"] = util.loads(item["ack"]) if item.get("ack") else None
            out.append(item)
        env.result = out
        env.found = bool(out)
        env.extra["summary"] = self.drift_summary()
        if not out:
            env.extra["suggest"] = ["index_status"]
            env.limits.append("журнал пуст: индекс соответствует рабочему дереву")
        return env.to_dict()

    def op_schema(self, table: str | None = None, limit: int = 200) -> dict:
        """10. Схема БД из миграций проекта (M2, P1-4).

        Признаки колонки отдаются отдельными полями, а не одной строкой
        `column_key`: по ним принимается решение о записи (`write_check`), и
        «строка, которую каждый читатель разбирает сам» уже давала измеренный
        дефект — колонка считалась генерируемой по подстроке `GENERATED`.
        """
        env = self.make_env("schema")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if not store.table_exists(self.graph, "db_table"):
            env.found = False
            env.error = "слой схемы БД не построен (этап M2)"
            return env.to_dict()
        params: list[Any] = [rev]
        sql = "SELECT table_id, table_name, engine, row_estimate, source FROM db_table WHERE rev_id = ?"
        if table:
            sql += " AND table_name = ?"
            params.append(table)
        sql += " LIMIT ?"
        params.append(limit)
        rows = self.graph.execute(sql, tuple(params)).fetchall()
        cols_available = self._db_column_marks_available()
        out = []
        for r in rows:
            extra_cols = (
                ", is_unique, auto_increment, generated_kind, has_default"
                if cols_available else ""
            )
            cols = self.graph.execute(
                "SELECT column_name, data_type, is_nullable, column_key"
                + extra_cols
                + " FROM db_column WHERE rev_id = ? AND table_id = ? ORDER BY ordinal",
                (rev, r["table_id"]),
            ).fetchall()
            columns = []
            for c in cols:
                item = dict(c)
                kind = str(item.pop("generated_kind", "") or "")
                item.update(
                    {
                        "pk": str(item.get("column_key") or "").upper() == "PRI",
                        "unique": bool(item.pop("is_unique", 0))
                        or str(item.get("column_key") or "").upper() == "UNI",
                        "auto_increment": bool(item.pop("auto_increment", 0)),
                        "has_default": bool(item.pop("has_default", 0)),
                        "generated": bool(kind),
                        "generated_kind": kind,
                    }
                )
                columns.append(item)
            table_item = dict(r)
            table_item["columns"] = columns
            table_item["primary_key"] = [c["column_name"] for c in columns if c["pk"]]
            table_item["unique_columns"] = [c["column_name"] for c in columns if c["unique"]]
            table_item["generated_columns"] = [c["column_name"] for c in columns if c["generated"]]
            out.append(table_item)
        env.result = out
        env.found = bool(out)
        if out:
            env.extra["columns_total"] = sum(len(t["columns"]) for t in out)
            env.extra["generated_total"] = sum(len(t["generated_columns"]) for t in out)
        return env.to_dict()

    def _db_column_marks_available(self) -> bool:
        """Есть ли в `db_column` колонки признаков (P1-4).

        На корпусах, построенных до этой правки, их нет: догоняющая миграция
        выполняется при ЗАПИСИ, а операции чтения открывают граф только для
        чтения. Без проверки `SELECT` упал бы на «no such column».
        """
        try:
            cols = {str(r[1]) for r in self.graph.execute("PRAGMA table_info(db_column)")}
        except Exception:
            return False
        return "generated_kind" in cols

    def op_table_usage(self, table: str, limit: int = 200, mode: str = "all") -> dict:
        """11. Кто читает и кто пишет таблицу (U4, U5).

        Что изменилось по итогам исследования GAPS_FOR_LLM:
          * U4: в каждом факте есть `write` и `access` (select/insert/update/
            delete/ddl). Раньше признак вычислялся при извлечении и выбрасывался,
            поэтому на вопрос «где именно идёт вставка» ответить было нечем;
          * U5: таблицы, которых НЕТ в схеме из миграций, выносятся отдельным
            списком `unresolved_tables`. Причина: регулярка имени таблицы
            срабатывает на конструкциях языка (CREATE TABLE, <table> в разметке),
            и в выдаче появлялись фантомные имена вида `value`, `hash`, `ugroup`,
            смешанные с подтверждёнными таблицами. Доверие к операции от этого
            падало, а различие «подтверждено схемой» и «похоже на таблицу» важно.

        Параметр mode: all — всё, read — только читающие обращения,
        write — только изменяющие.
        """
        env = self.make_env("table_usage")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if mode not in ("all", "read", "write"):
            env.found = False
            env.error = "неизвестный режим: ожидается all, read или write"
            return env.to_dict()

        # Схема из миграций: по ней решается, подтверждена таблица или нет.
        known = self._known_tables(rev)

        dst = f"@table:{table.lower()}"
        rows = self.graph.execute(
            """SELECT src, kind, provenance, evidence_path, evidence_line, confidence, props
               FROM edge WHERE rev_id = ? AND dst = ? LIMIT ?""",
            (rev, dst, limit),
        ).fetchall()
        out = []
        for r in rows:
            owner = self.graph.execute(
                "SELECT name, fqn, kind, path, start_line FROM node WHERE rev_id = ? AND node_id = ?",
                (rev, r["src"]),
            ).fetchone()
            edge_props: dict = {}
            try:
                edge_props = util.loads(r["props"]) if r["props"] else {}
            except Exception:
                edge_props = {}
            access = str(edge_props.get("access") or "")
            is_write = bool(edge_props.get("write"))
            # Фильтр по виду доступа, если он запрошен.
            if mode == "read" and is_write:
                continue
            if mode == "write" and not is_write:
                continue
            evidence = f"{r['evidence_path']}:{r['evidence_line']}"
            # Плоские path/line: агент должен получать ссылку в том же виде, что и
            # в других операциях. Без этого проверяемость ответа падала до нуля
            # (найдено замером на полигоне: 29 фактов, ни одного со ссылкой).
            item = {
                "path": r["evidence_path"],
                "line": r["evidence_line"],
                "symbol": dict(owner) if owner else None,
                "kind": r["kind"],
                "provenance": r["provenance"],
                "evidence": evidence,
            }
            # Поля доступа добавляются только при наличии данных: пустая строка
            # читалась бы как «в индексе нет признака», хотя он есть.
            if access:
                item["write"] = is_write
                item["access"] = access
            # Текст запроса и способ вызова (P1-4): «кто пишет» без текста
            # заставляет идти в файл за подтверждением, а `via` отличает
            # подготовленное выражение от прямого вызова.
            if edge_props.get("sql"):
                item["sql"] = str(edge_props["sql"])
            if edge_props.get("via"):
                item["via"] = str(edge_props["via"])
            out.append(item)

        # Список таблиц, на которые есть обращения, но которых нет в схеме.
        unresolved = [] if table.lower() in known else [
            {
                "name": table,
                "reason": "обращения в коде есть, но в схеме из миграций таблица не найдена",
                "hint": "возможная опечатка в SQL, обращение к системной таблице или неполнота разбора миграций",
            }
        ]

        env.result = {
            "table": table,
            "table_in_schema": table.lower() in known,
            "references": out,
            "read_count": sum(1 for x in out if not x.get("write")),
            "write_count": sum(1 for x in out if x.get("write")),
            "unresolved_tables": unresolved,
        }
        env.found = bool(out)
        if not env.found:
            env.extra["suggest"] = ["schema", "write_check", "search"]
            env.extra["note"] = (
                "обращений к таблице в коде не найдено; проверьте имя через schema"
                if table.lower() in known
                else "таблицы нет в схеме из миграций — проверьте имя и написание"
            )
        self.attach_stale(env, [str(x["path"]) for x in out if x["path"]])
        return env.to_dict()

    def _known_tables(self, rev: str) -> set[str]:
        """Набор таблиц из схемы (миграции) в нижнем регистре.

        Нужен, чтобы отличать подтверждённые таблицы от фантомных имён
        (U5): регулярка имени таблицы срабатывает и на конструкциях языка.
        """
        try:
            rows = self.graph.execute(
                "SELECT table_name FROM db_table WHERE rev_id = ?", (rev,)
            ).fetchall()
            return {str(r["table_name"]).lower() for r in rows}
        except Exception:
            return set()

    def op_policy_trace(self, symbol: str, action: str | None = None,
                        depth: int = 3, limit: int = 300) -> dict:
        """22. Трассировка решений о правах (U6).

        Зачем операция. Самый дорогой дефект на полигоне (№9 в отчёте по
        разработке) нашёлся только проверкой под реальной ролью: роль
        `employee` получала права `approve`, `mark_paid`, `sign`, `archive`,
        потому что ветвь `switch` возвращала `true` всем, кому выдан журнал.
        Никакой замер навигации такое не показывает, а глазами в большом
        `switch` это теряется.

        Что делает операция: по символу, принимающему решение о правах,
        собирает все места, где возвращается разрешение, вместе с условиями,
        и все места, где вызывается проверка этого символа.

        Что возвращает:
          * allows: ветви, возвращающие разрешение, — файл, строка, текст строки,
            номер ветви `case` (если определён);
          * denies: ветви явного отказа;
          * default_behavior: что происходит, если ни одно условие не совпало.
            Значение `deny` означает fail-closed — это правильно; `allow` —
            признак дефекта, потому что неизвестное действие получает право;
          * callers: кто вызывает проверку — это точки, где решение применяется.

        Границы: операция статическая. Она показывает ветви в коде, а не
        фактический результат для конкретного пользователя: значения ролей
        берутся из базы, которой у индекса нет.
        """
        env = self.make_env("policy_trace")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)

        rows = self._symbol_rows(rev, symbol, None, 3)
        if not rows:
            env.found = False
            env.error = f"символ не найден: {symbol}"
            env.extra["suggest"] = ["symbol", "search"]
            return env.to_dict()

        target = rows[0]
        path = str(target.get("path") or "")
        start = int(target.get("start_line") or 0)
        end = int(target.get("end_line") or 0)

        allows: list[dict] = []
        denies: list[dict] = []
        resolved_read = False

        # Разбор тела идёт по актуальному файлу: решение о правах важно видеть
        # в текущем виде, а не в версии на момент индексации.
        try:
            abs_path = self.cfg.root / path
            text = util.decode_text(abs_path.read_bytes(), path)
            resolved_read = True
            lines = text.splitlines()
            # Ветвь `case` отслеживается, чтобы в выдаче было видно,
            # к какому действию относится решение.
            current_case = ""
            default_line = 0
            default_allow = False

            for i in range(max(0, start - 1), min(len(lines), end)):
                line = lines[i]
                line_no = i + 1
                stripped = line.strip()

                # Номер ветви: `case 'create':` или `case "update":`.
                cm = re.match(r"case\s+['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*:", stripped)
                if cm:
                    current_case = cm.group(1)
                    continue
                if re.match(r"default\s*:", stripped):
                    current_case = "default"
                    default_line = line_no
                    continue

                if action and current_case and action.lower() not in current_case.lower():
                    # Если запрошено конкретное действие, ветви других действий
                    # пропускаются: цель — не вывалить весь switch.
                    continue

                ret = re.search(r"\breturn\s+(true|false)\s*;", stripped)
                if ret or re.search(r"return\s+\$?\w*(?:->|\[).*;|return\s+\$\w+\s*;", stripped):
                    # Решение: явное true/false либо возврат вычисленного значения.
                    value = ret.group(1) if ret else "computed"
                    item = {
                        "path": path,
                        "line": line_no,
                        "case": current_case or None,
                        "returns": value,
                        "text": stripped[:160],
                    }
                    if value == "false":
                        denies.append(item)
                    else:
                        allows.append(item)
                    if current_case == "default":
                        default_allow = value in ("true", "computed")
        except Exception as exc:
            env.limits.append(f"тело символа прочитать не удалось: {type(exc).__name__}")

        # Кто вызывает проверку: это точки применения решения.
        callers: list[dict] = []
        try:
            steps = self._bfs(rev, [str(target.get("node_id"))], depth, reverse=True,
                              limit=limit)
            for s in steps:
                # Ключ шага — edge_kind, а не relation. Ошибка в имени ключа
                # привела к тому, что вызывающие не находились, хотя рёбра
                # в графе есть (найдено проверкой .staging/check_callers.py).
                if s.get("edge_kind") in ("calls", "calls_heuristic"):
                    callers.append({
                        "path": s.get("path"),
                        "line": s.get("start_line"),
                        "symbol": s.get("name") or s.get("fqn"),
                        "provenance": s.get("provenance"),
                        "edge_kind": s.get("edge_kind"),
                    })
        except Exception as exc:
            env.limits.append(f"обход вызовов не выполнен: {type(exc).__name__}")

        # Поведение по умолчанию: если ни одно условие не совпало, остаётся
        # последний `return false` или ветвь default.
        if default_line:
            default_behavior = "allow" if default_allow else "deny"
        else:
            default_behavior = "deny" if denies else ("allow" if allows else "unknown")

        env.result = {
            "symbol": symbol,
            "path": path,
            "lines": {"start": start, "end": end},
            "action_filter": action,
            "allows": allows,
            "denies": denies,
            "default_behavior": default_behavior,
            "callers": callers,
            "body_read": resolved_read,
        }
        env.found = bool(allows or denies)

        # Самый важный вывод операции: признак fail-closed. Если функция разрешает
        # всё, что не описано явно, это дефект, а не решение.
        if default_behavior == "allow":
            env.extra["warning"] = (
                "проверка разрешает действие, если оно не описано явно: "
                "неизвестное действие получает право"
            )
            env.stale_warnings.append({
                "path": path,
                "reason": "fail-open: неназванное действие разрешено",
                "suggest": "проверить ветви глазами",
            })
        env.extra["fail_closed"] = default_behavior == "deny"
        if not env.found:
            env.extra["suggest"] = ["code", "search"]
            env.extra["note"] = "в теле символа не найдено решений о правах"
        self.attach_stale(env, [path])
        return env.to_dict()

    def op_class_usage(self, name: str | None = None, limit: int = 200,
                       mode: str = "all") -> dict:
        """23. Классы оформления: где определён, где использован, что расходится.

        Зачем операция. Класс оформления — самый частый объект, с которым
        сверяются при правке интерфейса: «есть ли стиль», «где ещё используется»,
        «не останется ли кнопка без оформления». Без этой операции агент ищет
        классы текстом по файлам и пропускает расхождения.

        Три режима:
          * `all` (по умолчанию) — сводка по одному классу или список проблемных;
          * `missing` — классы, используемые в разметке, но НЕ описанные
            в стилях. Это реальный класс дефектов: страница работает и
            выглядит «как-то» за счёт оформления браузера по умолчанию,
            поэтому ошибка не проявляется при запуске;
          * `unused` — классы, описанные в стилях, но нигде не используемые.

        Параметр `name` задаёт конкретный класс (без ведущей точки). Если он
        не задан — возвращается список по режиму.
        """
        env = self.make_env("class_usage")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if mode not in ("all", "missing", "unused"):
            env.found = False
            env.error = "неизвестный режим: ожидается all, missing или unused"
            return env.to_dict()

        clean_name = (name or "").lstrip(".")

        if clean_name:
            rows = self.graph.execute(
                """SELECT e.kind, e.evidence_path AS path, e.evidence_line AS line,
                          e.provenance, e.props
                     FROM edge e JOIN node n
                       ON n.rev_id = e.rev_id AND n.node_id = e.dst
                    WHERE e.rev_id = ? AND n.kind = 'css_class' AND n.name = ?
                      AND e.kind IN ('defines_class','uses_class')
                    ORDER BY e.kind, e.evidence_path, e.evidence_line LIMIT ?""",
                (rev, clean_name, limit),
            ).fetchall()
            defines = [dict(r) for r in rows if r["kind"] == "defines_class"]
            uses = [dict(r) for r in rows if r["kind"] == "uses_class"]
            env.result = {
                "class": clean_name,
                "defined": [
                    {"path": r["path"], "line": r["line"],
                     "selector": (util.loads(r["props"]) or {}).get("selector")
                                 if r["props"] else None}
                    for r in defines
                ],
                "used": [{"path": r["path"], "line": r["line"]} for r in uses],
                "defined_count": len(defines),
                "used_count": len(uses),
                "status": ("только используется: правила в стилях нет"
                           if uses and not defines else
                           "только описан: в разметке не найден"
                           if defines and not uses else
                           "описан и используется" if defines and uses else
                           "не найден ни в стилях, ни в разметке"),
            }
            env.found = bool(defines or uses)
            if not env.found:
                env.extra["suggest"] = ["search", "class_usage"]
                env.limits.append(
                    "класс не найден: если он определён вне проиндексированных файлов, "
                    "добавьте путь в include конфигурации корпуса"
                )
            self.attach_stale(env, [r["path"] for r in rows if r["path"]])
            return env.to_dict()

        # Сводный режим: список классов по состоянию.
        having = "HAVING defs = 0 AND uses > 0" if mode == "missing" else \
                 "HAVING defs > 0 AND uses = 0" if mode == "unused" else \
                 "HAVING defs = 0 AND uses > 0"
        rows = self.graph.execute(
            f"""SELECT n.name,
                       SUM(CASE WHEN e.kind = 'defines_class' THEN 1 ELSE 0 END) AS defs,
                       SUM(CASE WHEN e.kind = 'uses_class' THEN 1 ELSE 0 END) AS uses
                  FROM node n JOIN edge e
                    ON e.rev_id = n.rev_id AND e.dst = n.node_id
                 WHERE n.rev_id = ? AND n.kind = 'css_class'
                   AND e.kind IN ('defines_class','uses_class')
                 GROUP BY n.node_id {having}
                 ORDER BY uses DESC, n.name LIMIT ?""",
            (rev, limit),
        ).fetchall()
        out = []
        for r in rows:
            first = self.graph.execute(
                """SELECT e.evidence_path AS path, e.evidence_line AS line
                     FROM edge e JOIN node n ON n.rev_id = e.rev_id AND n.node_id = e.dst
                    WHERE e.rev_id = ? AND n.name = ?
                      AND e.kind = ? LIMIT 1""",
                (rev, r["name"],
                 "uses_class" if mode != "unused" else "defines_class"),
            ).fetchone()
            out.append({
                "name": r["name"],
                "defined_count": int(r["defs"] or 0),
                "used_count": int(r["uses"] or 0),
                "first_at": (f"{first['path']}:{first['line']}" if first else None),
            })
        env.result = {
            "mode": mode,
            "classes": out,
            "count": len(out),
            "note": ("классы используются в разметке, но правил в стилях нет: "
                     "страница отрисуется оформлением браузера по умолчанию"
                     if mode == "missing" else
                     "классы описаны, но нигде не используются" if mode == "unused"
                     else "классы без правил в стилях"),
        }
        env.found = bool(out)
        if not out:
            env.limits.append(
                "расхождений нет" if mode == "missing"
                else "неиспользуемых классов нет"
            )
        return env.to_dict()

    def op_write_check(self, table: str, columns: list[str] | None = None,
                       operation: str = "insert", values: int | None = None) -> dict:
        """18. Сверка плана записи со схемой ДО выполнения (U1).

        Зачем операция. Дефект №7 на полигоне: код писал в колонки AuthorUserID
        и UpdatedBy, которых в таблицах журналов нет (там InitiatorID, а UpdatedBy
        есть только у одной таблицы). Ошибка драйвера 42S22 всплыла только при
        первом сохранении — то есть после написания кода и запуска.

        Схема в индексе есть (стадия schema разбирает миграции), но операции
        «сверь мой список колонок» не было: каждый раз её делали глазами.
        Эта операция закрывает разрыв и отвечает ДО запуска.

        Что возвращает:
          ok               — все колонки найдены и обязательные (NOT NULL без
                             значения по умолчанию) при вставке заданы;
          unknown_columns  — колонки, которых нет, с ближайшим похожим именем
                             (подсказка правится одним сравнением);
          insert_required  — колонки NOT NULL без DEFAULT, которые надо задать;
          primary_key      — ключевые колонки (для UPDATE нужны в условии).

        Границы: операция НЕ подключается к базе. Источник — схема из миграций,
        то есть индексация остаётся работоспособной без СУБД.
        """
        env = self.make_env("write_check")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)

        operation = (operation or "insert").lower()
        if operation not in ("insert", "update", "delete"):
            env.found = False
            env.error = "неизвестная операция: ожидается insert, update или delete"
            return env.to_dict()

        # Ищем таблицу без учёта регистра: в миграциях и в коде регистр различается.
        trow = self.graph.execute(
            "SELECT table_id, table_name, source FROM db_table"
            " WHERE rev_id = ? AND lower(table_name) = lower(?) LIMIT 1",
            (rev, table),
        ).fetchone()
        # Таблицы может не быть в миграциях — и это НЕ повод отказывать.
        # Измерено на BWO: рабочие таблицы импорта (`TestConfig`, `TestData`,
        # `StringData`, `cellvoltagedata`) есть в живой базе `wizard_online`,
        # а миграций в корпусе нет вовсе (`db_table` пуст). Прежняя редакция в
        # этом случае отвечала «таблица не найдена», то есть проверка плана
        # записи не работала ровно там, где она и нужна: в проекте без миграций.
        live_only = False
        if trow is None and store.table_exists(self.graph, "db_live_table"):
            found_live = self.graph.execute(
                "SELECT COUNT(*) AS n FROM db_live_table"
                " WHERE rev_id = ? AND lower(table_name) = lower(?)",
                (rev, table),
            ).fetchone()
            live_only = bool(found_live and int(found_live["n"] or 0))
        if trow is None and not live_only:
            env.found = False
            # Подсказка с похожими именами: без неё агент не поймёт, опечатка это
            # или таблица вообще не разобрана из миграций.
            near = [
                str(r["table_name"])
                for r in self.graph.execute(
                    "SELECT table_name FROM db_table WHERE rev_id = ? ORDER BY table_name",
                    (rev,),
                ).fetchall()
            ]
            if store.table_exists(self.graph, "db_live_table"):
                near.extend(
                    str(r["table_name"])
                    for r in self.graph.execute(
                        "SELECT DISTINCT table_name FROM db_live_table"
                        " WHERE rev_id = ? ORDER BY table_name LIMIT ?",
                        (rev, 2000),
                    ).fetchall()
                )
            liked = util.closest_names(table, sorted(set(near)), limit=5)
            env.extra["suggest"] = ["schema", "db_schema"]
            env.extra["similar_tables"] = liked
            env.result = {
                "table": table,
                "table_found": False,
                "unknown_columns": [],
                "insert_required": [],
                "primary_key": [],
                "checked_columns": [],
                "note": "таблицы нет ни в схеме из миграций, ни в собранной живой схеме",
            }
            return env.to_dict()

        table_name = str(trow["table_name"]) if trow is not None else table
        source_name = str(trow["source"]) if trow is not None else "live"

        cols: list[dict] = []
        # Наличие DEFAULT проверяется по тексту миграции: в db_column этого
        # признака нет. Запрос идёт по text_chunk того же файла — разбор миграций
        # уже хранит определения таблиц как текст.
        #
        # P1-4: если схема построена новым индексатором, признаки (уникальность,
        # AUTO_INCREMENT, генерируемая колонка, DEFAULT) лежат в самом `db_column`,
        # и читать файл миграции не нужно. Проверка наличия колонок обязательна:
        # у корпусов, построенных раньше, их нет.
        marks_available = self._db_column_marks_available()
        extra_cols = (
            ", is_unique, auto_increment, generated_kind, has_default"
            if marks_available else ""
        )
        if trow is not None:
            for r in self.graph.execute(
                "SELECT column_name, data_type, is_nullable, column_key, ordinal"
                + extra_cols
                + " FROM db_column WHERE rev_id = ? AND table_id = ? ORDER BY ordinal",
                (rev, str(trow["table_id"])),
            ).fetchall():
                rec = dict(r)
                kind = str(rec.get("generated_kind") or "")
                has_default = (
                    bool(rec.get("has_default"))
                    if marks_available
                    else _column_has_default(rev, table_name, str(r["column_name"]))
                )
                cols.append(
                    {
                        "name": str(r["column_name"]),
                        "type": r["data_type"],
                        "nullable": bool(r["is_nullable"]),
                        "key": r["column_key"],
                        "has_default": has_default,
                        "unique": bool(rec.get("is_unique")) or str(r["column_key"] or "").upper() == "UNI",
                        "auto_increment": bool(rec.get("auto_increment")),
                        "generated": bool(kind),
                        "generated_kind": kind,
                    }
                )
        else:
            # Таблица известна только по живой базе: колонки берутся из неё же,
            # с теми же признаками (`db_marks`), что и в `db_schema`. Признаки
            # уже разобраны в `db_ops.schema`, поэтому здесь только перенос в
            # общий формат — второй разбор строки `extra` был бы вторым
            # источником правды об одном и том же.
            from . import db_ops

            live_tables = db_ops.schema(self.graph, rev, table=table, limit=5)
            live_row = live_tables[0] if live_tables else None
            if live_row is None:
                env.found = False
                env.error = "таблица есть в живой схеме, но её колонки не собраны"
                return env.to_dict()
            table_name = str(live_row.get("table_name") or table)
            for c in live_row.get("columns", []):
                cols.append(
                    {
                        "name": str(c.get("column_name")),
                        "type": c.get("data_type"),
                        "nullable": bool(c.get("nullable")),
                        "key": c.get("column_key"),
                        "has_default": bool(str(c.get("default_value") or "").strip())
                        or bool(c.get("default_expression")),
                        "unique": bool(c.get("unique")),
                        "auto_increment": bool(c.get("auto_increment")),
                        "generated": bool(c.get("generated")),
                        "generated_kind": c.get("generated_kind") or "",
                    }
                )

        existing = {c["name"].lower(): c["name"] for c in cols}
        given = [str(c) for c in (columns or [])]

        # --- живая схема БД: генерируемые колонки, ключи и колонки вне миграций ---
        # Зачем. Миграции генерируемых колонок не описывают, а СУБД запрещает в них
        # писать: «The value specified for generated column 'String Nominal Voltage'»
        # (замер на BWO: два INSERT упали уже после написания кода). Живая схема в
        # индексе знает про GENERATED (db_live_column.extra), поэтому проверяем и её.
        live: dict[str, dict] = {}
        try:
            if (store.table_exists(self.graph, "db_live_column")
                    and store.table_exists(self.graph, "db_live_table")):
                rows = self.graph.execute(
                    "SELECT c.column_name, COALESCE(c.extra,'') AS extra,"
                    " COALESCE(c.column_key,'') AS key"
                    " FROM db_live_column c"
                    " JOIN db_live_table t ON t.rev_id = c.rev_id AND t.table_id = c.table_id"
                    " WHERE c.rev_id = ? AND lower(t.table_name) = lower(?)",
                    (rev, table_name),
                ).fetchall()
                for r in rows:
                    live[str(r["column_name"]).lower()] = {
                        "name": str(r["column_name"]),
                        "extra": str(r["extra"] or ""),
                        "key": str(r["key"] or ""),
                    }
        except Exception:  # noqa: BLE001 — живой схемы может не быть, это не отказ
            live = {}

        # Генерируемые колонки: ДВА источника, и оба обязательны.
        #   1. живая схема — СУБД прямо говорит `VIRTUAL/STORED GENERATED`;
        #   2. миграции — `GENERATED ALWAYS AS (...) VIRTUAL|STORED`.
        # Только живой схемы мало: если таблица есть в миграциях, но её нет в
        # подключённой базе (другой стенд), запрет не находился, и вставка
        # падала уже в СУБД — ровно тот дефект, ради которого проверка заведена.
        # Признак разбирается ЕДИНЫМ правилом (`db_marks`): прежняя подстрока
        # "GENERATED" ловила ещё и DEFAULT_GENERATED — измерено на живой базе:
        # 171 совпадение, из них 92 ложных.
        live_marks = {
            key: db_marks.marks(c["extra"], c["key"]) for key, c in live.items()
        }
        generated_from_live = {
            c["name"]: live_marks[key]["generated_kind"]
            for key, c in live.items() if live_marks[key]["generated"]
        }
        generated_from_migrations = {
            c["name"]: (c.get("generated_kind") or "virtual")
            for c in cols if c.get("generated")
        }
        generated_kinds = dict(generated_from_migrations)
        generated_kinds.update(generated_from_live)
        generated = sorted(generated_kinds)
        default_expression = sorted(
            c["name"] for key, c in live.items()
            if live_marks[key]["default_expression"]
        )
        generated_lower = {g.lower() for g in generated}
        generated_given = [g for g in given if g.lower() in generated_lower]
        live_only = sorted(
            c["name"] for c in live.values() if c["name"].lower() not in existing
        )
        live_keys = {
            "primary": sorted(c["name"] for c in live.values() if c["key"].upper() == "PRI"),
            "unique": sorted(c["name"] for c in live.values() if c["key"].upper() == "UNI"),
        }
        # Признаки колонок миграций: по ним отвечает и `schema`, и сообщение
        # «почему нельзя писать». Собираются только для колонок таблицы.
        columns_marks = {
            c["name"]: {
                "pk": str(c.get("key") or "").upper() == "PRI",
                "unique": bool(c.get("unique")),
                "auto_increment": bool(c.get("auto_increment")),
                "generated": bool(c.get("generated")),
                "generated_kind": c.get("generated_kind") or "",
                "has_default": bool(c.get("has_default")),
                "nullable": bool(c.get("nullable")),
            }
            for c in cols
        }
        values_mismatch = bool(values is not None and given and int(values) != len(given))
        unknown: list[dict] = []
        for name in given:
            if name.lower() not in existing:
                unknown.append({
                    "name": name,
                    "nearest": util.closest_names(name, list(existing.values()), limit=2),
                })

        # Обязательные при вставке: NOT NULL и без значения по умолчанию.
        # AUTO_INCREMENT в разборе миграций не выделяется отдельным признаком,
        # поэтому исключаются PRIMARY-колонки (их значение даёт СУБД) и колонки
        # с явным DEFAULT — иначе проверка выдавала бы ложные «обязательные»,
        # и агент перестал бы ей доверять. Замерено на DocPayments: без этого
        # правила в обязательные попадали ID и Deleted (у обоих есть DEFAULT).
        required = []
        for c in cols:
            if c["nullable"] or (c["key"] or "") == "PRI":
                continue
            if c.get("has_default"):
                continue
            # Генерируемую колонку нельзя заполнить, а AUTO_INCREMENT СУБД ведёт
            # сама: требовать их значения — значит выдавать ложное «обязательно».
            if c.get("generated") or c.get("auto_increment"):
                continue
            required.append(c["name"])
        required_missing = [
            n for n in required if n.lower() not in {g.lower() for g in given}
        ]
        primary = [c["name"] for c in cols if (c["key"] or "") == "PRI"]

        env.result = {
            "table": table_name,
            "table_found": True,
            "operation": operation,
            "checked_columns": given,
            "unknown_columns": unknown,
            "insert_required": required_missing if operation == "insert" else [],
            "all_required_columns": required,
            "primary_key": primary,
            "column_count": len(cols),
            # Источник схемы назван прямо: «миграции» и «живая база» — разные
            # степени доверия, и от них зависит, что означает пустой список
            # расхождений.
            "source": source_name,
            # Что добавляет проверка по живой схеме: в эти колонки писать нельзя,
            # эти есть в базе но не в миграциях, ключи для ON DUPLICATE KEY.
            "generated_columns": generated,
            "generated_kinds": generated_kinds,
            "generated_from_migrations": sorted(generated_from_migrations),
            "generated_from_live": sorted(generated_from_live),
            "generated_requested": generated_given,
            "live_only_columns": live_only,
            "keys_live": live_keys,
            "default_expression_columns": default_expression,
            "columns_marks": columns_marks,
            "values_count": values,
            "values_mismatch": values_mismatch,
        }
        # Результат считаем найденным только при отсутствии проблем: агент должен
        # видеть «проверка не пройдена» как отрицательный ответ, а не как данные.
        problems = bool(unknown) or bool(required_missing) or bool(generated_given)
        env.found = (problems or values_mismatch) if given else True
        # ВСЕ найденные проблемы перечисляются списком, а не одной первой:
        # замер на живой схеме `TestData` дал одновременно запись в генерируемую
        # колонку И несовпадение числа значений, а сообщение показывало только
        # первое — вторую ошибку агент увидел бы уже от СУБД.
        found_problems: list[str] = []
        if unknown:
            found_problems.append(
                "колонки отсутствуют в таблице: "
                + ", ".join(str(u["name"]) for u in unknown)
            )
        if generated_given:
            found_problems.append(
                "нельзя писать в генерируемые колонки (значение считает СУБД): "
                + ", ".join(generated_given)
            )
        if values_mismatch:
            found_problems.append(
                f"число значений ({values}) не равно числу колонок ({len(given)}): "
                f"СУБД ответит Column count doesn't match value count"
            )
        if required_missing and operation == "insert":
            found_problems.append(
                "обязательные колонки не заданы: " + ", ".join(required_missing)
            )
        env.result["problems"] = found_problems
        if found_problems:
            env.extra["problem"] = found_problems[0]
            if len(found_problems) > 1:
                env.extra["problems"] = found_problems
        return env.to_dict()

    def op_routes(self, path: str | None = None, limit: int = 200) -> dict:
        """12. Маршруты и точки входа: регистрации и ссылки разметки (P1-3).

        Реестр заполняется при индексации извлекателями PHP/HTML/JS. Вид
        точки входа назван полем `kind`: `route` — маршрут зарегистрирован
        в коде (`Route::post('/x')`), `endpoint` — файл упомянут как адрес
        (`action=`, `src=`, `fetch(...)`, серверное включение). Это разные
        степени доверия, и смешивать их в одном списке нельзя.
        """
        env = self.make_env("routes")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if not store.table_exists(self.graph, "web_route"):
            env.found = False
            env.error = "слой маршрутов не построен (этап M2)"
            return env.to_dict()
        params: list[Any] = [rev]
        sql = "SELECT route, method, node_id, evidence_path, evidence_line FROM web_route WHERE rev_id = ?"
        if path:
            sql += " AND route LIKE ?"
            params.append(f"%{path}%")
        sql += " LIMIT ?"
        params.append(limit)
        rows = self.graph.execute(sql, tuple(params)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["kind"] = "route" if str(item.get("method") or "").strip() else "endpoint"
            out.append(item)
        env.result = out
        env.found = bool(rows)
        env.extra["by_kind"] = _count_by(x["kind"] for x in out)
        if not rows:
            env.extra["suggest"] = ["entrypoints", "search_multi"]
            env.limits.append(
                "точек входа в реестре нет: корпус построен до появления разбора "
                "маршрутов — поможет переиндексация"
            )
        return env.to_dict()

    def op_entrypoints(self, path: str | None = None, scope: str | None = None,
                       limit: int = 200) -> dict:
        """12a. Точки входа и достижимость файлов (P1-2).

        Зачем операция. Вопрос «какие файлы реально работают, а какие лежат
        мёртвым грузом» на живых разработках задаётся первым: рядом с рабочим
        `import.pl` лежат `import1.pl`, `import2.pl`, `import_stdin.pl`, а
        рядом с `Files7.php` — шесть его копий. Раньше это выяснялось
        чтением листингов и JS вручную.

        Что считается точкой входа (виды разделены, у них разная сила):
          * `route`    — маршрут зарегистрирован в коде (`web_route`);
          * `http`     — файл читает `$_GET/$_POST/$_SERVER` (доказано ребром);
          * `endpoint` — путь файла упомянут в `action=`/`src=`/`href=`/`fetch(...)`
                         другого файла: ссылка разметки, а не вызов кода;
          * `cli`      — скрипт с shebang или из служебного каталога (`cgi-bin/`, `bin/`).

        При `path` ответ дополняется разбором ОДНОГО файла: точка ли он входа,
        достижим ли от точек входа, кто на него ссылается и есть ли ссылки
        в тексте (литеральные упоминания пути).
        """
        env = self.make_env("entrypoints")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        kn = analysis.knowledge(self.graph, self.lexvec.conn, rev, with_entrypoints=True,
                                root=self.cfg.root)

        items = []
        for p in sorted(kn.entrypoints):
            if scope and not (p == scope.rstrip("/") or p.startswith(scope.rstrip("/") + "/")):
                continue
            refs = kn.entrypoints[p]
            items.append({
                "path": p,
                "kinds": sorted({r.kind for r in refs}),
                "reachable": p in kn.reachable,
                "incoming_refs": len(kn.incoming(p)),
                "lang": kn.files.get(p).lang if p in kn.files else "",
                "evidence": [
                    {"path": r.path, "line": r.line, "kind": r.kind, "text": r.text}
                    for r in refs[:5]
                ],
            })
        result: dict = {
            "scope": scope or "весь корпус",
            "entrypoints": items[:limit],
            "entrypoints_total": len(items),
            "files_total": len(kn.files),
            "reachable_total": len(kn.reachable),
            "by_kind": _count_by(r.kind for refs in kn.entrypoints.values() for r in refs),
            "notes": kn.notes,
        }

        target = (path or "").strip()
        if target:
            norm = util.to_posix(target).strip("/")
            if norm not in kn.files:
                result["target"] = {"path": norm, "in_files": False,
                                    "reason": "файла нет в индексе ревизии"}
            else:
                refs = kn.incoming(norm)
                literal = analysis.literal_refs(
                    self.lexvec.conn, self.graph, rev, [norm]
                ).get(norm, [])
                result["target"] = {
                    "path": norm,
                    "in_files": True,
                    "is_entrypoint": kn.is_entrypoint(norm),
                    "entrypoint_kinds": sorted({r.kind for r in kn.entrypoints.get(norm, [])}),
                    "reachable": norm in kn.reachable,
                    "incoming_refs": len(refs),
                    "referenced_by_code": [
                        {"path": r.path, "line": r.line, "kind": r.kind, "text": r.text}
                        for r in refs[:20]
                    ],
                    "referenced_by_text": [
                        {"path": r.path, "line": r.line, "kind": r.kind, "text": r.text}
                        for r in literal[:20]
                    ],
                    "verdict": _activity_verdict(kn, norm),
                }
        env.result = result
        env.found = bool(items) or bool(result.get("target", {}).get("in_files"))
        if not env.found:
            env.extra["suggest"] = ["repo_map", "duplicates", "dead_code"]
        self.attach_stale(env, list(kn.entrypoints)[:200])
        return env.to_dict()

    def op_duplicates(self, scope: str | None = None, limit: int = 100) -> dict:
        """12b. Копии файлов: группы «одно имя с номерами» и байт-идентичные (P1-2).

        Зачем операция. `Files.php` … `Files7.php` и `import.pl` /
        `import1.pl` / `import2.pl` — это копии, разошедшиеся правками.
        Штатное схлопывание дублей (`dedupe.py`) их не видит: оно сравнивает
        sha256 и служит контрольной сумме корпуса, а не ответу человеку.
        Здесь группы строятся по ИМЕНИ и подтверждаются сходством текста,
        а главный файл выбирается по фактам: точка входа → достижимость →
        число входящих ссылок → путь.

        Поле `evidence` у группы — это и есть доказательство активности:
        ссылки кода (`include`) и упоминания пути в разметке/скриптах
        (`BWO_FILES_ENDPOINT = 'php/Wizard/Files7.php'`) с адресом `path:line`.
        """
        env = self.make_env("duplicates")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        data = analysis.duplicates_report(self.graph, self.lexvec.conn, rev,
                                          scope=scope, limit=limit, root=self.cfg.root)
        env.result = data
        env.found = bool(data["groups"])
        if not env.found:
            env.extra["suggest"] = ["search", "repo_map"]
            env.limits.append(
                "групп копий не найдено: либо копий нет, либо фильтр scope их отсёк"
            )
        env.extra["groups_total"] = data["groups_total"]
        env.extra["files_checked"] = data["files_checked"]
        self.attach_stale(env, [g["primary"] for g in data["groups"]])
        return env.to_dict()

    def op_dead_code(self, scope: str | None = None, limit: int = 200) -> dict:
        """12c. Файлы и подпрограммы без ссылок и вне достижимости (P1-2).

        Приговор выносится только при совпадении ДВУХ независимых проверок:
        нет входящих рёбер в графе И нет литерального упоминания пути в
        тексте других файлов. Файлы, которые сами являются точками входа
        (скрипт с shebang), мёртвыми не объявляются: это рабочий сценарий
        запуска, у которого входящих ссылок и не должно быть.

        В ответе есть `refuted` — кандидаты, оправданные найденной ссылкой.
        Это не мусор: именно он показывает, что проверка не выдумывает
        вердикт, а снимает его по факту.
        """
        env = self.make_env("dead_code")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        data = analysis.dead_code_report(self.graph, self.lexvec.conn, rev,
                                         scope=scope, limit=limit, root=self.cfg.root)
        env.result = data
        env.found = bool(data["files"]) or bool(data["symbols"])
        env.extra["candidates"] = data["files_candidates"]
        env.extra["checked"] = data["files_checked"]
        if data["files_not_checked"]:
            env.limits.append(
                f"проверено {data['files_checked']} из {data['files_candidates']} кандидатов: "
                "остальные не осмотрены (увеличьте limit или сузьте scope)"
            )
        if not env.found:
            env.extra["suggest"] = ["entrypoints", "duplicates"]
            env.extra["note"] = (
                "мёртвого кода не найдено: у всех файлов есть входящие ссылки "
                "либо они достижимы из точек входа"
            )
        self.attach_stale(env, [f["path"] for f in data["files"]][:200])
        return env.to_dict()

    def op_drift_ack(self, entry_ids: list[int], note: str | None = None, by_whom: str = "agent") -> dict:
        """15. Признание дельт: дописывание ack, БЕЗ удаления записи (ФТ-36)."""
        env = self.make_env("drift_ack")
        done = []
        for eid in entry_ids:
            row = self.control.conn.execute(
                "SELECT id FROM drift_entry WHERE id = ?", (eid,)
            ).fetchone()
            if row is None:
                continue
            payload = util.dumps({"by": by_whom, "ts": util.now_iso(), "note": note})
            self.control.conn.execute(
                "UPDATE drift_entry SET ack = ? WHERE id = ?", (payload, eid)
            )
            self.control.conn.execute(
                "INSERT INTO ack(drift_entry_id, by_whom, ts, note) VALUES(?, ?, ?, ?)",
                (eid, by_whom, util.now_iso(), note),
            )
            done.append(eid)
        self.control.conn.commit()
        env.result = {"acked": done, "requested": entry_ids}
        env.extra["note"] = "записи журнала не удаляются: ack только дописывается"
        return env.to_dict()

    def op_calibrate(self, kind: str = "confidence", samples: dict | None = None) -> dict:
        """17. Калибровка порогов (ФТ-11 ТЗ)."""
        env = self.make_env("calibrate")
        if kind == "confidence":
            params = samples or self.cfg.raw.get("confidence", {})
            note = "стартовые значения из архитектуры §5.2"
        else:
            params = samples or {}
            note = "пользовательская калибровка"
        n = int((samples or {}).get("sample_size", 0)) if isinstance(samples, dict) else 0
        self.control.conn.execute(
            "INSERT INTO calibration(created_at, kind, params, sample_size, note) VALUES(?,?,?,?,?)",
            (util.now_iso(), kind, util.dumps(params), n, note),
        )
        self.control.conn.commit()
        env.result = {"kind": kind, "params": params, "sample_size": n, "note": note}
        env.limits.append(
            "пороги требуют калибровки на ~20 размеченных примерах: в литературе разброс в 3 раза"
        )
        return env.to_dict()

    # ---------------- внутреннее ----------------

    def _resolve_entry_ids(self, rev: str, raws: list[str]) -> tuple[
        list[tuple[str, int, int, str | None]], list[str], int, int
    ]:
        """Разрешить идентификаторы хита поиска (node_id и/или chunk_id) в спаны.

        Зачем. Хит `search` отдаёт `node_id` и `chunk_id`, но ДО этой правки их
        нельзя было передать никуда: `code --entry-ids <node_id>` отвечал
        «нужен параметр fqn или path», и агент шёл искать файл заново — то есть
        двухшаговая схема была двухшаговой только формально.

        Порядок разрешения и его границы:
        * сначала `node_id` — по таблице узлов (даёт точный путь и границы);
        * затем `chunk_id` — по таблице чанков (есть только при `source_kind`
          символ/файл; у чанков без узла путь есть, а имени нет);
        * что не разрешилось — возвращается списком, а не выбрасывается молча:
          агент должен видеть, что именно не найдено.
        """
        spans: list[tuple[str, int, int, str | None]] = []
        unresolved: list[str] = []
        via_node = 0
        via_chunk = 0
        seen: set[tuple[str, int, int]] = set()
        for raw in raws:
            ident = str(raw).strip()
            if not ident:
                continue
            # 1) node_id из графа (даёт точный путь, границы и имя)
            row = self.graph.execute(
                """SELECT path, start_line, end_line, COALESCE(fqn, name) AS label
                   FROM node WHERE rev_id = ? AND node_id = ?""",
                (rev, ident),
            ).fetchone()
            if row is not None and row["path"] is not None:
                key = (str(row["path"]), int(row["start_line"]), int(row["end_line"]))
                if key not in seen:
                    seen.add(key)
                    spans.append((key[0], key[1], key[2], row["label"]))
                via_node += 1
                continue
            # 2) chunk_id из хранилища чанков. Колонки chunk_id НЕТ в таблице node
            # (DDL store.py:122-131: rev_id, node_id, kind, name, fqn, path, ...),
            # а прежняя редакция делала по ней запрос к графу — сервер отвечал
            # OperationalError «no such column», то есть адресация по chunk_id
            # падала вместо разрешения. Такой идентификатор ищется там, где он
            # действительно есть: в chunk_map и chunk_text.
            cm = self.lexvec.conn.execute(
                """SELECT path, start_line, end_line FROM chunk_map
                   WHERE rev_id = ? AND chunk_id = ?""",
                (rev, ident),
            ).fetchone()
            if cm is not None and cm["path"] is not None:
                cstart = int(cm["start_line"] or 1)
                cend = int(cm["end_line"] or 0)
                if cend >= cstart:
                    key = (str(cm["path"]), cstart, cend)
                    if key not in seen:
                        seen.add(key)
                        spans.append((key[0], key[1], key[2], None))
                via_chunk += 1
                continue
            # 3) node_id, но без пути (например внешний узел) — не адресуемо
            unresolved.append(ident)
        return spans, unresolved, via_node, via_chunk

    def _no_index(self, env: Envelope) -> dict:
        env.found = False
        env.error = "индекс не создан: выполните ragraf index --project <config>"
        return env.to_dict()

    def _coverage(self, rev: str) -> dict:
        rows = self.graph.execute(
            "SELECT metric, value, note FROM coverage WHERE rev_id = ?", (rev,)
        ).fetchall()
        return {str(r["metric"]): r["value"] for r in rows}

    def _table_names(self, rev: str) -> set[str]:
        if not store.table_exists(self.graph, "db_table"):
            return set()
        try:
            rows = self.graph.execute(
                "SELECT table_name FROM db_table WHERE rev_id = ?", (rev,)
            ).fetchall()
            return {str(r["table_name"]).lower() for r in rows}
        except sqlite3.Error:
            return set()

    def _symbol_rows(self, rev: str, name: str, path: str | None, limit: int) -> list[dict]:
        """Поиск символа: сначала точное fqn, затем по имени, затем по вхождению.

        ПОРЯДОК ВАЖЕН, и это измеренный дефект. У имени может быть ДВА узла:
        реальное определение (`path` заполнен) и внешняя заглушка
        (`kind='external'`, `path` пуст) — её создаёт разрешение вызовов для
        целей вне корпуса. Сортировка по `COALESCE(path,'')` ставила заглушку
        ПЕРВОЙ (пустая строка меньше любого пути), а `callers`, `callees`,
        `impact` и `slice` берут `rows[0]`: замер 14.10.2026 — `callers` для
        `cross_check` отвечал «связей 0», потому что работал по заглушке
        `python:cross_check`, у которой рёбер нет вовсе. Теперь реальные
        определения идут первыми, заглушки — последними.
        """
        order = ("ORDER BY CASE WHEN kind = 'external' OR path IS NULL OR path = ''"
                 " THEN 1 ELSE 0 END, COALESCE(path, ''), start_line")
        exact = self.graph.execute(
            """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
               FROM node WHERE rev_id = ? AND (fqn = ? OR (name = ? AND kind <> 'file'))
               """ + order + " LIMIT ?",
            (rev, name, name, limit),
        ).fetchall()
        if not exact and "::" in name:
            cls, meth = name.split("::", 1)
            exact = self.graph.execute(
                """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
                   FROM node WHERE rev_id = ? AND fqn LIKE ? AND name = ?
                   """ + order + " LIMIT ?",
                (rev, f"%{cls}%", meth, limit),
            ).fetchall()
        # Поиск по имени класса без метода: "Config" → сам класс
        if not exact and "::" not in name:
            exact = self.graph.execute(
                """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
                   FROM node WHERE rev_id = ? AND name = ?
                     AND kind IN ('class','interface','trait','enum','property','constant')
                   """ + order + " LIMIT ?",
                (rev, name, limit),
            ).fetchall()
        if not exact and path:
            exact = self.graph.execute(
                """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
                   FROM node WHERE rev_id = ? AND path = ? AND name = ?
                   """ + order + " LIMIT ?",
                (rev, util.to_posix(path), name, limit),
            ).fetchall()
        if not exact:
            exact = self.graph.execute(
                """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
                   FROM node WHERE rev_id = ? AND fqn LIKE ?
                   ORDER BY CASE WHEN kind = 'external' OR path IS NULL OR path = ''
                   THEN 1 ELSE 0 END, LENGTH(COALESCE(fqn, '')) LIMIT ?""",
                (rev, f"%{name}%", limit),
            ).fetchall()
        out = []
        for r in exact:
            item = dict(r)
            try:
                item["props"] = util.loads(item["props"]) if item["props"] else {}
            except Exception:
                item["props"] = {}
            # U3: машинный список параметров поднимается из props на верхний уровень
            # ответа: агенту нужны типы и порядок аргументов, а не вложенность.
            params = (item["props"] or {}).get("signature_params")
            if params is not None:
                item["signature_params"] = params
                item["signature_truncated"] = bool(
                    (item["props"] or {}).get("signature_truncated")
                )
            elif item.get("kind") in ("function", "method"):
                # Функция без параметров и без признака обрезки — это норма,
                # но об этом надо сказать явно, чтобы отсутствие поля не читалось
                # как «индекс не смог разобрать».
                item["signature_params"] = []
                item["signature_truncated"] = False
            out.append(item)
        return out

    def _graph_walk(self, fqn: str, depth: int, hard_only: bool, op: str, limit: int,
                    answer: bool = False) -> dict:
        env = self.make_env(op)
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = self._symbol_rows(rev, fqn, None, 3)
        if not rows:
            env.found = False
            env.error = f"символ не найден: {fqn}"
            env.extra["suggest"] = ["symbol", "search"]
            return env.to_dict()
        reverse = op == "callers"
        steps = self._bfs(rev, [rows[0]["node_id"]], max(1, depth), reverse=reverse, limit=limit)
        # первый уровень — сама цель, исключаем её из результата
        steps = [s for s in steps if s["node_id"] != rows[0]["node_id"]]
        # Порог достоверности применяется ВТОРОЙ раз — на выходе операции, а не
        # только в SQL обхода. Это защита от того, что порог останется в одной
        # строке SQL и разойдётся между операциями; правило берётся из одного
        # места (_edge_passes_floor), поэтому расхождения быть не может.
        steps = [
            s for s in steps
            if _edge_passes_floor(s.get("provenance"), s.get("confidence"))
        ]
        if hard_only:
            steps = [s for s in steps if s["provenance"] == "stated"]
        below_floor = self._count_below_floor(rev, rows[0]["node_id"], reverse)
        # Вызовы по имени, не привязанные к определению: только для `callers`
        # (вопрос «кто меня зовёт» — единственный, где это меняет ответ).
        name_links = (
            self._unresolved_name_links(rev, str(rows[0].get("name") or ""))
            if reverse else []
        )
        env.result = {
            "target": rows[0]["fqn"],
            "direction": "кто вызывает" if reverse else "кого вызывает",
            "depth": depth,
            "items": steps,
            "count": len(steps),
            "stated": sum(1 for s in steps if s["provenance"] == "stated"),
            "derived": sum(1 for s in steps if s["provenance"] == "derived"),
            # Порог назван в ответе: агент, не увидевший знакомого ребра, обязан
            # иметь возможность отличить «связи нет» от «связь отсечена порогом».
            "confidence_floor": MIN_EDGE_CONFIDENCE,
            # Сколько связей отсечено ПОРОГОМ: без этого числа «связей 0»
            # читается как «никто не вызывает», хотя связь в графе есть, но
            # помечена как вывод (например `calls_heuristic` 0.4). Измерено
            # 14.10.2026: `callers cross_check` отвечал «связей 0» при живой
            # связи `op_db_cross → cross_check` достоверности 0.4.
            "below_confidence_floor": below_floor,
            # Вызовы, адресованные ИМЕНИ, но не привязанные к ЭТОМУ определению
            # (межфайловое разрешение по имени намеренно не делается).
            "name_links": name_links,
            "name_links_count": len(name_links),
        }
        env.found = bool(steps)
        if name_links:
            env.limits.append(
                f"вызовов по имени без доказанной привязки к этому определению: "
                f"{len(name_links)} — они в списке `name_links` с достоверностью "
                f"0.4; пустой `count` означает «связь не доказана», а не "
                f"«вызовов нет»"
            )
        if not env.found:
            env.extra["suggest"] = ["search", "live_read"]
            env.limits.append("связи не найдены: возможен динамический вызов вне статического анализа")
            if below_floor:
                env.limits.append(
                    f"ниже порога достоверности {MIN_EDGE_CONFIDENCE} отсечено связей: "
                    f"{below_floor} — они в графе есть, но помечены как вывод, а не факт"
                )
        self.attach_stale(env, [s["path"] for s in steps if s.get("path")])
        if answer:
            verb = "Вызывают" if reverse else "Вызывает"
            items = [
                {"path": s.get("path"), "line": s.get("start_line"),
                 "name": f"{s.get('name')} ({s.get('provenance')})"}
                for s in steps
            ]
            notes = [f"порог достоверности связей: {MIN_EDGE_CONFIDENCE}"]
            if name_links:
                for link in name_links[:3]:
                    items.append({"path": link["path"], "line": link["line"],
                                  "name": f"по имени (не доказано): {link['caller']}"})
                notes.append(
                    f"вызовов по имени без доказанной привязки: {len(name_links)} — "
                    f"они показаны как «по имени (не доказано)», достоверность 0.4"
                )
            if below_floor:
                notes.append(
                    f"ниже порога отсечено связей: {below_floor} — в графе они есть, "
                    f"но помечены как вывод, а не факт; смотрите результат без "
                    f"`answer=true`, поле `below_confidence_floor`"
                )
            self._answer_mode(
                env, f"{verb} `{rows[0]['fqn']}`: связей {len(steps)}", items,
                notes=notes,
            )
        return env.to_dict()

    def _unresolved_name_links(self, rev: str, name: str, *, limit: int = 20) -> list[dict]:
        """Вызовы, адресованные ИМЕНИ символа и не привязанные к его определению.

        Зачем отдельный список. Разрешение внешних имён привязывает вызов к
        определению только при совпадении имени И файла: замер журнала
        индексации собственного корпуса — «по имени и файлу 1458, по имени без
        файла 0, не разрешено 14 365». Значит вызов из ДРУГОГО файла остаётся
        заглушкой `@ext:` с именем символа, у которой `path` пуст, и `callers`
        отвечает «связей 0» — хотя вызов в коде ЕСТЬ.

        Выдумывать связь из этого нельзя (по имени без файла легко попасть в
        одноимённую функцию другого класса — ловушка 11), поэтому такие вызовы
        отдаются ОТДЕЛЬНЫМ списком с достоверностью вывода и названной границей:
        «вызов по имени есть, привязка к определению не доказана». Агент видит
        и число, и место, и решает сам.
        """
        try:
            rows = self.graph.execute(
                """SELECT e.src, e.evidence_path, e.evidence_line,
                          caller.fqn AS caller_fqn, caller.path AS caller_path,
                          caller.start_line AS caller_line
                     FROM node ext
                     JOIN edge e ON e.rev_id = ext.rev_id AND e.dst = ext.node_id
                     LEFT JOIN node caller
                       ON caller.rev_id = e.rev_id AND caller.node_id = e.src
                    WHERE ext.rev_id = ? AND ext.kind = 'external' AND ext.name = ?
                      AND e.kind LIKE 'calls%'
                    ORDER BY e.evidence_path, e.evidence_line LIMIT ?""",
                (rev, name, limit),
            ).fetchall()
        except Exception:
            return []
        out: list[dict] = []
        seen: set[tuple] = set()
        for r in rows:
            key = (str(r["evidence_path"]), int(r["evidence_line"] or 0))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "caller": str(r["caller_fqn"] or r["src"]),
                "path": str(r["caller_path"] or r["evidence_path"] or ""),
                "line": int(r["caller_line"] or r["evidence_line"] or 0),
                "evidence": f"{r['evidence_path']}:{r['evidence_line']}",
                "provenance": "derived",
                "confidence": 0.4,
                "why": ("вызов назвал это имя, но привязка к определению не "
                        "доказана: разрешение идёт по имени И файлу"),
            })
        return out

    def _count_below_floor(self, rev: str, node_id: str, reverse: bool) -> int:
        """Сколько связей узла отсечено ПОРОГОМ достоверности.

        Зачем отдельный запрос: обход (`_bfs`) отсекает слабые рёбра внутри SQL,
        поэтому в результате их уже нет и посчитать «сколько потеряно» по нему
        нельзя. Без этого числа ответ «связей 0» читается как «никто не
        вызывает», хотя связь в графе есть — она лишь помечена как вывод.
        Измерено 14.10.2026: `callers cross_check` отвечал «связей 0» при живой
        связи `op_db_cross → cross_check` достоверности 0.4.
        """
        column = "dst" if reverse else "src"
        neighbor = "src" if reverse else "dst"
        try:
            row = self.graph.execute(
                f"SELECT COUNT(DISTINCT {neighbor}) AS n FROM edge"
                f" WHERE rev_id = ? AND {column} = ?"
                f" AND NOT (provenance = 'stated' OR COALESCE(confidence, 0) >= ?)",
                (rev, node_id, MIN_EDGE_CONFIDENCE),
            ).fetchone()
            return int(row["n"] or 0) if row else 0
        except Exception:
            return 0

    def _bfs(self, rev: str, roots: list[str], depth: int, reverse: bool, limit: int) -> list[dict]:
        """Обход графа в ширину. Каждый шаг несёт доказательство и достоверность.

        Рёбра слабее `MIN_EDGE_CONFIDENCE` в обход не берутся (доказанные — берутся
        всегда). Обход один на callers, callees, impact, slice, references и
        policy_trace, поэтому порог действует одинаково во всех этих операциях.
        """
        seen: dict[str, dict] = {}
        frontier = list(roots)
        root_set = set(roots)
        # Направление обхода:
        #   reverse=True  — ищем ВХОДЯЩИЕ связи (кто вызывает / откуда приходят данные);
        #                   фильтр по dst, сосед = src.
        #   reverse=False — ищем ИСХОДЯЩИЕ связи (кого вызывает); фильтр по src, сосед = dst.
        # Раньше было наоборот: callers и impact возвращали вызываемых вместо вызывающих.
        rel = "dst" if reverse else "src"          # колонка, по которой ищем известный узел
        join = "src" if reverse else "dst"         # колонка, откуда берём соседа
        for level in range(depth):
            if not frontier:
                break
            next_frontier: list[str] = []
            CH = 200
            for i in range(0, len(frontier), CH):
                part = frontier[i : i + CH]
                marks = ",".join("?" for _ in part)
                # Порог достоверности отсекается ЗДЕСЬ, в самом обходе, а не в
                # оформлении ответа: ребро, не прошедшее порог, не должно даже
                # попадать в `seen`, иначе оно вытеснит более достоверного соседа
                # по лимиту `limit` и останется в ответе как «связь не найдена».
                # Доказанные рёбра не отсекаются — см. MIN_EDGE_CONFIDENCE.
                sql = f"""SELECT src, dst, kind, provenance, evidence_path, evidence_line, confidence
                          FROM edge WHERE rev_id = ? AND {rel} IN ({marks})
                          AND kind IN ('calls','calls_heuristic','instantiates','extends',
                                       'implements','overrides','queries','imports',
                                       'imports_heuristic','handles','renders','tested_by',
                                       'documented_by')
                          AND (provenance = 'stated' OR confidence >= ?)"""
                rows = self.graph.execute(sql, (rev, *part, MIN_EDGE_CONFIDENCE)).fetchall()
                for r in rows:
                    other = str(r[join])
                    if other in root_set or other in seen:
                        continue
                    node = self.graph.execute(
                        """SELECT node_id, kind, name, fqn, path, start_line, end_line
                           FROM node WHERE rev_id = ? AND node_id = ?""",
                        (rev, other),
                    ).fetchone()
                    item = {
                        "node_id": other,
                        "level": level + 1,
                        "edge_kind": r["kind"],
                        "provenance": r["provenance"],
                        "confidence": r["confidence"],
                        "evidence": f"{r['evidence_path']}:{r['evidence_line']}",
                        "via": str(r["dst"]) if reverse else str(r["src"]),
                        # B5: связь не доказана, если происхождение не `stated`,
                        # и не доказана тем более, если цель — заглушка внешней
                        # цели, которой в корпусе нет. Поле отдаётся ВСЕГДА, а не
                        # только при `true`: отсутствие поля читается как «связь
                        # проверена», и это была бы та же ошибка, ради которой
                        # поле добавлено.
                        "ambiguous": (
                            str(r["provenance"]) != "stated"
                            or (bool(node) and str(node["kind"]) == "external")
                        ),
                    }
                    if node:
                        item.update(
                            {
                                "kind": node["kind"],
                                "name": node["name"],
                                "fqn": node["fqn"],
                            }
                        )
                        # B5: заглушка внешней цели не объявлена НИ В ОДНОМ файле,
                        # но в индексах, построенных до правки indexer.py, ей
                        # проставлен путь ПОСЛЕДНЕГО файла, который её упомянул
                        # (ключ node — (rev_id, node_id), запись INSERT OR REPLACE).
                        # Поэтому у `external` путь и строки не отдаются вовсе:
                        # пустой ответ честнее чужого файла. Правка работает и на
                        # уже построенном индексе — переиндексация не нужна.
                        if str(node["kind"]) != "external":
                            item["path"] = node["path"]
                            item["start_line"] = node["start_line"]
                            item["end_line"] = node["end_line"]
                    else:
                        item["kind"] = "external"
                        item["fqn"] = other
                    seen[other] = item
                    next_frontier.append(other)
                    if len(seen) >= limit:
                        return list(seen.values())
            frontier = next_frontier
        return list(seen.values())

    def _impact_table(self, env: Envelope, rev: str, table: str, mode: str) -> dict:
        dst = f"@table:{table}"
        rows = self.graph.execute(
            """SELECT src, kind, provenance, evidence_path, evidence_line FROM edge
               WHERE rev_id = ? AND dst = ?""",
            (rev, dst),
        ).fetchall()
        roots = [str(r["src"]) for r in rows]
        steps = self._bfs(rev, roots, 2, reverse=True, limit=300) if roots else []
        stated = [s for s in steps if s["provenance"] == "stated"]
        derived = [s for s in steps if s["provenance"] == "derived"]
        direct = [
            {
                "symbol": str(r["src"]),
                "kind": r["kind"],
                "provenance": r["provenance"],
                "evidence": f"{r['evidence_path']}:{r['evidence_line']}",
            }
            for r in rows
        ]
        env.result = {
            "target": f"table:{table}",
            "mode": mode,
            "direct_readers_writers": direct,
            "indirect": stated + ([] if mode == "hard" else derived),
            "affected_count": {"direct": len(direct), "indirect": len(steps)},
        }
        env.found = bool(direct or steps)
        self.attach_stale(env, [d["evidence"].split(":")[0] for d in direct if d.get("evidence")])
        return env.to_dict()

    def close(self) -> None:
        self.graph.close()
        self.lexvec.close()
        self.control.close()